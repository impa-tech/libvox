"""The HTTP surface `/libvox-mcp` calls.

Four routes, and the contract for three of them is not ours to choose:
`libvox_mcp/core_client.py` already exists, is tested, and is therefore the
specification. Two of its properties shape everything here.

**A status code carries nothing.** The client collapses every 4xx and 5xx into
one opaque message for the model. So a condition the model must be able to act
on is a **200 with a field**, never a status code — which is exactly why
`index_status` exists rather than a 503 for an index still filling.

**Introspection never raises.** An unreachable Core degrades to "nobody is
authenticated", not to an error, and every 2xx on that path must be JSON or the
token verifier dies decoding it.
"""

from __future__ import annotations

import logging
from pathlib import Path

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse

from .config import Settings
from .sessions import SessionStore

logger = logging.getLogger(__name__)

TITLE = "LibVox Core"


def create_app(
    settings: Settings | None = None, sessions: SessionStore | None = None
) -> FastAPI:
    """Build the internal application — the one `/libvox-mcp` calls.

    A factory rather than a module-level singleton, so tests run against an
    isolated instance and a host embedding LibVox can supply its own settings.

    `sessions` is a parameter because the public app mints into the same store.
    A session opened on the WebSocket has to be introspectable here: that is
    the call `/libvox-mcp` makes before every tool call. Two stores would be
    two ideas of who is connected, and the reader's would be the one nobody
    could verify.
    """
    settings = settings or Settings.from_env()
    # `is None`, not `or`: SessionStore defines __len__, so a store with nobody
    # signed in yet is falsy and `or` would quietly build a second one. Both
    # apps would then be given a store, and neither would be the same store.
    if sessions is None:
        sessions = SessionStore(ttl_s=settings.session_ttl_s)

    app = FastAPI(
        title=TITLE,
        description=(
            "The tenant boundary. Implements the tools /libvox-mcp declares, "
            "resolving each call against exactly one library."
        ),
        version="0.1.0",
        # No interactive docs. This surface selects a tenant from its request
        # body and must never be somewhere a browser goes looking.
        #
        # `openapi_url` too, which was the hole: /openapi.json answered 200
        # with no service token and enumerated every route and schema on it.
        # Turning off the two HTML pages and leaving the machine-readable one
        # is turning off the sign and leaving the door.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.sessions = sessions

    def require_service_token(
        x_libvox_service_token: str | None = Header(default=None),
    ) -> None:
        """Authenticate the calling service. Never a tenant.

        The tenant arrives in the request body, already resolved by the MCP
        layer from the caller's own credential. So this token says *which
        service is asking*, and its absence in a real deployment means anything
        that can reach the port can read any library's data -- which is why
        `Settings.from_env` refuses to start without one outside development.
        """
        expected = settings.service_token
        if expected is None:
            return
        if x_libvox_service_token != expected:
            raise HTTPException(status_code=401, detail="bad service token")

    internal = Depends(require_service_token)

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        """Liveness, and nothing else.

        Deliberately says nothing about the databases: a health check that
        reports which dependency is down is a reconnaissance endpoint, and this
        one answers before any authentication.
        """
        return {"status": "ok", "service": "libvox-core"}

    from .routes import register_routes

    register_routes(app, settings=settings, sessions=sessions, guard=internal)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        """Never let an exception message reach the caller.

        A traceback here would carry DSNs, an ILS URL, or a fragment of a
        query. The client turns any 5xx into one opaque sentence for the model
        anyway; this makes sure the body it discards was never sensitive.
        """
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    return app


def create_public_app(
    settings: Settings | None = None, sessions: SessionStore | None = None
) -> FastAPI:
    """Build the browser-facing application.

    A second ASGI app rather than a second router on the first one, and the
    difference is the whole point. `/internal/v1` is not unrouted here; **it
    does not exist here.** That surface takes `tenant_id` from its request body
    -- anything that reaches it reads any library -- and the module says so out
    loud in `create_app`: it "must never be somewhere a browser goes looking".

    Making that structural rather than configured means a misconfigured reverse
    proxy cannot undo it. The failing test is one line: a request for an
    internal path here answers 404.

    No service token guards anything on this app. The credential a reader's
    connection carries is minted server-side from a public widget key and an
    Origin, and never leaves the process -- see `gateway.py`.
    """
    settings = settings or Settings.from_env()
    # `is None`, not `or`: SessionStore defines __len__, so a store with nobody
    # signed in yet is falsy and `or` would quietly build a second one. Both
    # apps would then be given a store, and neither would be the same store.
    if sessions is None:
        sessions = SessionStore(ttl_s=settings.session_ttl_s)

    app = FastAPI(
        title=f"{TITLE} (public)",
        description="The web channel: a WebSocket per reader, and the widget.",
        version="0.1.0",
        # For the same reason as the internal app, and more so: this one is
        # reachable from a browser by design.
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings
    app.state.sessions = sessions

    @app.get("/healthz")
    async def healthz() -> dict[str, object]:
        """Liveness, and nothing else. Says nothing about any dependency."""
        return {"status": "ok", "service": "libvox-core-public"}

    from fastapi.responses import FileResponse

    from .gateway import register_gateway

    register_gateway(app, settings=settings, sessions=sessions)

    widget = Path(__file__).parent / "static" / "widget.js"

    @app.get("/widget.js")
    async def widget_js() -> FileResponse:
        """The browser client, served from the same origin as its socket.

        Not from /libvox-portal, deliberately. The portal is the management
        plane; putting it in the path of every OPAC page view would mean the
        portal going down takes every library's widget with it. This host has
        to be up anyway -- there is nothing to talk to otherwise.

        One file for every library. Nothing here is per-tenant: the key comes
        from the tag, and the library's own name, colour and greeting arrive
        over the socket after the server has decided whose key it is.
        """
        return FileResponse(
            widget,
            media_type="application/javascript",
            headers={"Cache-Control": "public, max-age=300"},
        )

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        """A traceback out here would reach a member of the public."""
        logger.exception("unhandled error on %s", request.url.path)
        return JSONResponse(status_code=500, content={"detail": "internal error"})

    return app
