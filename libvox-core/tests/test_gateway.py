"""The web channel: what a reader's browser is handed, and what it never is.

`TestClient.websocket_connect` runs the real application, so the handshake
under test is the handshake a browser performs — Origin header included.
"""

from __future__ import annotations

import json

import pytest
from conftest import (
    SERVICE_TOKEN,
    TENANT_A,
    TENANT_B,
    databases_up,
    register_library,
)
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from libvox_core.app import create_public_app
from libvox_core.gateway import REFUSED
from libvox_core.sessions import SessionStore

# Every test here calls `register_library`, because the handshake resolves its
# binding key against `channel_bindings` in the Tenant DB -- there is no
# gateway test that can open a socket without a row. The mark says so, instead
# of letting fifteen tests error one by one on the same missing server.
pytestmark = databases_up

ORIGIN = "http://localhost:8081"


@pytest.fixture
def public(settings, migrated):
    """The public app, with a store this test can inspect."""
    store = SessionStore(ttl_s=settings.session_ttl_s)
    with TestClient(create_public_app(settings, store)) as client:
        client.sessions = store  # type: ignore[attr-defined]
        yield client


def open_chat(client: TestClient, key: str, *, origin: str | None = ORIGIN):
    headers = {"Origin": origin} if origin else {}
    return client.websocket_connect(f"/ws/v1/chat?key={key}", headers=headers)


# --- the handshake ----------------------------------------------------------


def test_a_reader_is_greeted_in_the_librarys_own_voice(public) -> None:
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ready = ws.receive_json()

    assert ready["type"] == "SessionReadyEvent"
    assert ready["assistant_name"] == "MARC.IA"
    assert ready["theme_color"]
    # Asserted on the value, not its presence: this read `default_locale` for
    # a while, which widget_config renames to `locale` on the way out. It
    # answered None every time, and None is also what "no preference" looks
    # like, so the bug had nothing to show for itself.
    assert ready["locale"] == "pt-BR"


def test_the_session_token_never_reaches_the_browser(public) -> None:
    """The sentence routes.py wrote before there was a connection to bind one
    to: the credential stays server-side. A reader who reads every byte their
    browser received must find nothing they could replay."""
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ready = ws.receive_json()

    minted = list(public.sessions._sessions)
    assert len(minted) == 1

    body = json.dumps(ready)
    assert minted[0] not in body
    # Nor the library it belongs to: the browser knows the key it sent and
    # learns nothing more about who answered.
    assert TENANT_A not in body


def test_an_origin_the_library_did_not_register_is_refused(public) -> None:
    key = register_library(TENANT_A, origins=["https://opac.biblioteca.br"])

    with pytest.raises(WebSocketDisconnect) as refusal, open_chat(
        public, key, origin="https://outro-site.example"
    ) as ws:
        ws.receive_json()

    assert refusal.value.code == REFUSED


@pytest.mark.parametrize(
    ("case", "make"),
    [
        ("unknown key", lambda: "wk_nao_existe"),
        ("channel off", lambda: register_library(TENANT_A, enabled=False)),
        ("library suspended", lambda: register_library(TENANT_B, status="suspended")),
    ],
)
def test_every_refusal_closes_the_same_way(public, case, make) -> None:
    """Telling them apart would let someone learn which of those a key is.
    The collapsing happens in the database function, and this asserts nothing
    out here undoes it."""
    key = make()

    with pytest.raises(WebSocketDisconnect) as refusal, open_chat(
        public, key
    ) as ws:
        ws.receive_json()

    assert refusal.value.code == REFUSED, case


# --- what a frame may be ----------------------------------------------------


@pytest.mark.parametrize(
    "frame",
    [
        {"type": "ResponseGenerated", "text": "eu mesmo respondo"},
        {"type": "QueryReceived"},
        {"type": "QueryReceived", "text": "   "},
        {"type": "QueryReceived", "text": 42},
        {"text": "sem tipo"},
        [],
    ],
)
def test_a_frame_that_is_not_a_question_is_answered_and_not_obeyed(
    public, frame
) -> None:
    """The one place a stranger's bytes become an argument to the agent."""
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        ws.send_json(frame)
        assert ws.receive_json()["type"] == "ErrorEvent"


