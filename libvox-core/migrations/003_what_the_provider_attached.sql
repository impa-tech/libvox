-- 003 — a tool call carries back whatever the provider attached to it
--
-- Gemini's thinking models sign the reasoning that precedes a tool call and
-- return the signature under `extra_content`, outside the OpenAI schema. Send
-- the tool result without it and the request is refused:
--
--   400 Function call is missing a thought_signature in functionCall parts
--
-- The Core reconstructs an assistant tool-call message from tool_name,
-- tool_arguments and a call id it synthesizes on load -- which is right for the
-- id, since only the pairing has to survive. A signature is the opposite: it is
-- a cryptographic token over something only the model saw, so it has to be the
-- same bytes or it is worth nothing.
--
-- And it has to be *here* rather than in memory. The history goes back to the
-- model on every turn, and a conversation is rebuilt from these rows between
-- turns, so without the column a reader's second question fails exactly as the
-- first one did.
--
-- Deliberately opaque and deliberately not named `thought_signature`. The Core
-- never opens it, which is what keeps "any OpenAI-compatible endpoint" true
-- rather than true-with-an-exception-for-Google -- and what makes the next
-- provider with a field of its own cost no edit at all.
--
-- Nullable, and null for every row written before this: no model that answered
-- without one needs one now.

ALTER TABLE messages ADD COLUMN provider_extra jsonb;

COMMENT ON COLUMN messages.provider_extra IS
    'Opaque per-call data the model provider attached, echoed back verbatim on '
    'later turns. Never parsed here.';
