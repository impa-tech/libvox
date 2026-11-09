"""The four routes.

Three are the contract `/libvox-mcp` already calls. The fourth mints the
credential the other three depend on — the README's WebSocket handshake with
the WebSocket removed, so the channel gateway will wrap this rather than
replace it.
"""

from __future__ import annotations

import logging

import psycopg
from fastapi import Depends, FastAPI, HTTPException
from pydantic import BaseModel, Field

from .config import Settings
from .sessions import SessionStore

logger = logging.getLogger(__name__)


class MintRequest(BaseModel):
    channel: str = "web"
    binding_key: str
    origin: str | None = None


class IntrospectRequest(BaseModel):
    token: str


class IlsCheckRequest(BaseModel):
    tenant_id: str


class SearchRequest(BaseModel):
    tenant_id: str
    query: str = ""
    # Always sent, and nullable. The client fills these in explicitly rather
    # than omitting them, so the schema has to accept an explicit null.
    author: str | None = None
    title: str | None = None
    # A range over the index's `copyright_date`, either half valid alone. The
    # year is not in the text vector, so this is the only way to ask for one.
    year_from: int | None = None
    year_to: int | None = None
    limit: int = Field(default=5, ge=1, le=20)


# How many records one availability call may ask about.
#
# **The one bound in this system that protects somebody else.** `limit` above
# caps a search, which reaches nothing but our own index; this caps a list that
# becomes one live HTTP request per entry against a library's own Koha. It is
# what stands between a curious model and the rule the architecture is built on:
# LibVox must never be the reason a library's OPAC goes down.
#
# It was unbounded here, with the only ceiling twenty layers away in
# /libvox-mcp -- so a caller reaching this API directly had none at all. Asked
# one question, a model checked every record its search returned: twenty
# sequential GETs to another institution's server over thirteen seconds, for an
# answer that named a handful.
#
# Kept in step with /libvox-mcp's constant of the same name by hand, since Core
# cannot import from a module that depends on it. That layer *clamps* to this
# number instead of refusing, because a validation error there costs the model a
# round of the loop. Here it refuses: a service sending more has a bug, and 422
# is the honest answer to one.
MAX_AVAILABILITY_IDS = 5


class AvailabilityRequest(BaseModel):
    tenant_id: str
    biblio_ids: list[int] = Field(min_length=1, max_length=MAX_AVAILABILITY_IDS)


class MessageRequest(BaseModel):
    """The one inbound shape, from every channel.

    Nothing here names a platform, which is the point: the Core cannot behave
    differently for WhatsApp than for the widget because it cannot tell them
    apart. A new channel is a new adapter, never a branch in here.
    """

    session_id: str
    """The session token. A channel gateway will hold this server-side and
    never send it to a browser; it arrives in the body because there is no
    transport yet to hold it in."""

    text: str = Field(min_length=1)
    locale: str | None = None
    capabilities: list[str] = Field(default_factory=list)

    conversation_id: str | None = None
    """Continues an existing conversation. Absent starts one."""


