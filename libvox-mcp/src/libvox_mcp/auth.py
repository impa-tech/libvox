"""Tenant propagation.

The single highest-risk path in a multi-tenant deployment: binding a tool call
to exactly one library. Two rules govern everything here.

**The LLM is untrusted input, not a trusted component.** Its context contains
text typed by anonymous members of the public, so tenant identity must never be
expressed in anything the model can read or write — not as a tool argument, not
in the system prompt as a routing key, not in a tool result.

**Headers identify nothing.** The SDK is explicit that request headers are
client-supplied input, fine for a locale or a feature flag and never an
identity. So the session credential travels as an OAuth 2.1 bearer token, and
`/libvox-core` — the only component with Tenant DB access — resolves it.

Both ingress paths converge here: the embedded widget (whose credential Core
mints at the WebSocket handshake) and third-party MCP clients (which
authenticate directly). Either way a tool call arrives carrying a tenant-scoped
credential the model never saw.
"""

from __future__ import annotations

import logging

from mcp import MCPError
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.types import INVALID_REQUEST

from .core_client import CoreClient

logger = logging.getLogger(__name__)

TENANT_CLAIM = "tenant_id"


class LibVoxTokenVerifier(TokenVerifier):
    """Resolves a session credential to a tenant by asking Core.

    Deliberately stateless and cache-free: the tenant is re-resolved on every
    tool call, so a long conversation cannot drift into another tenant's scope
    and a revoked credential stops working immediately.
    """

    def __init__(self, core: CoreClient) -> None:
        self._core = core

    async def verify_token(self, token: str) -> AccessToken | None:
        session = await self._core.introspect_session(token)
        if session is None:
            return None

        tenant_id = session.get(TENANT_CLAIM)
        if not tenant_id:
            # A credential Core considers active but that names no tenant is a
            # bug upstream, not an anonymous caller. Refuse it.
            logger.error("core returned an active session with no tenant_id")
            return None

        return AccessToken(
            token=token,
            client_id=str(session.get("client_id") or tenant_id),
            scopes=list(session.get("scopes") or []),
            expires_at=session.get("expires_at"),
            subject=session.get("subject"),
            claims={TENANT_CLAIM: str(tenant_id)},
        )


def require_tenant() -> str:
    """Return the calling tenant, or refuse the request.

    Raises `MCPError` rather than an ordinary exception on purpose: a missing
    or malformed credential is not something a smarter model could have
    avoided, so it belongs to the host application, not to the conversation.
    Raising it also keeps the failure out of the model's context, where a
    message about tenant resolution would be an invitation to probe.
    """
    token = get_access_token()
    if token is None:
        raise MCPError(
            code=INVALID_REQUEST,
            message="This request carries no session credential.",
        )

    tenant_id = (token.claims or {}).get(TENANT_CLAIM)
    if not tenant_id:
        raise MCPError(
            code=INVALID_REQUEST,
            message="This session credential is not bound to a library.",
        )
    return str(tenant_id)
