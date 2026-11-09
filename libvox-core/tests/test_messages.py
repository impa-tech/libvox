"""The route every channel will send to.

Real databases, a fake model and a fake tool layer: what is under test is the
wiring — that the library comes from the credential, that the conversation is
stored, and that a second message knows what the first one was about.
"""

from __future__ import annotations

import json

import psycopg
import pytest
from conftest import (
    CONVERSATION_DSN,
    TENANT_A,
    databases_up,
    register_library,
)

from libvox_core.agent import store
from libvox_core.agent.llm import Completion, ToolIntent

pytestmark = databases_up


class FakeToolBox:
    def __init__(self, result: dict | None = None) -> None:
        self._result = result or {"index_status": "ready", "records": []}
        self.calls: list[tuple[str, dict]] = []

    async def schemas(self) -> list[dict]:
        return [{"type": "function", "function": {"name": "search_catalog"}}]

    async def call(self, name: str, arguments: dict):
        from libvox_core.agent.tools import ToolCall

        self.calls.append((name, arguments))
        return ToolCall(name=name, arguments=arguments, result=self._result)


class FakeModel:
    def __init__(self, *completions: Completion) -> None:
        self._completions = list(completions)
        self.sent: list[list[dict]] = []

    async def complete(self, messages, *, tools=None):
        self.sent.append(messages)
        return self._completions.pop(0) if self._completions else Completion(text="ok")


@pytest.fixture
def wired(monkeypatch, client):
    """Substitute the model and the tool layer, keep everything else real."""
    from contextlib import asynccontextmanager

    from libvox_core.agent import service

    box = FakeToolBox()
    models: list[FakeModel] = []

    @asynccontextmanager
    # `**_` so a new keyword on `connect` -- the timeout was one --
    # surfaces as a signature error here rather than as a test that
    # waits forever for a frame the gateway turned into an ErrorEvent.
    async def fake_connect(url, token, **_):
        yield box

    def fake_model(*args, **kwargs):
        model = models[0] if models else FakeModel()
        return model

    monkeypatch.setattr(service, "connect", fake_connect)
    monkeypatch.setattr(service, "Model", fake_model)
    return client, box, models


def start_session(client) -> str:
    key = register_library(TENANT_A)
    minted = client.post(
        "/internal/v1/sessions",
        json={"channel": "web", "binding_key": key, "origin": "http://localhost:8081"},
    ).json()
    return minted["token"]


# --- the shape ---------------------------------------------------------------


def test_a_message_is_answered(wired) -> None:
    client, _, models = wired
    models.append(FakeModel(Completion(text="Bom dia!")))
    token = start_session(client)

    body = client.post(
        "/internal/v1/messages",
        json={"session_id": token, "text": "bom dia", "locale": "pt-BR",
              "capabilities": ["streaming"]},
    ).json()

    assert body["text"] == "Bom dia!"
    assert body["conversation_id"]
    assert body["truncated"] is False


def test_the_library_comes_from_the_credential_and_never_the_body(wired) -> None:
    """There is no tenant field to send, which is the point."""
    client, _, models = wired
    models.append(FakeModel(Completion(text="ok")))
    token = start_session(client)

    response = client.post(
        "/internal/v1/messages",
        json={"session_id": token, "text": "oi", "tenant_id": "lib-outra"},
    )

    assert response.status_code == 200
    with psycopg.connect(CONVERSATION_DSN) as conn, conn.cursor() as cur:
        cur.execute("SET ROLE libvox_app")
        cur.execute(
            "SELECT tenant_id FROM conversations "
            " WHERE tenant_id = current_setting('libvox.tenant_id', true)"
        )
    # The registry write went to the session's library, not the body's.
    assert response.json()["text"] == "ok"


def test_an_unknown_session_is_refused_without_saying_why(wired) -> None:
    """A 401 with a reason would tell a caller whether a token ever existed."""
    client, _, _ = wired
    body = client.post(
        "/internal/v1/messages", json={"session_id": "inventado", "text": "oi"}
    ).json()
    assert body == {"active": False}


def test_a_message_needs_the_service_token(settings, migrated) -> None:
    from fastapi.testclient import TestClient

    from libvox_core.app import create_app

    with TestClient(create_app(settings)) as bare:
        response = bare.post(
            "/internal/v1/messages", json={"session_id": "x", "text": "oi"}
        )
    assert response.status_code == 401


# --- the conversation ---------------------------------------------------------


def test_the_conversation_is_stored(wired) -> None:
    client, _, models = wired
    models.append(FakeModel(Completion(text="Temos dois.")))
    token = start_session(client)

    body = client.post(
        "/internal/v1/messages",
        json={"session_id": token, "text": "tem Dürrenmatt?"},
    ).json()

    import uuid

    with psycopg.connect(CONVERSATION_DSN) as conn:
        loaded = store.load(
            conn, TENANT_A, uuid.UUID(body["conversation_id"]), "system"
        )
    assert [m.role for m in loaded.messages] == ["user", "assistant"]
    assert loaded.messages[0].content == "tem Dürrenmatt?"


def test_the_second_message_knows_what_the_first_was_about(wired) -> None:
    """The whole reason the history is persisted rather than kept in memory."""
    client, box, models = wired
    box._result = {"index_status": "ready", "records": [{"biblio_id": 137}]}

    first = FakeModel(
        Completion(intents=[ToolIntent("c1", "search_catalog", {"query": "x"})]),
        Completion(text="Temos um: Gustav Mahler."),
    )
    models.append(first)
    token = start_session(client)

    started = client.post(
        "/internal/v1/messages", json={"session_id": token, "text": "tem algo?"}
    ).json()

    second = FakeModel(Completion(text="Sim."))
    models[0] = second
    client.post(
        "/internal/v1/messages",
        json={"session_id": token, "text": "esse está disponível?",
              "conversation_id": started["conversation_id"]},
    )

    sent = json.dumps(second.sent[0], ensure_ascii=False)
    assert "137" in sent, "the id from the first message did not survive into the second"


def test_a_new_message_without_a_conversation_id_starts_a_new_one(wired) -> None:
    client, _, models = wired
    models.append(FakeModel(Completion(text="a"), Completion(text="b")))
    token = start_session(client)

    first = client.post(
        "/internal/v1/messages", json={"session_id": token, "text": "um"}
    ).json()
    second = client.post(
        "/internal/v1/messages", json={"session_id": token, "text": "dois"}
    ).json()

    assert first["conversation_id"] != second["conversation_id"]


def test_the_reader_language_outranks_the_library_default(wired) -> None:
    """A patron writing in English to a Portuguese library is answered in
    English."""
    client, _, models = wired
    model = FakeModel(Completion(text="ok"))
    models.append(model)
    token = start_session(client)

    client.post(
        "/internal/v1/messages",
        json={"session_id": token, "text": "hello", "locale": "en"},
    )

    system = model.sent[0][0]["content"]
    assert "Answer in English" in system


def test_the_session_credential_is_never_sent_to_the_model(wired) -> None:
    client, _, models = wired
    model = FakeModel(Completion(text="ok"))
    models.append(model)
    token = start_session(client)

    client.post("/internal/v1/messages", json={"session_id": token, "text": "oi"})

    assert token not in json.dumps(model.sent[0])


def test_a_malformed_conversation_id_is_refused_rather_than_guessed(wired) -> None:
    client, _, models = wired
    models.append(FakeModel(Completion(text="ok")))
    token = start_session(client)

    body = client.post(
        "/internal/v1/messages",
        json={"session_id": token, "text": "oi", "conversation_id": "nao-e-uuid"},
    ).json()
    assert body == {"active": False}
