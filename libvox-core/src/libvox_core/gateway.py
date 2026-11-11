"""The web channel: one WebSocket per reader, and the events it speaks.

This is the first surface in LibVox a member of the public touches, and the
whole design follows from one sentence already written in `routes.py`, back
when there was no connection to bind a credential to:

    "The credential does not travel to the browser -- it stays server-side,
    bound to the connection, once there is a connection to bind it to."

This is that connection. The browser sends a **public widget key** and its
`Origin`; the session token is minted here, held in a local variable for the
life of the socket, and never serialized into a frame. A reader who reads every
byte their browser received learns nothing they could replay elsewhere.

**One thing does reach the browser on purpose: a resume slip**, so the next
page of the OPAC can pick the conversation back up. It is not the session token
and not the conversation id, and `resume.py` says what it is bound to and why.

**What this module is not.** No agent, no tool registry, no prompt, no
credential for a library's ILS. It translates a frame into `Inbound` and an
answer into frames. The root README's test for whether the boundary sits in the
right place is whether adding a channel means touching the agent; if this file
ever needs to, the answer is no and the boundary moved.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

import psycopg
from fastapi import FastAPI, Query, WebSocket, WebSocketDisconnect

from .agent.service import Inbound, respond
from .config import Settings
from .resume import MAX_TOKEN_CHARS, ResumeStore
from .sessions import SessionStore
from .links import Builder, record_links, remember, remember_records
from .tenants import opac_links, resolve_channel, widget_config

logger = logging.getLogger(__name__)

# 1008 is "policy violation", and it is the *only* code a refused handshake
# uses. An unknown key, an origin the library did not register, a channel
# switched off and a suspended library are one answer here for the same reason
# they are one answer in the database function that decides them: telling them
# apart lets someone learn which of those a given key is.
REFUSED = 1008

# Said to the reader, never a status code or a URL. The operator's log carries
# the reason; this is the whole of what a person is told.
_SESSION_ENDED = (
    "Esta conversa expirou. Recarregue a página para começar outra."
)
_SOMETHING_BROKE = (
    "Não consegui responder agora. Tente de novo em um instante."
)
_NOT_UNDERSTOOD = "Não entendi essa mensagem."

MAX_ANSWERS = 500
"""The largest ordinal a rating may name.

