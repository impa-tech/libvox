"""The conversation log: what is kept, who can read it, and when it is forgotten.

Real PostgreSQL, because the point of the schema is row-level security and a
foreign key that cascades — neither exists under SQLite.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import fields as dataclass_fields

import psycopg
import pytest
from conftest import (
    CONVERSATION_ADMIN,
    CONVERSATION_DSN,
    TENANT_A,
    TENANT_B,
    databases_up,
)

from libvox_core.agent import store
from libvox_core.agent.conversation import Message

pytestmark = databases_up


@pytest.fixture
def conn(migrated: None):
    with psycopg.connect(CONVERSATION_DSN) as connection:
        with connection.cursor() as cur:
            cur.execute("SET statement_timeout = 15000")
        connection.commit()
        yield connection


def a_turn() -> list[Message]:
    return [
        Message("user", "tem algo do Dürrenmatt?"),
        Message("assistant", "", tool_name="search_catalog",
                tool_arguments={"query": "Dürrenmatt"}, tool_call_id="c1"),
        Message("tool", json.dumps({"records": [{"biblio_id": 137}]}),
                tool_name="search_catalog", tool_call_id="c1"),
        Message("assistant", "Temos dois."),
    ]


# --- keeping it -------------------------------------------------------------


def test_a_conversation_is_stored_and_can_be_read_back(conn) -> None:
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    restored = store.load(conn, TENANT_A, opened.conversation_id, "system")

    assert [m.role for m in restored.messages] == [
        "user", "assistant", "tool", "assistant"
    ]


def test_the_tool_result_survives_a_restart(conn) -> None:
    """The whole reason to store it: the second question still knows which book
    the first one was about, even across a process that died."""
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    restored = store.load(conn, TENANT_A, opened.conversation_id, "system")
    tool_message = next(m for m in restored.messages if m.role == "tool")

    assert json.loads(tool_message.content)["records"][0]["biblio_id"] == 137


def test_the_system_prompt_is_rebuilt_and_never_read_back(conn) -> None:
    """A policy layer read from a row is a policy layer someone can edit in the
    database — and a library that renamed its assistant should not have the old
    name resurface mid-conversation."""
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    restored = store.load(conn, TENANT_A, opened.conversation_id, "A NEW PROMPT")
    assert restored.system_prompt == "A NEW PROMPT"

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM information_schema.columns "
            " WHERE table_name = 'conversations' AND column_name LIKE '%prompt%'"
        )
        assert cur.fetchone()[0] == 0


def test_the_conversation_id_is_not_the_session_token(conn) -> None:
    """A bearer credential used as a primary key ends up in every backup, query
    log and screenshot of a result set."""
    opened = store.open_conversation(conn, TENANT_A)
    assert isinstance(opened.conversation_id, uuid.UUID)


# --- who can read it --------------------------------------------------------


def test_one_library_cannot_read_another_librarys_conversations(conn) -> None:
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    seen_by_b = store.load(conn, TENANT_B, opened.conversation_id, "system")
    assert seen_by_b.messages == []


def test_a_conversation_cannot_be_written_for_another_library(conn) -> None:
    """WITH CHECK refuses it, rather than a WHERE clause somebody remembered."""
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        from libvox_sync.db import tenant_scope

        with tenant_scope(conn, TENANT_A) as scoped, scoped.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (conversation_id, tenant_id) "
                "VALUES (%s, %s)",
                (uuid.uuid4(), TENANT_B),
            )


def test_without_a_scope_no_conversation_is_visible(conn) -> None:
    """Fail closed, the same as every other table in this system."""
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM conversations")
        assert cur.fetchone()[0] == 0
    conn.rollback()


# --- what it does not hold --------------------------------------------------


def test_nothing_in_the_schema_identifies_a_person(conn) -> None:
    """The root README's non-goal survives persistence only if the schema makes
    it survive."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT column_name FROM information_schema.columns "
            " WHERE table_name IN ('conversations', 'messages')"
        )
        columns = {row[0] for row in cur.fetchall()}
    conn.rollback()

    assert not columns & {
        "user_id", "patron", "patron_id", "email", "phone", "ip_address",
        "ip", "name", "subject", "chat_id", "session_id",
    }


def test_the_stored_conversation_carries_no_person_either() -> None:
    carried = {f.name for f in dataclass_fields(store.StoredConversation)}
    assert carried == {"conversation_id", "tenant_id", "channel"}


# --- forgetting it ----------------------------------------------------------


