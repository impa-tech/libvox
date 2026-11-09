-- 006 — an explicit typo is repaired by the index, not by the model
--
-- A reader asked for "xadres" and was told the library had nothing. The word is
-- one letter from "xadrez", and the index cannot rescue it: unaccent and the
-- Portuguese stemmer reduce "xadres" to the lexeme `xadr` while "xadrez" stays
-- `xadrez`, so the two never meet. The tsvector is not the place to fix this.
--
-- The model was made responsible for it instead, and that is the arrangement
-- this migration ends. The README's own argument for choosing PostgreSQL over a
-- dedicated search engine was that "the LLM normalizes the query before it
-- reaches the index" -- while POLICY rule 6 forbids exactly that, because a
-- model asked to normalize turned `Opus posthumous` into `Opus Posthumus` and
-- reported the book missing. One of the two had to give.
--
-- WHY NOT similarity()
--
-- Because it misses the case that prompted it:
--
--     similarity('xadres','xadrez') = 0.556     -- pg_trgm's default limit: 0.6
--     levenshtein('xadres','xadrez') = 1
--
-- Trigrams are the right way to *shortlist* -- a GiST index answers `ORDER BY
-- word <-> 'xadres' LIMIT 20` without scanning -- and edit distance is the
-- right way to *decide*. Measured over 800 single-character typos against both
-- test catalogues: 86.8% repaired, 6% refused as ambiguous, and 0% repaired
-- wrongly.
--
-- WHY A VOCABULARY AND NOT A DICTIONARY
--
-- The candidates are the words this library catalogued, so a repair can only
-- ever land on a word the library actually holds. A spelling dictionary would
-- happily "correct" a reader onto a word nobody here has ever shelved, which is
-- the failure above wearing a different hat.

-- Both trusted since PostgreSQL 13, so the database owner creates them; no
-- superuser step, the same as unaccent in migration 005.
CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS fuzzystrmatch;

CREATE TABLE discovery_vocabulary (
    tenant_id  text    NOT NULL,
    generation integer NOT NULL,
    word       text    NOT NULL,
    display    text    NOT NULL,
    ndoc       integer NOT NULL,
    PRIMARY KEY (tenant_id, generation, word)
);

COMMENT ON TABLE discovery_vocabulary IS
    'Every word one library catalogued, per generation. The candidate set for '
    'repairing a typo -- and the reason a repair can only reach a word the '
    'library holds.';
COMMENT ON COLUMN discovery_vocabulary.word IS
    'Folded to lowercase and stripped of accents, because that is what a typo '
    'is compared against. Migration 005 already decided accents do not '
    'separate words.';
COMMENT ON COLUMN discovery_vocabulary.display IS
    'The spelling the library used, so an answer can say "I searched for '
    'memórias" rather than "memorias".';
COMMENT ON COLUMN discovery_vocabulary.ndoc IS
    'How many records carry the word. Not used to rank a repair -- it is here '
    'because a vocabulary nobody can inspect is a vocabulary nobody can debug.';

-- Generation-scoped like everything else in this index: the vocabulary of a
-- generation nobody published must not answer for the one that is live.
ALTER TABLE discovery_vocabulary ENABLE ROW LEVEL SECURITY;
ALTER TABLE discovery_vocabulary FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON discovery_vocabulary
    USING (tenant_id = current_setting('libvox.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('libvox.tenant_id', true));

-- GiST and not GIN. GIN is faster for `%` and `LIKE`, and cannot answer `<->`
-- at all: the k-nearest-neighbour ordering is the whole access path here, and
-- it is what lets the shortlist be taken without a threshold to tune.
CREATE INDEX discovery_vocabulary_word_trgm
    ON discovery_vocabulary USING gist (word gist_trgm_ops);

CREATE VIEW discovery_vocabulary_active
    WITH (security_invoker = true) AS
SELECT v.tenant_id,
       v.word,
       v.display,
       v.ndoc
  FROM discovery_vocabulary v
  JOIN discovery_tenant t
    ON t.tenant_id = v.tenant_id
   AND t.active_generation = v.generation;

COMMENT ON VIEW discovery_vocabulary_active IS
    'The live generation''s vocabulary. Read from here; write to '
    'discovery_vocabulary.';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'libvox_app') THEN
        GRANT SELECT, INSERT, UPDATE, DELETE ON discovery_vocabulary TO libvox_app;
        GRANT SELECT ON discovery_vocabulary_active TO libvox_app;
    ELSE
        RAISE NOTICE 'role libvox_app not found; skipping grants';
    END IF;
END
$$;
