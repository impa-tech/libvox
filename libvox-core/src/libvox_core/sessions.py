"""Session credentials: opaque, short-lived, bound to exactly one library.

This is propagation rule 7, and the shape of it is the whole point. A session
credential says *which library* a tool call belongs to, and it says nothing
else — the model never sees it, the conversation never carries it, and no
operation re-scopes one to a different library.

Rule 3 lives here too, by omission: there is no cache anywhere on the read
path. `/libvox-mcp` introspects on every single tool call and caches nothing,
and Core answers from the store every time. A long conversation therefore
cannot drift into another tenant's scope, and a revoked credential stops
working on the next call rather than when something expires.

**In memory, and only for now.** The architecture makes Core a read-only
consumer of the Tenant DB — /libvox-portal is its one writer — so sessions
cannot be persisted there without giving that database a second author. Redis
is what the root README earmarks for the genuinely ephemeral, and it is not
deployed. So: a dict, with the consequence said out loud rather than
discovered. **Restarting Core logs everyone out**, and running two Core
processes behind a load balancer would give each its own idea of who is signed
in. The interface is a store so that swapping in Redis is one class.
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# 32 bytes from the CSPRNG, base64url. `secrets`, not `random`, and spelled out
# at the call site rather than hidden behind a helper: this is the one value in
# the system whose unguessability is the whole security property.
_TOKEN_BYTES = 32

DEFAULT_SCOPES = ("catalog:read",)
"""What a session may do. `/libvox-mcp` refuses any tool call whose credential
lacks `catalog:read`, so this is not decoration -- it is the scope check on the
other side of the wire."""


@dataclass(frozen=True, slots=True)
class Session:
    """One bound session. `tenant_id` is set once, at minting, and never again."""

    token: str
    tenant_id: str
    expires_at: int
    scopes: tuple[str, ...] = DEFAULT_SCOPES
    channel: str = "web"

    def is_active(self, *, now: float | None = None) -> bool:
        return (now if now is not None else time.time()) < self.expires_at

    def introspection(self) -> dict[str, object]:
        """The body `/libvox-mcp`'s token verifier reads.

        Flat, not nested: it reads `tenant_id` from the top level. And it is
        deliberately not RFC 7662 -- that spec wants a form-encoded request and
        a space-delimited `scope` string, while this takes JSON and returns a
        `scopes` array. Building it from the RFC produces something the client
        cannot read.
        """
        return {
            "active": True,
            "tenant_id": self.tenant_id,
            "client_id": self.tenant_id,
            "scopes": list(self.scopes),
            "expires_at": self.expires_at,
            # No `subject`. Conversations are scoped to a session and never to
            # a person -- LibVox does not store patron identity, and a subject
            # claim is exactly where one would start to accumulate.
        }


@dataclass
class SessionStore:
    """Minting, lookup and revocation. No re-scoping, by construction."""

    ttl_s: int = 3600
    _sessions: dict[str, Session] = field(default_factory=dict)

    def mint(self, tenant_id: str, *, channel: str = "web") -> Session:
        """Create a credential for one library.

        There is no `retenant`, no `update`, and no setter: rebinding an
        existing credential to a different library is not a slow path here, it
        is an operation that does not exist. Someone who needs a different
        tenant mints a different session and gets a different token.
        """
        session = Session(
            token=secrets.token_urlsafe(_TOKEN_BYTES),
            tenant_id=tenant_id,
            expires_at=int(time.time()) + self.ttl_s,
            channel=channel,
        )
        self._sessions[session.token] = session
        # The token is never logged, here or anywhere. It is a bearer
        # credential, and a log line holding one is a credential store nobody
        # decided to build.
        logger.info(
            "minted a %s session for %s, valid %ds", channel, tenant_id, self.ttl_s
        )
        return session

    def resolve(self, token: str) -> Session | None:
        """The session for this token, or None. Expiry is checked on read.

        Constant-time comparison is not used and is not needed: this is a dict
        lookup by the full token, not a byte-by-byte comparison against a
        secret, so there is no timing signal to leak.
        """
        session = self._sessions.get(token)
        if session is None:
            return None
        if not session.is_active():
            # Dropped on the way past rather than by a sweeper. Expiry is
            # answered correctly whether or not anything is running.
            del self._sessions[token]
            return None
        return session

    def revoke(self, token: str) -> bool:
        """Forget a credential. It stops working on the next call, not later."""
        return self._sessions.pop(token, None) is not None

    def __len__(self) -> int:
        return len(self._sessions)