def test_a_conversation_older_than_the_window_is_forgotten(conn) -> None:
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    with psycopg.connect(CONVERSATION_ADMIN) as admin:
        with admin.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET last_message_at = now() - interval '200 days'"
            )
        admin.commit()

        assert store.prune(admin, older_than_days=90) == 1

    assert store.load(conn, TENANT_A, opened.conversation_id, "s").messages == []


def test_a_recent_conversation_is_kept(conn) -> None:
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    with psycopg.connect(CONVERSATION_ADMIN) as admin:
        assert store.prune(admin, older_than_days=90) == 0

    assert store.load(conn, TENANT_A, opened.conversation_id, "s").messages


def test_forgetting_a_conversation_forgets_its_messages(conn) -> None:
    """By cascade, which is why the foreign key has one: a message whose
    conversation was pruned would be a transcript nobody can find to delete."""
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    with psycopg.connect(CONVERSATION_ADMIN) as admin:
        with admin.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET last_message_at = now() - interval '200 days'"
            )
        admin.commit()
        store.prune(admin, older_than_days=90)

        with admin.cursor() as cur:
            cur.execute("SELECT count(*) FROM messages")
            assert cur.fetchone()[0] == 0


def test_appending_moves_the_retention_clock(conn) -> None:
    """In the same transaction as the write, so it cannot disagree with what is
    stored."""
    opened = store.open_conversation(conn, TENANT_A)

    with psycopg.connect(CONVERSATION_ADMIN) as admin:
        with admin.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET last_message_at = now() - interval '200 days'"
            )
        admin.commit()

    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    with psycopg.connect(CONVERSATION_ADMIN) as admin:
        assert store.prune(admin, older_than_days=90) == 0


def test_pruning_on_the_service_connection_is_refused_not_silent(conn) -> None:
    """The failure this guard exists for, and it was live.

    `libvox_app` is subject to row-level security and no tenant scope is set
    for an operator's job, so it sees no conversations and every DELETE it
    issues matches nothing and commits cleanly. Measured on the dev database
    before this check existed: 0 of 30 visible, 0 candidates for deletion.

    Returning 0 would be indistinguishable from "nothing was old enough" to
    whoever reads the nightly log, and only one of those is fine.
    """
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    with psycopg.connect(CONVERSATION_ADMIN) as admin, admin.cursor() as cur:
        cur.execute(
            "UPDATE conversations SET last_message_at = now() - interval '200 days'"
        )
        admin.commit()

    with psycopg.connect(CONVERSATION_DSN) as app, pytest.raises(
        store.RetentionUnavailable
    ):
        store.prune(app, older_than_days=90)

    # And the conversation is still there, rather than half-deleted.
    with psycopg.connect(CONVERSATION_ADMIN) as admin, admin.cursor() as cur:
        cur.execute("SELECT count(*) FROM conversations")
        assert cur.fetchone()[0] == 1


def test_a_dry_run_counts_and_deletes_nothing(conn) -> None:
    """An operator command that spans every library should be answerable
    before it is run."""
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    with psycopg.connect(CONVERSATION_ADMIN) as admin:
        with admin.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET last_message_at = now() - interval '200 days'"
            )
        admin.commit()

        assert store.prune(admin, older_than_days=90, dry_run=True) == 1
        assert store.prune(admin, older_than_days=90, dry_run=True) == 1

    assert store.load(conn, TENANT_A, opened.conversation_id, "s").messages


def test_a_restored_tool_result_keeps_the_call_it_belongs_to(conn) -> None:
    """The bug that made "is the second one available?" fail across a restart.

    Numbering each restored message separately gave the call and its result
    different ids, the pairing broke, and the orphan filter dropped the very
    line that carried the biblio_id.
    """
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    wire = store.load(conn, TENANT_A, opened.conversation_id, "system").as_wire()

    called = {
        call["id"]
        for message in wire
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    }
    results = [m for m in wire if m.get("role") == "tool"]

    assert results, "the tool result did not survive the round trip"
    assert results[0]["tool_call_id"] in called


# --- whether it helped ------------------------------------------------------


def _rated(conn, conversation_id) -> list[tuple]:
    with conn.cursor() as cur:
        cur.execute("select set_config('libvox.tenant_id', %s, false)", (TENANT_A,))
        cur.execute(
            "SELECT m.content, r.useful FROM message_ratings r "
            "  JOIN messages m USING (message_id) "
            " WHERE m.conversation_id = %s ORDER BY m.message_id",
            (conversation_id,),
        )
        return cur.fetchall()


