"""Keeping the conversation.

A database Core owns — `libvox_conversations`, on the same PostgreSQL instance
as the Tenant DB but a different database, because the rule the architecture
protects is **one writer per database** and not one database per server. Core
still writes nothing to `libvox_tenant`.

Everything here runs inside `tenant_scope`, so row-level security is what keeps
one library's conversations away from another's. A forgotten filter returns
nothing rather than someone else's transcript.

**The patron's text goes to the table and nowhere else.** Not to a log line,
not to an exception message — the same rule the ILS secret already has, for a
different reason: a log with a conversation in it is a second copy nobody set a
retention period for.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import psycopg

from .conversation import Conversation, Message

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 90
"""Long enough to investigate a problem someone reported, short enough not to
become a collection. Named here and in the README rather than left implicit,
because a conversation log without a deadline is accumulation and not a record.
"""


@dataclass(frozen=True, slots=True)
class StoredConversation:
    conversation_id: uuid.UUID
    tenant_id: str
    channel: str


def open_conversation(
    conn: psycopg.Connection, tenant_id: str, *, channel: str = "web"
) -> StoredConversation:
    """Start one, and hand back its own identifier.

    **Not the session token.** That is a bearer credential, and a credential
    used as a primary key is a credential in every backup, every query log and
    every screenshot of a result set. The conversation gets a uuid of its own
    and the session merely points at it while it lives.
    """
    from libvox_sync.db import tenant_scope

    conversation_id = uuid.uuid4()
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "INSERT INTO conversations (conversation_id, tenant_id, channel) "
            "VALUES (%s, %s, %s)",
            (conversation_id, tenant_id, channel),
        )
    logger.info("opened a %s conversation for %s", channel, tenant_id)
    return StoredConversation(conversation_id, tenant_id, channel)


def append(
    conn: psycopg.Connection,
    tenant_id: str,
    conversation_id: uuid.UUID,
    messages: list[Message],
) -> int:
    """Write messages, and move the retention clock forward.

    `last_message_at` is updated in the same transaction as the write, so it
    cannot disagree with what is stored — the same reasoning that put
    `ils_configured_at` on the tenants table rather than deriving it.
    """
    from libvox_sync.db import tenant_scope

    if not messages:
        return 0

    rows = [
        (
            conversation_id,
            message.role,
            message.content,
            message.tool_name,
            json.dumps(message.tool_arguments) if message.tool_arguments else None,
            json.dumps(message.provider_extra) if message.provider_extra else None,
        )
        for message in messages
    ]

    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.executemany(
            "INSERT INTO messages "
            "  (conversation_id, role, content, tool_name, tool_arguments, "
            "   provider_extra) "
            "VALUES (%s, %s, %s, %s, %s, %s)",
            rows,
        )
        cur.execute(
            "UPDATE conversations SET last_message_at = now() "
            " WHERE conversation_id = %s",
            (conversation_id,),
        )
    # The count, never the content. A log line holding what someone asked a
    # library is a second copy with no retention period attached.
    logger.info("stored %d message(s) for %s", len(rows), tenant_id)
    return len(rows)


class NoSuchAnswer(LookupError):
    """The ordinal named no answer in this conversation.

    Raised rather than returning False, because the two things a caller does
    about them differ: an answer that is not there is a widget counting
    something the server never said, which is a bug worth a log line, and a
    write that failed is not.
    """


def rate(
    conn: psycopg.Connection,
    tenant_id: str,
    conversation_id: uuid.UUID,
    *,
    answer: int,
    useful: bool,
) -> int:
    """Record whether one answer helped, and hand back which message it was.

    **`answer` is an ordinal, not an identifier.** The reader's browser is never
    given a `message_id` -- that is a global sequence, and handing one over
    would leak how much the whole installation has answered, on top of being
    exactly the kind of id `gateway.py` keeps server-side on purpose. What the
    widget can honestly name is what it has been shown: the first answer, the
    third. This resolves that against the conversation the socket is already
    holding.

    The ordinal is sound only because every path that answers a reader stores
    exactly one assistant message; `test_loop.py` pins that, after the one path
    that did not was found by counting these rows against user messages.

    Upsert rather than insert. One row per answer is the schema's decision and
    the reason is privacy, not tidiness: a reader who changes their mind
    replaces a thumb, where two rows an hour apart would be a weak fingerprint
    saying the same person was here twice.
    """
    from libvox_sync.db import tenant_scope

    if answer < 1:
        raise NoSuchAnswer(f"answers are numbered from one, got {answer}")

    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        # `tool_name IS NULL` is what separates a spoken answer from the
        # assistant messages that carry tool calls. Ordered by message_id
        # because that is the order they were said in, which is the order the
        # reader counted.
        cur.execute(
            "SELECT message_id FROM messages "
            " WHERE conversation_id = %s AND role = 'assistant' "
            "   AND tool_name IS NULL "
            " ORDER BY message_id OFFSET %s LIMIT 1",
            (conversation_id, answer - 1),
        )
        row = cur.fetchone()
        if row is None:
            raise NoSuchAnswer(f"no answer number {answer} in this conversation")

        message_id = row[0]
        cur.execute(
            "INSERT INTO message_ratings (message_id, useful) VALUES (%s, %s) "
            "ON CONFLICT (message_id) DO UPDATE "
            "   SET useful = EXCLUDED.useful, rated_at = now()",
            (message_id, useful),
        )

    # Which answer and whether, never the text of either.
    logger.info(
        "answer %d of a conversation rated %s for %s",
        answer,
        "useful" if useful else "not useful",
        tenant_id,
    )
    return message_id


def load(
    conn: psycopg.Connection,
    tenant_id: str,
    conversation_id: uuid.UUID,
    system_prompt: str,
) -> Conversation:
    """Rebuild a conversation from what was written.

    The system prompt is *not* stored and is rebuilt from the library's current
    persona instead. Two reasons: a policy layer read back from a row is a
    policy layer someone can edit in the database, and a library that changed
    its assistant's name should not have the old one resurface mid-conversation.
    """
    from libvox_sync.db import tenant_scope

    conversation = Conversation(system_prompt)

    # Call ids are per-request and mean nothing across a restart, so they are
    # regenerated rather than stored. What has to survive is the *pairing*: a
    # tool result whose id does not match its call is an orphan, and every
    # OpenAI-compatible endpoint either ignores it or refuses the request.
    #
    # Numbering each message separately breaks exactly that, which is how the
    # first version of this silently dropped the tool result that made "is the
    # second one available?" answerable. A tool message takes the id of the
    # nearest preceding call of the same tool.
    pending: dict[str, str] = {}
    issued = 0

    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT role, content, tool_name, tool_arguments, provider_extra "
            "  FROM messages WHERE conversation_id = %s ORDER BY message_id",
            (conversation_id,),
        )
        for role, content, tool_name, tool_arguments, provider_extra in cur.fetchall():
            if role == "assistant" and tool_name:
                issued += 1
                call_id = f"restored_{issued}"
                pending[tool_name] = call_id
            elif role == "tool":
                call_id = pending.pop(tool_name or "", None)
                if call_id is None:
                    # A result whose call is not in the history. Dropped rather
                    # than given an invented id, which would produce the orphan
                    # this is here to avoid.
                    logger.warning("dropping a stored tool result with no call")
                    continue
            else:
                call_id = None

            conversation.messages.append(
                Message(
                    role=role,
                    content=content or "",
                    tool_name=tool_name,
                    tool_arguments=tool_arguments,
                    tool_call_id=call_id,
                    # The call id above is synthesized, because only the
                    # pairing has to survive. This cannot be: it is a token the
                    # provider signed over something only the model saw, and a
                    # made-up one is rejected exactly like a missing one.
                    provider_extra=provider_extra,
                )
            )
    return conversation


class RetentionUnavailable(RuntimeError):
    """This connection cannot forget anything, so nothing was forgotten.

    Raised rather than returning zero, because the two are indistinguishable to
    whoever scheduled the job and only one of them is fine. Measured on the dev
    database: the app role saw 0 of 30 conversations and the DELETE matched 0
    rows. A retention job that logs "forgot 0" every night forever is worse
    than no retention at all — nobody looks again at a task reporting success.
    """


def prune(
    conn: psycopg.Connection,
    *,
    older_than_days: int = DEFAULT_RETENTION_DAYS,
    dry_run: bool = False,
) -> int:
    """Forget conversations past the retention window.

    Deliberately **not** tenant-scoped: this is the operator's job, it spans
    libraries by definition, and it deletes rather than reads — there is no
    transcript to leak in a DELETE that names only a date.

    Which is exactly why it cannot run on the connection the service uses.
    Row-level security is fail-closed here: with no `libvox.tenant_id` set,
    `libvox_app` sees no conversations at all, so every DELETE it issues
    matches nothing and commits cleanly. Hence the check below, and
    `Settings.conversation_admin_dsn`.

    Messages go by cascade, which is why the foreign key has one. A cascade
    runs as the system rather than as the caller, so the policy on `messages`
    does not filter it.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"
        )
        row = cur.fetchone()
        if not row or not row[0]:
            cur.execute("SELECT current_user")
            role = cur.fetchone()[0]
            raise RetentionUnavailable(
                f"{role} is subject to row-level security, so it sees no "
                "conversations and would delete none while reporting success. "
                "Point LIBVOX_CONVERSATION_ADMIN_DSN at a role that can read "
                "across libraries."
            )

    cutoff = datetime.now(timezone.utc) - timedelta(days=older_than_days)

    with conn.cursor() as cur:
        if dry_run:
            cur.execute(
                "SELECT count(*) FROM conversations WHERE last_message_at < %s",
                (cutoff,),
            )
            would = cur.fetchone()[0]
            logger.info(
                "%d conversation(s) older than %d days would be forgotten",
                would,
                older_than_days,
            )
            return would

        cur.execute(
            "DELETE FROM conversations WHERE last_message_at < %s", (cutoff,)
        )
        removed = cur.rowcount or 0
    conn.commit()

    logger.info(
        "forgot %d conversation(s) older than %d days", removed, older_than_days
    )
    return removed