Not a conversation length limit -- it is the bound on an OFFSET a stranger
controls. A socket that has shown three answers cannot honestly rate the
four-hundredth, and `_record_rating` checks that too; this is the cheaper
refusal, before a query is built at all."""

MAX_TEXT_CHARS = 2_000
"""A question longer than this is not a question. The model's context is paid
for by the library, and a socket is an open invitation to send a novel."""


def register_gateway(
    app: FastAPI,
    *,
    settings: Settings,
    sessions: SessionStore,
    resumes: ResumeStore | None = None,
) -> None:
    """Mount the web channel on the public application."""
    if resumes is None:
        resumes = ResumeStore(ttl_s=settings.resume_ttl_s)

    @app.websocket("/ws/v1/chat")
    async def chat(
        websocket: WebSocket,
        key: str = Query(..., min_length=1, max_length=200),
    ) -> None:
        # The browser sends this and cannot lie about it; `curl` can. That is
        # fine and worth being clear-eyed about: Origin stops another site from
        # opening a session in a reader's browser, and stops nothing else. What
        # a session reaches is one library's public catalogue.
        origin = websocket.headers.get("origin")

        with psycopg.connect(settings.tenant_dsn) as conn:
            tenant_id = resolve_channel(conn, "web", key, origin)
            if tenant_id is None:
                # Logged with the origin, answered without it.
                logger.info("refused a web binding attempt from origin %r", origin)
                await websocket.close(code=REFUSED)
                return
            widget = widget_config(conn, tenant_id)
            # Read fresh at connect, like the widget settings: a librarian who
            # fills in the OPAC address sees links from the next page on.
            link = opac_links(conn, tenant_id)

        # Minted after the binding is proven, held here, and never sent. The
        # gateway cannot call /internal/v1/sessions for this -- that route is
        # behind the service token -- which is what "the gateway wraps the
        # handshake" means in practice.
        session = sessions.mint(tenant_id, channel="web")

        await websocket.accept()
        logger.info("web session opened for %s", tenant_id)

        await websocket.send_json(
            {
                "type": "SessionReadyEvent",
                # The library's own voice, read fresh at connect. `persona_tone`
                # stays behind: it shapes the prompt and is not the widget's
                # business.
                "assistant_name": widget.get("assistant_name"),
                "welcome_message": widget.get("welcome_message"),
                "theme_color": widget.get("theme_color"),
                # `locale`, not `default_locale`: widget_config renames the
                # column on the way out. Reading the column name here answered
                # None every time, and None is also what a library with no
                # preference looks like -- so it was wrong and invisible.
                "locale": widget.get("locale"),
            }
        )

        # Held here rather than sent, for the same reason as the token: the
        # reader's browser has no use for it, and a conversation id in a frame
        # is an id someone can put in a different frame.
        conversation_id: uuid.UUID | None = None

        # How many answers this socket has shown. The widget counts the same
        # thing, and a rating names one of them by its ordinal -- "the third" --
        # because that is what a reader can honestly point at and it hands the
        # browser nothing. A `message_id` would be worse than the conversation
        # id above: it is a global sequence, so it leaks how much the whole
        # installation has answered.
        #
        # Sound only because every path that answers stores exactly one
        # assistant message. test_loop.py pins that; one path did not, and the
        # gap put nineteen conversations one turn out of step before anything
        # needed to count.
        answers = 0

        # The slip this socket's conversation can be resumed with on the next
        # page -- see `resume.py`. Issued with the answer that opened the
        # conversation, or redeemed from the browser; never the conversation
        # id itself.
        slip: str | None = None

        # biblio_id -> title for every record this socket's searches returned,
        # so a later answer that only checked availability can still link the
        # book it names. Titles and ids the reader's answers were already made
        # from; nothing here goes to the browser except as a link.
        seen: dict[int, str] = {}

        try:
            while True:
                frame = await websocket.receive_json()

                # Only before the first question: a socket already holding a
                # conversation does not get to swap it for another.
                presented = _resume(frame)
                if presented is not None:
                    if conversation_id is None and answers == 0:
                        conversation_id, answers, history, pages = _resumed(
                            settings,
                            resumes,
                            presented,
                            tenant_id,
                            origin,
                            link,
                            seen,
                        )
                        if conversation_id is not None:
                            slip = presented
                        # `answers` because `history` is capped: the widget
                        # numbers ratings from the whole conversation, not
                        # from what fits on screen.
                        resumed: dict[str, Any] = {
                            "type": "ConversationResumed",
                            "history": history,
                            "answers": answers,
                        }
                        # Under the last line only, exactly as it was drawn
                        # before the page changed.
                        if pages:
                            resumed["pages"] = pages
                        await websocket.send_json(resumed)
                    continue

                if _forget(frame):
                    # "Nova conversa". The slip stops working now rather than
                    # in half an hour; the conversation stays in the log, under
                    # the retention it already had.
                    if slip is not None:
                        resumes.revoke(slip)
                    slip = None
                    conversation_id = None
                    answers = 0
                    continue

                # Before `_question`, because that function is the whole of the
                # inbound grammar: anything it does not recognise becomes "não
                # entendi essa mensagem", and a rating would land there.
                rating = _rating(frame)
                if rating is not None:
                    await _record_rating(
                        websocket,
                        settings,
                        tenant_id,
                        conversation_id,
                        answers,
                        rating,
                    )
                    continue

                text = _question(frame)
                if text is None:
                    await _error(websocket, _NOT_UNDERSTOOD)
                    continue

                if not session.is_active():
                    # Said, not swallowed. A widget that goes quiet after an
                    # hour looks broken; one that says the conversation ended
                    # is a widget the reader knows how to fix.
                    logger.info("session for %s expired mid-conversation", tenant_id)
                    await _error(websocket, _SESSION_ENDED)
                    await websocket.close(code=REFUSED)
                    return

                async def announce(tool_name: str) -> None:
                    """The honest typing indicator: which tool, and nothing
                    about it. Arguments carry the reader's own words back, and
                    a result carries the catalogue -- neither belongs in a
                    progress event."""
                    await websocket.send_json(
                        {"type": "ToolExecuting", "tool": tool_name}
                    )

                opened = conversation_id is None
                try:
                    answer, conversation_id = await respond(
                        settings,
                        Inbound(
                            session=session,
                            text=text,
                            locale=widget.get("locale"),  # type: ignore[arg-type]
                            capabilities=(),
                            conversation_id=conversation_id,
                        ),
                        on_tool=announce,
                    )
                except WebSocketDisconnect:
                    raise
                except Exception:
                    # The reason goes to the operator's log and no further. A
                    # 403 from a library's Koha, an unreachable model, a DSN in
                    # a traceback: none of it is a reader's to see.
                    logger.exception("could not answer for %s", tenant_id)
                    await _error(websocket, _SOMETHING_BROKE)
                    continue

                generated: dict[str, Any] = {
                    "type": "ResponseGenerated",
                    "text": answer.text,
                }
                if pages := _pages(answer.tool_calls):
                    generated["pages"] = pages
                if link is not None:
                    remember(seen, answer.tool_calls)
                    if links := record_links(answer.text, seen, link):
                        generated["links"] = links
                if opened and conversation_id is not None:
                    slip = resumes.issue(tenant_id, origin, conversation_id)
                    generated["resume"] = slip
                elif slip is not None:
                    resumes.touch(slip)
                await websocket.send_json(generated)
                answers += 1
        except WebSocketDisconnect:
            # Not an error. The conversation is already stored turn by turn, so
            # closing a tab loses the socket and nothing else.
            logger.info("web session closed for %s", tenant_id)


def _resume(frame: object) -> str | None:
    """The slip a `ResumeConversation` frame presents, or None.

    Strict for the same reason `_question` is. The length cap is not
    validation of the slip -- `ResumeStore` is that -- it keeps an arbitrary
    string from becoming a dictionary key.
    """
    if not isinstance(frame, dict) or frame.get("type") != "ResumeConversation":
        return None
    token = frame.get("token")
    if not isinstance(token, str) or not (0 < len(token) <= MAX_TOKEN_CHARS):
        return None
    return token


def _forget(frame: object) -> bool:
    return isinstance(frame, dict) and frame.get("type") == "ForgetConversation"


def _resumed(
    settings: Settings,
    resumes: ResumeStore,
    token: str,
    tenant_id: str,
    origin: str | None,
    link: Builder | None = None,
    seen: dict[int, str] | None = None,
) -> tuple[
    uuid.UUID | None, int, list[dict[str, Any]], dict[str, bool] | None
]:
    """`(conversation, answers so far, what the reader was shown, pages)`.

    With a link builder, the restored answers carry their links again, built
    from the searches in the log and the library's OPAC address as it is now;
    and `seen` is filled, so the socket links those books in later answers too.

    `(None, 0, [], None)` for every way a slip can fail -- unknown, expired, revoked,
    issued to another library or another origin, or pointing at a conversation
    `prune` has since deleted. The browser gets the same empty history for all
    of them and starts afresh; telling them apart would tell a stranger which
    one a slip they hold is.
    """
    from .agent import store

    conversation_id = resumes.redeem(token, tenant_id, origin)
    if conversation_id is None:
        return None, 0, [], None

    try:
        with psycopg.connect(settings.conversation_dsn) as log:
            transcript = store.transcript(log, tenant_id, conversation_id)
    except psycopg.Error:
        logger.exception("could not resume a conversation for %s", tenant_id)
        return None, 0, [], None

    if transcript is None:
        resumes.revoke(token)
        logger.info("a resume slip for %s outlived its conversation", tenant_id)
        return None, 0, [], None

    logger.info("resumed a web conversation for %s", tenant_id)
    known: dict[int, str] = {} if seen is None else seen
    if link is not None:
        remember_records(known, transcript.records)

    history: list[dict[str, Any]] = []
    for role, text in transcript.lines:
        line: dict[str, Any] = {"who": "you" if role == "user" else "them", "text": text}
        if role != "user" and link is not None:
            if links := record_links(text, known, link):
                line["links"] = links
        history.append(line)
    pages = {"next": True} if transcript.has_next else None
    return conversation_id, transcript.answers, history, pages


def _pages(tool_calls: list) -> dict[str, bool] | None:
    """Whether the answer's search has a page after this one.

    A boolean and nothing else: it is what the widget needs to draw "Próximos
    ›", and the button sends an ordinary question in the reader's words. The
    offset stays with the model, which already has it -- in a frame it would be
    one more number a stranger could edit and send back, for nothing the
    reader can see.

    Only forward. The earlier pages are already in the log above, and
    scrolling up is the way back; a reader who asks for them in words still
    gets them, through `previous_offset`.

    The **last** search counts, because a turn that searched twice answered
    from the second one. None when the turn did not search, or when its search
    has nothing after this page, so a frame with no button carries no field.
    """
    for call in reversed(tool_calls):
        if call.name != "search_catalog" or call.failed:
            continue
        result = call.result or {}
        return {"next": True} if result.get("next_offset") is not None else None
    return None


def _question(frame: object) -> str | None:
    """The reader's words, or None if the frame was not one.

    Deliberately strict about the shape and forgiving about nothing: this is
    the one place a stranger's bytes become an argument to the agent.
    """
    if not isinstance(frame, dict) or frame.get("type") != "QueryReceived":
        return None
    text = frame.get("text")
    if not isinstance(text, str):
        return None
    text = text.strip()
    if not text or len(text) > MAX_TEXT_CHARS:
        return None
    return text


def _rating(frame: object) -> tuple[int, bool] | None:
    """`(which answer, was it useful)`, or None if the frame was not a rating.

    Strict about the shape for the same reason `_question` is. Two details that
    are not obvious: `useful` is checked with `is True`/`is False` rather than
    truthiness, because `1` and `"yes"` are not booleans and a reader's client
    should not get to decide that they are; and the ordinal is capped, because
    an unbounded one becomes an `OFFSET` the database is asked to walk.
    """
    if not isinstance(frame, dict) or frame.get("type") != "AnswerRated":
        return None

    answer = frame.get("answer")
    useful = frame.get("useful")
    if not isinstance(answer, int) or isinstance(answer, bool):
        return None
    if not (1 <= answer <= MAX_ANSWERS):
        return None
    if useful is not True and useful is not False:
        return None
    return answer, useful


async def _record_rating(
    websocket: WebSocket,
    settings: Settings,
    tenant_id: str,
    conversation_id: uuid.UUID | None,
    answers: int,
    rating: tuple[int, bool],
) -> None:
    """Store a thumb, and say so, or say nothing at all.

    **No session check.** A rating names a message in the conversation this
    socket is already holding, reaches no library data, and costs no model
    call. Refusing an expired one would lose the thumb on the answer that made
    the reader wait, which is the one worth having.

    **No error frame when it does not land.** A rating the reader did not ask
    for cannot fail in a way they can act on, and a red bubble under an answer
    they were trying to praise reads as the widget breaking. It goes to the
    operator's log instead — where a widget counting answers the server never
    said is a bug worth seeing.

    Nothing here is rate-limited, and it does not need to be: the primary key is
    the message, so the worst a socket can do by repeating itself is rewrite its
    own rows.
    """
    from .agent import store

    which, useful = rating

    if conversation_id is None or which > answers:
        logger.info(
            "%s rated answer %d of %d shown; nothing to attach it to",
            tenant_id,
            which,
            answers,
        )
        return

    try:
        with psycopg.connect(settings.conversation_dsn) as log:
            store.rate(
                log, tenant_id, conversation_id, answer=which, useful=useful
            )
    except (store.NoSuchAnswer, psycopg.Error):
        logger.exception("could not record a rating for %s", tenant_id)
        return

    await websocket.send_json(
        {"type": "RatingRecorded", "answer": which, "useful": useful}
    )


async def _error(websocket: WebSocket, message: str) -> None:
    await websocket.send_json({"type": "ErrorEvent", "message": message})
