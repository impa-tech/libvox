-- 005 — a query without accents finds a record with them
--
-- Migration 004 put "Pokémon - Generation I" in the search vector, and a
-- search for "pokemon" still found nothing:
--
--     vector  ... 'pokémon' ...        query  'pokemon'
--
-- The `portuguese` configuration stems plurals and conjugations and leaves
-- accents exactly where they are. That is correct as a linguistic default and
-- wrong as a catalog search: people type into a search box the way they type
-- into a search box. "sertao", "memorias postumas", "jose", "pokemon" — every
-- one of them missed a record the library holds, and reported `ready` while
-- doing it, which is the failure this whole index is meant not to have.
--
-- The fix is a configuration of our own: unaccent first, then stem. Applied to
-- both sides, so it is symmetric -- the accented and the unaccented spelling
-- reduce to the same token, and either query finds either record.
--
-- WHY NOT unaccent() ON THE COLUMN
--
-- `unaccent(text)` is STABLE, not IMMUTABLE -- it reads a dictionary file --
-- so a generated column cannot call it. Used as a *dictionary inside a text
-- search configuration* it is fine, and `to_tsvector(regconfig, text)` stays
-- immutable. That is the difference between this working and the ALTER TABLE
-- being refused outright.
--
-- REWRITES THE TABLE, AGAIN
--
-- Same as 004, and for the same reason: the vector is generated, so changing
-- how it is built means rebuilding it. Existing rows are correct afterwards --
-- unlike 004, no re-import is needed, because the text is already here.

-- Trusted since PostgreSQL 13, so the database owner can create it; no
-- superuser step, and nothing for an operator to do by hand.
CREATE EXTENSION IF NOT EXISTS unaccent;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_ts_config WHERE cfgname = 'libvox_search'
    ) THEN
        CREATE TEXT SEARCH CONFIGURATION libvox_search (COPY = portuguese);
        -- Order matters: strip the accent, then hand the plain word to the
        -- stemmer. The reverse would stem "pokémon" and unaccent the result.
        ALTER TEXT SEARCH CONFIGURATION libvox_search
            ALTER MAPPING FOR hword, hword_part, word
            WITH unaccent, portuguese_stem;
    END IF;
END
$$;

COMMENT ON TEXT SEARCH CONFIGURATION libvox_search IS
    'Portuguese stemming with accents folded away. Both sides of every search use this.';

DROP VIEW discovery_active;

ALTER TABLE discovery_biblio DROP COLUMN search;

ALTER TABLE discovery_biblio
    ADD COLUMN search tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('libvox_search', coalesce(title,     '')), 'A') ||
        setweight(to_tsvector('libvox_search', coalesce(series,    '')), 'B') ||
        setweight(to_tsvector('libvox_search', coalesce(author,    '')), 'B') ||
        setweight(to_tsvector('libvox_search', coalesce(publisher, '')), 'C') ||
        setweight(to_tsvector('libvox_search', coalesce(abstract,  '')), 'D') ||
        setweight(to_tsvector('libvox_search', coalesce(notes,     '')), 'D')
    ) STORED;

CREATE INDEX discovery_biblio_search_idx
    ON discovery_biblio USING gin (search);

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
       b.search
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
