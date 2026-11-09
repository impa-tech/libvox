-- 008 — the index can be asked by meaning, not only by word
--
-- A reader asked "tem algo sobre matemática?" against a collection catalogued
-- in English and was told the library had nothing. It has five books on the
-- subject. The next turn asked "mathematics" and found all five.
--
--     "matemática"   → 0 records
--     "mathematics"  → 5 records
--     "math"         → 2: Math curse … and The hundred penny box
--
-- No lexical index crosses a language. That is the limit of the technique, not
-- a setting nobody got round to: `matemática` and `mathematics` share no stem
-- in any configuration, and `math` and `mathemat` are two words even inside
-- English. The third line is a second defect on top -- `Mathis, Sharon Bell.`
-- tokenizes as `math` because the *Portuguese* stemmer eats the `-is`, which is
-- how a book about a box of pennies answered a question about mathematics.
--
-- WHAT AN EMBEDDING DOES THAT A TSVECTOR CANNOT
--
-- Measured on those exact titles with qwen3-embedding:4b, truncated to 1024:
--
--     "matemática"                        "math"
--     0.706  Communication in mathematics  0.700  Communication in mathematics
--     0.658  Mathematics assessment        0.611  Mathematics teacher resource…
--     0.639  Mathematics teacher resource  0.411  Math curse
--     0.338  The hundred penny box         0.306  The hundred penny box
--
-- A question in Portuguese reaching an English shelf, and the false positive
-- from the lexical arm sinking to last place in both.
--
-- WHY THE LEXICAL INDEX STAYS
--
-- Because the two fail in different places. From the same measurement:
--
--     "friendships"
--     0.541  Communication in mathematics, K-12       ← wrong
--     0.503  Pedro and me: friendship, loss…
--
-- A bare inflected word embeds poorly, and the vector puts a mathematics book
-- above the book about friendship -- the case a stemmer gets right without
-- thinking. So both arms run and the results are fused; neither replaces the
-- other.
--
-- WHY 1024 AND NOT 2560
--
-- The model returns 2560 dimensions and pgvector indexes at most 2000. Qwen3's
-- embeddings are Matryoshka, so a prefix is a valid smaller embedding: measured
-- on the titles above, the ordering at 1024 is *identical* to the full vector,
-- and only at 512 do two irrelevant entries swap. 1024 also halves the storage
-- -- 36 MB for a collection of 8,774 records.

-- Not trusted like unaccent and pg_trgm: `vector` is a C extension, which is
-- why the image had to change. The guard names that, because the failure
-- otherwise arrives as a bare "could not open extension control file".
DO $$
BEGIN
    CREATE EXTENSION IF NOT EXISTS vector;
EXCEPTION WHEN OTHERS THEN
    RAISE EXCEPTION
        'the `vector` extension is not available on this server. /libvox-db '
        'must run pgvector/pgvector:pg17; the stock postgres image does not '
        'carry it. (%)', SQLERRM;
END
$$;

-- Nullable on purpose, and it is what lets every failure degrade instead of
-- abort. A record whose embedding could not be computed -- the gateway was
-- down, the import ran with no embedding configured at all -- is still found by
-- the lexical arm, still answered about, still correct. Only its reach by
-- meaning is missing, and that is a smaller thing to lose than the record.
ALTER TABLE discovery_biblio ADD COLUMN embedding vector(1024);

COMMENT ON COLUMN discovery_biblio.embedding IS
    'The record''s title, author and series as one vector. NULL when no '
    'embedding service was reachable at import; the lexical index answers '
    'alone for those rows.';

-- Cosine, because the vectors are normalized and cosine is what the similarity
-- floor in `search.py` is expressed in. HNSW rather than IVFFlat: it needs no
-- training pass over the data, which matters when every import publishes a new
-- generation and there is nothing to train on until the rows are already there.
CREATE INDEX discovery_biblio_embedding_idx
    ON discovery_biblio USING hnsw (embedding vector_cosine_ops);

-- The view is generated from the table's columns, so it has to be rebuilt to
-- carry the new one -- the same sequence migrations 004 and 005 already ran.
DROP VIEW discovery_active;

CREATE VIEW discovery_active
    WITH (security_invoker = true) AS
SELECT b.tenant_id,
       b.biblio_id,
       b.title,
       b.author,
       b.isbn,
       b.publisher,
       b.copyright_date,
       b.item_type,
       b.series,
       b.ils_timestamp,
       b.search,
       b.embedding
  FROM discovery_biblio b
  JOIN discovery_tenant t
    ON t.tenant_id = b.tenant_id
   AND t.active_generation = b.generation;

COMMENT ON VIEW discovery_active IS
    'The live generation for each tenant. Read from here; write to discovery_biblio.';

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'libvox_app') THEN
        GRANT SELECT ON discovery_active TO libvox_app;
    ELSE
        RAISE NOTICE 'role libvox_app not found; skipping grants';
    END IF;
END
$$;