def test_a_question_longer_than_the_cap_is_refused(public) -> None:
    """A socket is an open invitation to send a novel, and the library pays
    for the context."""
    from libvox_core.gateway import MAX_TEXT_CHARS

    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        ws.send_json({"type": "QueryReceived", "text": "a" * (MAX_TEXT_CHARS + 1)})
        assert ws.receive_json()["type"] == "ErrorEvent"


# --- answering, with the model and the tools substituted --------------------


@pytest.fixture
def wired(monkeypatch, public):
    """The real gateway, agent and store; a scripted model and tool layer."""
    from contextlib import asynccontextmanager

    from test_messages import FakeModel, FakeToolBox

    from libvox_core.agent import service

    box = FakeToolBox()
    models: list[FakeModel] = []

    @asynccontextmanager
    # `**_` so a new keyword on `connect` -- the timeout was one --
    # surfaces as a signature error here rather than as a test that
    # waits forever for a frame the gateway turned into an ErrorEvent.
    async def fake_connect(url, token, **_):
        yield box

    monkeypatch.setattr(service, "connect", fake_connect)
    monkeypatch.setattr(service, "Model", lambda *a, **k: models[0])
    return public, box, models


def test_a_tool_call_is_announced_without_its_arguments_or_its_result(
    wired,
) -> None:
    """The honest typing indicator. Arguments carry the reader's own words
    back and a result carries the catalogue; a progress event needs neither,
    and a channel is the wrong place for either to surface."""
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion, ToolIntent

    public, _box, models = wired
    models.append(
        FakeModel(
            Completion(
                intents=[
                    ToolIntent(
                        call_id="c1",
                        name="search_catalog",
                        arguments={"query": "pokemon"},
                    )
                ]
            ),
            Completion(text="Temos vinte."),
        )
    )
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        ws.send_json({"type": "QueryReceived", "text": "vocês têm pokemon?"})

        announced = ws.receive_json()
        answer = ws.receive_json()

    assert announced == {"type": "ToolExecuting", "tool": "search_catalog"}
    # The first answer opened the conversation, so it carries a resume slip --
    # and nothing about the tool call.
    assert isinstance(answer.pop("resume"), str)
    assert answer == {"type": "ResponseGenerated", "text": "Temos vinte."}


def _searched(wired, result: dict) -> dict:
    """One question whose answer came from one search with `result`."""
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion, ToolIntent

    public, box, models = wired
    box._result = result
    models.append(
        FakeModel(
            Completion(
                intents=[
                    ToolIntent(
                        call_id="c1",
                        name="search_catalog",
                        arguments={"query": "newton"},
                    )
                ]
            ),
            Completion(text="Pelo menos cinco."),
        )
    )
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        ws.send_json({"type": "QueryReceived", "text": "livros sobre newton"})
        ws.receive_json()  # ToolExecuting
        return ws.receive_json()


def test_an_answer_with_a_next_page_says_so_and_nothing_more(wired) -> None:
    """The offset stays with the model. A button sends words, not a position."""
    answer = _searched(
        wired,
        {"index_status": "ready", "records": [], "truncated": True,
         "next_offset": 10, "previous_offset": 0},
    )

    assert answer["pages"] == {"next": True}


def test_the_last_page_draws_no_button(wired) -> None:
    """Going back is scrolling up; the earlier pages are already in the log."""
    answer = _searched(
        wired,
        {"index_status": "ready", "records": [], "truncated": False,
         "next_offset": None, "previous_offset": 5},
    )

    assert "pages" not in answer


def test_a_search_that_fit_on_one_page_draws_no_button(wired) -> None:
    answer = _searched(wired, {"index_status": "ready", "records": []})

    assert "pages" not in answer


def test_the_second_question_knows_what_the_first_was_about(wired) -> None:
    """Over one connection, without the browser ever seeing a conversation
    id: the gateway holds it, exactly as ./core.sh ask holds it with --in."""
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion

    public, _box, models = wired
    model = FakeModel(Completion(text="Dom Casmurro."), Completion(text="Sim."))
    models.append(model)
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        ws.send_json({"type": "QueryReceived", "text": "algo do Machado?"})
        ws.receive_json()
        ws.send_json({"type": "QueryReceived", "text": "e esse, tem?"})
        ws.receive_json()

    # The second call carried the first exchange with it.
    second = model.sent[-1]
    said = " ".join(str(m.get("content")) for m in second)
    assert "algo do Machado?" in said
    assert "Dom Casmurro." in said