def register_routes(
    app: FastAPI,
    *,
    settings: Settings,
    sessions: SessionStore,
    guard: Depends,
) -> None:
    import uuid

    from libvox_sync.embedding import Embedder, EmbeddingSettings
    from libvox_sync.search import search as index_search

    from .agent.service import Inbound, respond
    from .availability import AvailabilityUnknown, availability_for
    from .contracts import (
        AvailabilityRecord,
        CatalogRecord,
        CheckAvailabilityResult,
        IlsCheckResult,
        SearchCatalogResult,
    )
    from .tenants import resolve_channel, widget_config

    def discovery() -> psycopg.Connection:
        return psycopg.connect(settings.discovery_dsn)

    # One client for the process, not one per question: the connection to the
    # embedding service is reused across searches the way the pool would be.
    # Unset configuration leaves it `configured = False`, the vector arm never
    # runs, and search answers from the lexical index exactly as it did before.
    #
    # It lives here rather than in /libvox-sync's own configuration because
    # this is where the query side runs -- `index_search` executes inside this
    # process, against this connection.
    embedder = Embedder(EmbeddingSettings.from_env())

    def tenant_db() -> psycopg.Connection:
        return psycopg.connect(settings.tenant_dsn)

    # --- the session lifecycle ---------------------------------------------

    @app.post("/internal/v1/sessions", dependencies=[guard])
    async def mint_session(request: MintRequest) -> dict[str, object]:
        """Bind a session to one library, from a public channel key.

        The widget key in an OPAC page identifies; it does not authenticate.
        What makes the binding trustworthy is the Origin check, and both happen
        inside `resolve_channel_binding` where a caller cannot separate them.
        """
        with tenant_db() as conn:
            tenant_id = resolve_channel(
                conn, request.channel, request.binding_key, request.origin
            )
            if tenant_id is None:
                # One answer for every refusal. See tenants.resolve_channel.
                logger.info(
                    "refused a %s binding attempt from origin %r",
                    request.channel,
                    request.origin,
                )
                return {"active": False}

            widget = widget_config(conn, tenant_id)

        session = sessions.mint(tenant_id, channel=request.channel)
        return {
            "token": session.token,
            "tenant_id": session.tenant_id,
            "expires_at": session.expires_at,
            "scopes": list(session.scopes),
            # The widget's own configuration travels with the handshake, which
            # is what `SessionReadyEvent` will carry. The credential does not
            # travel to the browser -- it stays server-side, bound to the
            # connection, once there is a connection to bind it to.
            "widget": widget,
        }

    @app.post("/internal/v1/sessions/introspect")
    async def introspect(request: IntrospectRequest) -> dict[str, object]:
        """Resolve a credential to its library. Called on **every** tool call.

        No service-token guard on this one, deliberately: it is the token
        verifier's path, it reveals nothing about a token it does not already
        hold, and gating it would turn a missing service token into "every
        caller is anonymous" instead of a startup failure.

        Always 200, always JSON. The client does not wrap `response.json()` in
        anything, so a non-JSON 2xx from here kills the verifier outright.
        """
        session = sessions.resolve(request.token)
        if session is None:
            return {"active": False}
        return session.introspection()

    # --- the tools ---------------------------------------------------------

    @app.post(
        "/internal/v1/ils/check",
        response_model=IlsCheckResult,
        dependencies=[guard],
    )
    async def check_ils(request: IlsCheckRequest) -> IlsCheckResult:
        """Try this library's catalogue connection, and say what happened.

        `/libvox-portal` asks, because it cannot answer: its credential store
        has no read path, by design and by test. This is the component the
        architecture names as the one that may decrypt, so the question comes
        here rather than the property being given up there.

        Always 200. A failed handshake is an answer -- the whole point is to
        say *which* of unreachable, rejected or unpermitted it was -- and a
        status code would collapse the three into "something went wrong" on the
        way through a client that turns every 4xx into one sentence.
        """
        from .ils import check as check_connection

        return check_connection(settings, request.tenant_id)

    @app.post(
        "/internal/v1/search_catalog",
        response_model=SearchCatalogResult,
        dependencies=[guard],
    )
    async def search_catalog(request: SearchRequest) -> SearchCatalogResult:
        """Answer from the Discovery Index. **Never from the ILS.**

        Not even when nothing matches. Falling back to a live query on a miss
        was considered and rejected in the architecture: it would break the
        guarantee that search traffic never reaches the library's server, and a
        still-filling index is exactly when that fallback would generate the
        heaviest load. An empty result with `index_status` is the answer.
        """
        with discovery() as conn:
            result = index_search(
                conn,
                request.tenant_id,
                request.query,
                author=request.author,
                title=request.title,
                year_from=request.year_from,
                year_to=request.year_to,
                limit=request.limit,
                embedder=embedder,
            )

        return SearchCatalogResult(
            index_status=result.index_status,
            records=[CatalogRecord(**record) for record in result.records],
            truncated=result.truncated,
            undated_records=result.undated_records,
            searched_for=result.searched_for,
        )

    @app.post(
        "/internal/v1/check_availability",
        response_model=CheckAvailabilityResult,
        dependencies=[guard],
    )
    async def check_availability(
        request: AvailabilityRequest,
    ) -> CheckAvailabilityResult:
        """Live circulation status, for ids this library actually has.

        Propagation rule 5: an id absent from the tenant's index is dropped
        before anything reaches the ILS. That is the defence against a
        hallucinated or injected id being probed against a library's server —
        and dropping it is also the contract, which says a record the library
        does not hold is omitted rather than reported unavailable.
        """
        try:
            records = availability_for(
                settings, request.tenant_id, request.biblio_ids
            )
        except AvailabilityUnknown as exc:
            # 503 rather than an empty list. The MCP client collapses any
            # non-200 into one opaque sentence for the model, which is exactly
            # right here: it learns the catalog could not answer, instead of
            # reading an empty list as "the library has none of these".
            logger.warning("%s: %s", request.tenant_id, exc)
            raise HTTPException(
                status_code=503, detail="the catalog could not be reached"
            ) from None

        return CheckAvailabilityResult(
            records=[AvailabilityRecord(**record) for record in records]
        )

    # --- the conversation ---------------------------------------------------

    @app.post("/internal/v1/messages", dependencies=[guard])
    async def message(request: MessageRequest) -> dict[str, object]:
        """Answer one message from a patron.

        The session is resolved here and nowhere else in this path: the library
        comes from the credential, never from the body, and the model is never
        told which library it is serving.
        """
        session = sessions.resolve(request.session_id)
        if session is None:
            # The same shape a bad credential gets everywhere in this service.
            # A 401 with a reason would tell a caller whether a token ever
            # existed.
            return {"active": False}

        conversation_id = None
        if request.conversation_id:
            try:
                conversation_id = uuid.UUID(request.conversation_id)
            except ValueError:
                return {"active": False}

        result, conversation_id = await respond(
            settings,
            Inbound(
                session=session,
                text=request.text,
                locale=request.locale,
                capabilities=tuple(request.capabilities),
                conversation_id=conversation_id,
            ),
        )

        return {
            "text": result.text,
            "conversation_id": str(conversation_id),
            "truncated": result.truncated,
            # What was called, not what came back: the records are already in
            # the answer, and repeating them here would be a second copy for a
            # channel to accidentally render.
            "tool_calls": [
                {"name": call.name, "failed": call.failed}
                for call in result.tool_calls
            ],
        }
