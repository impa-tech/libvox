-- 007 — an import is visible before it writes its first record
--
-- The librarian presses *Atualizar índice*, the panel sends her to the import
-- screen, and the screen said the previous import, finished. Refreshing three
-- or four times eventually showed the new one running.
--
-- Nothing was cached. The screen infers "an import is under way" from rows
-- existing in a generation newer than the live one, and between the press and
-- the first of those rows there is a real gap. The worker wakes on NOTIFY in
-- milliseconds and stamps `tenant_events.consumed_at` immediately -- so the
-- request stops being pending, which is the other thing the screen looks at --
-- and only then authenticates against the library's own server and asks for a
-- page. Twenty-five records later the first batch is written. For those
-- seconds every signal the panel had said the same thing: nothing pending,
-- nothing being written, done.
--
-- Neither existing column can carry it.
--
--   * `index_status` must NOT become 'building' during a re-import. The
--     previous generation is still answering perfectly well, and that column is
--     a contract with libvox_mcp.models.IndexStatus -- it is how MARC.IA
--     decides whether an empty result means "no matches" or "not indexed yet".
--     See _record_failure, which has the same reasoning written out.
--   * `last_sync_at` is the end of an import, not the beginning.
--
-- So: a column that is non-NULL exactly while an import is running. The
-- pipeline sets it and clears it around the same span the advisory lock covers
-- -- the lock is the truest answer to "is one running", and it is not readable
-- from another database, which is where the panel lives.

ALTER TABLE discovery_tenant ADD COLUMN import_started_at timestamptz;

COMMENT ON COLUMN discovery_tenant.import_started_at IS
    'When the import now running began, and NULL when none is. Set and cleared '
    'by the sync worker, so a panel can say "importing" before the first record '
    'is written. A worker killed outright leaves it set until the next import.';