def test_a_failure_reaches_the_reader_as_a_sentence(wired, caplog) -> None:
    """A 403 from a library's Koha, an unreachable model, a DSN in a
    traceback: the operator's log, never a reader's screen."""
    from test_messages import FakeModel

    public, _box, models = wired

    class Exploding(FakeModel):
        async def complete(self, messages, *, tools=None):
            raise RuntimeError(
                "403 from https://koha.interno:8443 — needs the circulate flag"
            )

    models.append(Exploding())
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        ws.send_json({"type": "QueryReceived", "text": "oi"})
        event = ws.receive_json()

    assert event["type"] == "ErrorEvent"
    for leak in ("403", "koha.interno", "circulate", "RuntimeError"):
        assert leak not in event["message"]


# --- the contract with the internal surface ---------------------------------


def test_the_gateway_mints_into_the_store_the_internal_api_reads(
    public, settings
) -> None:
    """A session opened here is introspected on the internal port -- the call
    /libvox-mcp makes before every tool call."""
    from libvox_core.app import create_app

    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()

        with TestClient(create_app(settings, public.sessions)) as internal:
            token = next(iter(public.sessions._sessions))
            answer = internal.post(
                "/internal/v1/sessions/introspect",
                json={"token": token},
                headers={"x-libvox-service-token": SERVICE_TOKEN},
            ).json()

    assert answer["active"] is True
    assert answer["tenant_id"] == TENANT_A


# --- the thumb --------------------------------------------------------------


def _ask_and_answer(ws, text: str) -> str:
    """Send a question, drain the tool chatter, return the answer's text."""
    ws.send_json({"type": "QueryReceived", "text": text})
    while True:
        frame = ws.receive_json()
        if frame["type"] == "ResponseGenerated":
            return frame["text"]


def _ratings_of() -> list[tuple]:
    """Every rating in the test database, with the text it is attached to."""
    import psycopg
    from conftest import CONVERSATION_ADMIN

    with psycopg.connect(CONVERSATION_ADMIN) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT m.content, r.useful FROM message_ratings r "
            "  JOIN messages m USING (message_id) ORDER BY m.message_id"
        )
        return cur.fetchall()


def test_a_reader_rates_the_answer_they_were_shown(wired) -> None:
    """End to end: two answers, and the second one is the one rated.

    The ordinal has to skip the assistant messages that carry tool calls, which
    is why the first turn calls one and the second does not.
    """
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion, ToolIntent

    public, _box, models = wired
    models.append(
        FakeModel(
            Completion(
                intents=[
                    ToolIntent("c1", "search_catalog", {"query": "pokemon"})
                ]
            ),
            Completion(text="Temos vinte."),
            Completion(text="Nada sobre gatos."),
        )
    )
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        assert _ask_and_answer(ws, "vocês têm pokemon?") == "Temos vinte."
        assert _ask_and_answer(ws, "e gatos?") == "Nada sobre gatos."

        ws.send_json({"type": "AnswerRated", "answer": 2, "useful": False})
        recorded = ws.receive_json()

    assert recorded == {"type": "RatingRecorded", "answer": 2, "useful": False}
    assert _ratings_of() == [("Nada sobre gatos.", False)]


def test_changing_your_mind_replaces_the_thumb_rather_than_leaving_two(
    wired,
) -> None:
    """One row per answer is the schema's decision, and the reason is privacy:
    two rows an hour apart would be a weak fingerprint saying the same person
    was here twice."""
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion

    public, _box, models = wired
    models.append(FakeModel(Completion(text="Temos vinte.")))
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        _ask_and_answer(ws, "vocês têm pokemon?")

        ws.send_json({"type": "AnswerRated", "answer": 1, "useful": False})
        ws.receive_json()
        ws.send_json({"type": "AnswerRated", "answer": 1, "useful": True})
        ws.receive_json()

    assert _ratings_of() == [("Temos vinte.", True)]


def test_an_ordinal_past_what_was_shown_is_refused_and_not_announced(
    wired,
) -> None:
    """A rating the reader did not ask for cannot fail in a way they can act
    on, so nothing is said. A red bubble under an answer they were trying to
    praise reads as the widget breaking.

    Asserted by what arrives next: the answer to the following question, with
    no RatingRecorded in between.
    """
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion

    public, _box, models = wired
    models.append(
        FakeModel(Completion(text="Temos vinte."), Completion(text="E gatos."))
    )
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        _ask_and_answer(ws, "vocês têm pokemon?")

        ws.send_json({"type": "AnswerRated", "answer": 9, "useful": True})
        assert _ask_and_answer(ws, "e gatos?") == "E gatos."

    assert _ratings_of() == []