def test_the_ordinal_skips_the_messages_that_carry_tool_calls(conn) -> None:
    """An assistant message with a tool_name is a request, not an answer. The
    reader counted what appeared on screen, and that is only the second one."""
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    store.rate(conn, TENANT_A, opened.conversation_id, answer=1, useful=True)

    assert _rated(conn, opened.conversation_id) == [("Temos dois.", True)]


def test_an_ordinal_with_no_answer_behind_it_is_refused(conn) -> None:
    """Raised rather than returned false: a widget naming an answer the server
    never gave is a bug worth a log line, and a write that failed is not."""
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())

    with pytest.raises(store.NoSuchAnswer):
        store.rate(conn, TENANT_A, opened.conversation_id, answer=2, useful=True)

    with pytest.raises(store.NoSuchAnswer):
        store.rate(conn, TENANT_A, opened.conversation_id, answer=0, useful=True)


def test_a_rating_never_crosses_into_another_conversation(conn) -> None:
    """Two conversations, each with one answer, and the ordinal is per
    conversation -- "the first" means a different message in each."""
    first = store.open_conversation(conn, TENANT_A)
    second = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, first.conversation_id, a_turn())
    store.append(
        conn, TENANT_A, second.conversation_id,
        [Message("user", "e xadrez?"), Message("assistant", "Nada sobre xadrez.")],
    )

    store.rate(conn, TENANT_A, second.conversation_id, answer=1, useful=False)

    assert _rated(conn, first.conversation_id) == []
    assert _rated(conn, second.conversation_id) == [("Nada sobre xadrez.", False)]


def test_a_rating_never_crosses_into_another_library(conn) -> None:
    """The policy is reached through the message, which is reached through the
    conversation. Scoped to B, A's conversation is not there to rate."""
    mine = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, mine.conversation_id, a_turn())

    with pytest.raises(store.NoSuchAnswer):
        store.rate(conn, TENANT_B, mine.conversation_id, answer=1, useful=True)

    assert _rated(conn, mine.conversation_id) == []


def test_forgetting_a_conversation_forgets_its_ratings(conn) -> None:
    """Retention is the whole reason for the cascade. Without it the ratings
    would outlive the transcripts they describe and become the one collection
    nobody set a deadline for."""
    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, a_turn())
    store.rate(conn, TENANT_A, opened.conversation_id, answer=1, useful=True)
    conn.commit()

    # Scoped, because this connection is libvox_app and the policy is
    # fail-closed: an unscoped UPDATE here matches nothing and commits cleanly,
    # which is the row-level security working and looks exactly like the test
    # passing for the wrong reason.
    with conn.cursor() as cur:
        cur.execute("select set_config('libvox.tenant_id', %s, false)", (TENANT_A,))
        cur.execute(
            "UPDATE conversations SET last_message_at = now() - interval '400 days' "
            " WHERE conversation_id = %s",
            (opened.conversation_id,),
        )
        assert cur.rowcount == 1
    conn.commit()

    with psycopg.connect(CONVERSATION_ADMIN) as admin:
        assert store.prune(admin, older_than_days=90) >= 1
        with admin.cursor() as cur:
            cur.execute("SELECT count(*) FROM message_ratings")
            assert cur.fetchone()[0] == 0


def test_what_the_provider_attached_survives_a_reload(conn) -> None:
    """The half of the fix that is not obvious, and the one a reader hits.

    Gemini validates a signature it issued against the history it gets back, so
    it has to be the same bytes. The call id above it is synthesized on load and
    that is fine -- only the pairing has to survive there. This cannot be
    synthesized, and the conversation is rebuilt from these rows between turns,
    so without the column a reader's *second* question fails exactly as the
    first one did.
    """
    signed = {"google": {"thought_signature": "Ct4BAdHt1m8xY..."}}
    turn = a_turn()
    turn[1] = Message(
        "assistant", "", tool_name="search_catalog",
        tool_arguments={"query": "Dürrenmatt"}, tool_call_id="c1",
        provider_extra=signed,
    )

    opened = store.open_conversation(conn, TENANT_A)
    store.append(conn, TENANT_A, opened.conversation_id, turn)

    restored = store.load(conn, TENANT_A, opened.conversation_id, "system")
    call = next(m for m in restored.messages if m.role == "assistant" and m.tool_name)

    assert call.provider_extra == signed
    # And it reaches the wire, which is the only place it does any good.
    assert restored.as_wire()[2]["tool_calls"][0]["extra_content"] == signed
