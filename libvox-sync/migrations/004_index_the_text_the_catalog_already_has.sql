-- 004 — series, abstract and notes reach the search vector
--
-- A library whose catalog is Pokémon records has titles like "Metapod" and a
-- series of "Pokémon - Generation I". Someone asking for pokémon found nothing
-- at all, and — because the index was `ready` and complete — was told so with
-- confidence, about a copy sitting on a shelf in Centerville.
--
-- That was not a search bug. It was coverage: this table mirrored the seven
-- fields of the driver's BiblioRecord, and the vector reached only title,
-- author and publisher. Koha returns series, abstract and notes in the *same*
-- request that returns the title; they were being read and dropped.
--
-- WHAT GETS WHICH WEIGHT
--
--   A  title       what the work is called
--   B  series      often the word a patron actually types; author-level
--   C  publisher
--   D  abstract, notes
--
-- D is deliberately the floor. Descriptive prose is long, and a term that
-- appears once in passing inside a paragraph must never outrank a title that
-- matches exactly. This is the tradeoff the driver already documents in the
-- other direction -- "chess" reaching "The Duchess of Malfi" -- and the weight
-- is what keeps recall from becoming noise at the top of the list.
--
-- ABSTRACT AND NOTES ARE INDEXED, NOT RETURNED
--
-- They stay off `discovery_active`, which is the contract with libvox-mcp:
-- every column of that view is a CatalogRecord field, and a test asserts it.
-- Twenty abstracts in one tool result is more text than the conversation
-- around it, and MARC.IA's job is to say what the library holds, not to recite
-- a plot summary she cannot verify.
--
-- REWRITES THE TABLE
--
-- A generated column cannot be altered in place, so it is dropped and
-- recreated -- which rewrites every row and rebuilds the GIN index. On a live
-- catalog run this in a window; the alternative, a new column with a new name,
-- would leave the old one behind forever to avoid one rewrite.
--
-- Existing rows get NULL in the three new columns and a vector without them:
-- correct but incomplete, so `libvox-sync full` is required afterwards. There
-- is no backfill from SQL -- the text lives in the ILS, not here.

-- The view selects `search`, so it blocks the drop: `cannot drop column search
-- ... because other objects depend on it`. It is recreated at the bottom.
DROP VIEW discovery_active;

ALTER TABLE discovery_biblio DROP COLUMN search;

ALTER TABLE discovery_biblio
    ADD COLUMN series   text,
    ADD COLUMN abstract text,
    ADD COLUMN notes    text;

ALTER TABLE discovery_biblio
    ADD COLUMN search tsvector GENERATED ALWAYS AS (
        setweight(to_tsvector('portuguese', coalesce(title,     '')), 'A') ||
        setweight(to_tsvector('portuguese', coalesce(series,    '')), 'B') ||
        setweight(to_tsvector('portuguese', coalesce(author,    '')), 'B') ||
        setweight(to_tsvector('portuguese', coalesce(publisher, '')), 'C') ||
        setweight(to_tsvector('portuguese', coalesce(abstract,  '')), 'D') ||
        setweight(to_tsvector('portuguese', coalesce(notes,     '')), 'D')
    ) STORED;

CREATE INDEX discovery_biblio_search_idx
    ON discovery_biblio USING gin (search);

COMMENT ON COLUMN discovery_biblio.series IS
    'Series or collection. Frequently the term a patron searches by.';
COMMENT ON COLUMN discovery_biblio.abstract IS
    'Indexed at weight D, never returned. Reaches a record by what it is about.';
COMMENT ON COLUMN discovery_biblio.notes IS
    'Indexed at weight D, never returned. Cataloguer prose; noisy, hence D.';

-- Back, gaining series and nothing else: this view is the MCP contract, and
-- every column in it is a CatalogRecord field.
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

-- A recreated view is a new object, and the grant did not survive with the
-- name: every query came back `permission denied for view discovery_active`.
-- Guarded the same way migration 001 guards it, for a database where the role
-- is provisioned elsewhere or not at all.
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'libvox_app') THEN
        GRANT SELECT ON discovery_active TO libvox_app;
    ELSE
        RAISE NOTICE 'role libvox_app not found; skipping grants';
    END IF;
END
$$;