def test_a_malformed_rating_is_not_mistaken_for_a_question(wired) -> None:
    """`useful` is checked with `is True`/`is False`, not truthiness: "sim" is
    not a boolean and a reader's client does not get to decide that it is. What
    is left is a frame nothing recognises, which is what _NOT_UNDERSTOOD is
    for."""
    public, _box, _models = wired
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        ws.send_json({"type": "AnswerRated", "answer": 1, "useful": "sim"})
        assert ws.receive_json()["type"] == "ErrorEvent"

    assert _ratings_of() == []


def test_the_confirmation_carries_nothing_the_browser_did_not_send(
    wired,
) -> None:
    """The whole point of the ordinal. No message id comes back -- that is a
    global sequence, and it would leak how much the installation has answered
    on top of being an id someone can put in a different frame."""
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion

    public, _box, models = wired
    models.append(FakeModel(Completion(text="Temos vinte.")))
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        _ask_and_answer(ws, "vocês têm pokemon?")
        ws.send_json({"type": "AnswerRated", "answer": 1, "useful": True})
        recorded = ws.receive_json()

    assert set(recorded) == {"type", "answer", "useful"}
    assert "conversation" not in json.dumps(recorded)


# --- resuming on the next page ----------------------------------------------


def _answer(ws, text: str) -> dict:
    """Like `_ask_and_answer`, but the whole ResponseGenerated frame."""
    ws.send_json({"type": "QueryReceived", "text": text})
    while True:
        frame = ws.receive_json()
        if frame["type"] == "ResponseGenerated":
            return frame


def _resume(ws, token: str) -> list[dict]:
    ws.send_json({"type": "ResumeConversation", "token": token})
    frame = ws.receive_json()
    assert frame["type"] == "ConversationResumed"
    assert set(frame) == {"type", "history", "answers"}
    assert frame["answers"] == (
        sum(1 for line in frame["history"] if line["who"] == "them")
        if frame["history"] else 0
    )
    return frame["history"]


def test_only_the_answer_that_opened_the_conversation_carries_a_slip(
    wired,
) -> None:
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion

    public, _box, models = wired
    models.append(FakeModel(Completion(text="Um."), Completion(text="Dois.")))
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        first = _answer(ws, "primeira?")
        second = _answer(ws, "segunda?")

    assert isinstance(first["resume"], str) and len(first["resume"]) >= 40
    assert "resume" not in second


def test_the_next_page_picks_the_conversation_back_up(wired) -> None:
    """The reason the slip exists: a new socket, the same conversation. The
    panel gets back what the reader saw, and the model gets back what it
    said -- so "e esse, tem?" still knows which book."""
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion, ToolIntent

    public, _box, models = wired
    model = FakeModel(
        Completion(
            intents=[ToolIntent("c1", "search_catalog", {"query": "machado"})]
        ),
        Completion(text="Dom Casmurro."),
        Completion(text="Sim, está na estante."),
    )
    models.append(model)
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        slip = _answer(ws, "algo do Machado?")["resume"]

    with open_chat(public, key) as ws:
        ws.receive_json()
        history = _resume(ws, slip)
        later = _answer(ws, "e esse, tem?")

    # What the reader was shown, and nothing the tools returned.
    assert history == [
        {"who": "you", "text": "algo do Machado?"},
        {"who": "them", "text": "Dom Casmurro."},
    ]
    # Still the same conversation: no second slip, and the model saw the first
    # exchange, tool result included.
    assert "resume" not in later
    said = " ".join(str(m.get("content")) for m in model.sent[-1])
    assert "algo do Machado?" in said
    assert "Dom Casmurro." in said


