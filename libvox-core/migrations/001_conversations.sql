-- 001 — the conversation log
--
-- What a patron asked a library, and what MARC.IA answered. Durable, so a
-- conversation survives a restart and so the benchmarking study
-- has transcripts rather than screenshots.
--
-- WHAT THIS DELIBERATELY DOES NOT HOLD
--
-- Nothing that identifies a person. No name, no email, no phone number, no IP,
-- no platform identifier. The root README's non-goal — "LibVox does not store
-- patron identity; conversations are scoped to a session, not to a person" —
-- survives persistence only if the schema makes it survive, and the absence of
-- those columns is the enforcement.
--
-- That matters more now than it did when history died with the process. Before,
-- what someone asked a library vanished; now it is durable, so a leak exposes
-- it. Which is also why retention is a command and not a suggestion: see
-- migration 002 and `libvox-core prune`.
--
-- No BEGIN/COMMIT here: the migration runner wraps each file in its own
-- transaction, and a COMMIT inside would close it out from under the runner.

CREATE TABLE conversations (
    conversation_id uuid        PRIMARY KEY,
    tenant_id       text        NOT NULL,

    -- Which adapter this arrived through. Not who: 'web' and 'telegram' say
    -- what the delivery looked like, and a chat id would say who, which is why
    -- one is here and the other is not.
    channel         text        NOT NULL DEFAULT 'web'
        CONSTRAINT conversations_channel_check
        CHECK (channel IN ('web', 'telegram', 'whatsapp', 'email')),

    started_at      timestamptz NOT NULL DEFAULT now(),

    -- What retention is measured against, and the reason it is a column rather
    -- than max(messages.created_at): the prune has to be an index scan on a
    -- schedule, not a join over everything ever said.
    last_message_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX conversations_tenant_idx ON conversations (tenant_id, last_message_at);
CREATE INDEX conversations_retention_idx ON conversations (last_message_at);

COMMENT ON TABLE conversations IS
    'One session''s conversation. Scoped to a session and never to a person: there is deliberately no column that identifies a reader.';

CREATE TABLE messages (
    message_id      bigint      GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    conversation_id uuid        NOT NULL
        REFERENCES conversations (conversation_id) ON DELETE CASCADE,

    -- The same three roles the model sees. 'tool' carries what a tool
    -- returned, which is what lets a later turn know which book the earlier
    -- one was about.
    role            text        NOT NULL
        CONSTRAINT messages_role_check CHECK (role IN ('user', 'assistant', 'tool')),

    content         text,
    tool_name       text,
    tool_arguments  jsonb,

    created_at      timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX messages_conversation_idx ON messages (conversation_id, message_id);

COMMENT ON COLUMN messages.content IS
    'For role=tool, the tool result as JSON. Prose would lose the biblio_id a later turn needs.';

-- ---------------------------------------------------------------------------
-- Tenant isolation
-- ---------------------------------------------------------------------------
--
-- A conversation is library data like any other, and rule 4 applies unchanged:
-- the database enforces the scoping, not the author of each query. A forgotten
-- WHERE returns nothing rather than another library's conversations.
--
-- `messages` has no tenant_id of its own on purpose -- it would be a second
-- copy of a fact, and the two could disagree. It is reached through its
-- conversation, whose policy the subquery re-evaluates under the same scope.

ALTER TABLE conversations ENABLE ROW LEVEL SECURITY;
ALTER TABLE conversations FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON conversations
    USING (tenant_id = current_setting('libvox.tenant_id', true))
    WITH CHECK (tenant_id = current_setting('libvox.tenant_id', true));

ALTER TABLE messages ENABLE ROW LEVEL SECURITY;
ALTER TABLE messages FORCE ROW LEVEL SECURITY;

CREATE POLICY tenant_isolation ON messages
    USING (conversation_id IN (SELECT conversation_id FROM conversations))
    WITH CHECK (conversation_id IN (SELECT conversation_id FROM conversations));

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'libvox_app') THEN
        GRANT USAGE ON SCHEMA public TO libvox_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON conversations TO libvox_app;
        GRANT SELECT, INSERT, UPDATE, DELETE ON messages TO libvox_app;
        GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO libvox_app;
    ELSE
        RAISE NOTICE 'role libvox_app not found; skipping grants';
    END IF;
END
$$;
