-- 002 — whether the answer helped
--
-- A reader presses a thumb under one of MARC.IA's answers. That is the whole
-- of the feature, and this table is deliberately the smallest thing that can
-- hold it.
--
-- WHY THIS EXISTS, WHICH IS NOT "A SATISFACTION SCORE"
--
-- The number worth having is not the share of readers who were happy. It is
-- the *join*: a thumbs-down on an answer whose tool result was
-- `{"records": []}` says the library told someone it holds nothing and the
-- someone disagreed — a gap in the collection, a gap in the index, or a search
-- that failed on the word the reader used. That is actionable, and a
-- percentage is not. Both cost the same to collect, so both screens are built
-- around the join and neither shows a tally.
--
-- THE FOURTH COLUMN THAT IS NOT HERE
--
-- No session id, no device fingerprint, no IP, no user agent, no reason field
-- carrying free text a reader typed. Migration 001 makes the point at length
-- and it applies here without weakening: what a reader thought of an answer is
-- more sensitive than the answer, not less, and the absence of a column is the
-- only enforcement that survives someone writing a query in a hurry.
--
-- `message_id` as the primary key, rather than an id of its own, is part of
-- that. One row per answer means a reader who changes their mind replaces
-- their thumb instead of leaving two rows behind — and two rows an hour apart
-- would be a weak fingerprint saying "the same person was here twice".
--
-- The cascade is not housekeeping either. Retention is `libvox-core prune`,
-- which deletes conversations; without ON DELETE CASCADE the ratings would
-- outlive the transcripts and become the one collection nobody set a deadline
-- for.
--
-- No BEGIN/COMMIT: the runner wraps each file in its own transaction.

CREATE TABLE message_ratings (
    message_id bigint      PRIMARY KEY
        REFERENCES messages (message_id) ON DELETE CASCADE,

    useful     boolean     NOT NULL,

    rated_at   timestamptz NOT NULL DEFAULT now()
);

COMMENT ON TABLE message_ratings IS
    'Whether one answer helped. Attached to a message and never to a person: there is deliberately no column that could identify or re-identify a reader.';

COMMENT ON COLUMN message_ratings.message_id IS
    'The answer being rated -- an assistant message with no tool_name. Primary key, so a reader who changes their mind replaces the row rather than leaving a second one.';

COMMENT ON COLUMN message_ratings.rated_at IS
    'When, at the same granularity messages.created_at already carries. Not a second clock, and not a session: two ratings say nothing about being the same reader.';

-- ---------------------------------------------------------------------------
-- Tenant isolation
-- ---------------------------------------------------------------------------
--
-- Reached through its message, whose own policy is reached through its
-- conversation. Same shape as `messages`, and for the same reason 001 gives: a
-- tenant_id of its own would be a third copy of a fact, and three copies can
-- disagree two ways.

ALTER TABLE message_ratings ENABLE ROW LEVEL SECURITY;
ALTER TABLE message_ratings FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON message_ratings
    USING (message_id IN (SELECT message_id FROM messages))
    WITH CHECK (message_id IN (SELECT message_id FROM messages));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'libvox_app') THEN
        -- The operator's panel reads as this role too, not only Core.
        GRANT SELECT, INSERT, UPDATE, DELETE ON message_ratings TO libvox_app;
    ELSE
        RAISE NOTICE 'role libvox_app not found; skipping grants';
    END IF;
END
$$;