def test_a_rating_after_resuming_lands_on_the_answer_it_names(wired) -> None:
    """The widget keeps counting from the restored answers, and so must the
    server, or the first thumb on the new page rates the old page's answer."""
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion

    public, _box, models = wired
    models.append(FakeModel(Completion(text="Um."), Completion(text="Dois.")))
    key = register_library(TENANT_A, origins=[ORIGIN])

    with open_chat(public, key) as ws:
        ws.receive_json()
        slip = _answer(ws, "primeira?")["resume"]

    with open_chat(public, key) as ws:
        ws.receive_json()
        _resume(ws, slip)
        _answer(ws, "segunda?")
        ws.send_json({"type": "AnswerRated", "answer": 2, "useful": True})
        recorded = ws.receive_json()

    assert recorded == {"type": "RatingRecorded", "answer": 2, "useful": True}
    assert _ratings_of() == [("Dois.", True)]


def _slip_for(wired, key: str) -> str:
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion

    public, _box, models = wired
    models.append(FakeModel(Completion(text="Um.")))
    with open_chat(public, key) as ws:
        ws.receive_json()
        return _answer(ws, "primeira?")["resume"]


def test_every_way_a_slip_fails_looks_the_same(wired) -> None:
    """Another origin of the same library, another library, a made-up slip,
    and a conversation `prune` already deleted: one empty history each, and no
    hint of which. The reader starts afresh; a stranger learns nothing."""
    import psycopg
    from conftest import CONVERSATION_ADMIN

    public, _box, _models = wired
    other = "http://outro.exemplo"
    key = register_library(TENANT_A, origins=[ORIGIN, other])
    key_b = register_library(TENANT_B, origins=[ORIGIN])

    slip = _slip_for(wired, key)

    with open_chat(public, key, origin=other) as ws:
        ws.receive_json()
        assert _resume(ws, slip) == []

    with open_chat(public, key_b) as ws:
        ws.receive_json()
        assert _resume(ws, slip) == []

    with open_chat(public, key) as ws:
        ws.receive_json()
        assert _resume(ws, "x" * 43) == []

    with psycopg.connect(CONVERSATION_ADMIN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM conversations")
    with open_chat(public, key) as ws:
        ws.receive_json()
        assert _resume(ws, slip) == []


def test_a_socket_already_talking_cannot_swap_its_conversation(wired) -> None:
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion

    public, _box, models = wired
    key = register_library(TENANT_A, origins=[ORIGIN])
    slip = _slip_for(wired, key)

    models.clear()
    model = FakeModel(Completion(text="Novo."), Completion(text="Ainda novo."))
    models.append(model)
    with open_chat(public, key) as ws:
        ws.receive_json()
        _answer(ws, "outra coisa?")
        ws.send_json({"type": "ResumeConversation", "token": slip})
        _answer(ws, "e agora?")

    said = " ".join(str(m.get("content")) for m in model.sent[-1])
    assert "outra coisa?" in said
    assert "primeira?" not in said


def test_a_new_conversation_revokes_the_old_slip_at_once(wired) -> None:
    """"Nova conversa" is the reader saying the old one is done. Waiting half
    an hour for the slip to lapse would leave it working in that tab."""
    public, _box, _models = wired
    key = register_library(TENANT_A, origins=[ORIGIN])

    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion

    _models.append(FakeModel(Completion(text="Um.")))
    with open_chat(public, key) as ws:
        ws.receive_json()
        slip = _answer(ws, "primeira?")["resume"]
        ws.send_json({"type": "ForgetConversation"})

    with open_chat(public, key) as ws:
        ws.receive_json()
        assert _resume(ws, slip) == []


def test_no_frame_carries_the_conversation_id(wired) -> None:
    import psycopg
    from conftest import CONVERSATION_ADMIN
    from test_messages import FakeModel

    from libvox_core.agent.llm import Completion

    public, _box, models = wired
    models.append(FakeModel(Completion(text="Um.")))
    key = register_library(TENANT_A, origins=[ORIGIN])

    seen = []
    with open_chat(public, key) as ws:
        seen.append(ws.receive_json())
        seen.append(_answer(ws, "primeira?"))
    with open_chat(public, key) as ws:
        seen.append(ws.receive_json())
        ws.send_json({"type": "ResumeConversation", "token": seen[1]["resume"]})
        seen.append(ws.receive_json())

    with psycopg.connect(CONVERSATION_ADMIN) as conn, conn.cursor() as cur:
        cur.execute("SELECT conversation_id::text FROM conversations")
        (conversation_id,) = cur.fetchone()

    wire = json.dumps(seen)
    assert conversation_id not in wire
    assert conversation_id.replace("-", "") not in wire
