"""Resume slips: how the widget picks a conversation back up on the next page.

An OPAC is a site where every click reloads the page, and the socket dies with
it. Before this, each page was a new conversation: the reader lost the panel's
history and MARC.IA lost what "the second one" meant, at the exact moment the
natural use -- ask, open the record, ask again -- needed both.

`gateway.py` keeps the `conversation_id` server-side on purpose, because "a
conversation id in a frame is an id someone can put in a different frame".
Getting a conversation back across a reload means handing the browser
*something* it returns, so this is the smallest thing that can be:

- **Random, and not the conversation id.** The log's own identifier never
  leaves the process. A slip says nothing about the conversation it points at,
  and it can be revoked without touching the log.
- **Bound to the tenant and the exact Origin of the handshake that earned it.**
  Redeeming it re-checks both against the handshake now in progress, which has
  already been through `resolve_channel`. A slip leaked from one library's page
  opens nothing on another's.
- **Short-lived, and renewed by use.** Idle time, measured from the last
  answer: a visit, not a relationship. The browser holds it in
  `sessionStorage`, which dies with the tab, so the next reader at a shared
  terminal finds an empty widget.
- **Pointing at the stored conversation, not at a session.** Sessions are
  per-socket and short; the log in `libvox_conversations` is what a later turn
  is rebuilt from anyway.

**In memory, like `SessionStore`, and for the same reason.** A table would
survive a restart, and it would also be a column joining one reader's page
views together -- the `session_id` that `/libvox-core/AGENTS.md` asks this
module never to add to the log. Restarting Core forgets every slip, and the
next page starts a fresh conversation without saying anything went wrong.

A refusal is one answer whatever the reason -- unknown, expired, revoked,
another library, another origin. Telling them apart would let someone learn
which of those a slip they hold is.
"""

from __future__ import annotations

import logging
import secrets
import time
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

# Same size as a session token, from the same CSPRNG. Unguessability is the
# whole property: a slip anyone could guess is anyone's conversation.
_TOKEN_BYTES = 32

MAX_TOKEN_CHARS = 100
"""The longest slip `gateway.py` will look up. `token_urlsafe(32)` is 43
characters; anything far past that was not minted here."""

# Expired slips are swept on the way past rather than by a timer. Every this
# many issues, one pass over the dict -- cheap, and it bounds memory by how
# many conversations start in one TTL rather than by uptime.
_SWEEP_EVERY = 64


@dataclass(slots=True)
class _Slip:
    tenant_id: str
    origin: str | None
    conversation_id: uuid.UUID
    expires_at: float


@dataclass
class ResumeStore:
    """Issue, redeem, renew and revoke. Nothing here re-points a slip."""

    ttl_s: int = 1800
    _slips: dict[str, _Slip] = field(default_factory=dict)
    _issued: int = 0

    def issue(
        self, tenant_id: str, origin: str | None, conversation_id: uuid.UUID
    ) -> str:
        """A new slip for a conversation that has just been opened."""
        self._issued += 1
        if self._issued % _SWEEP_EVERY == 0:
            self._sweep()

        token = secrets.token_urlsafe(_TOKEN_BYTES)
        self._slips[token] = _Slip(
            tenant_id=tenant_id,
            origin=origin,
            conversation_id=conversation_id,
            expires_at=time.time() + self.ttl_s,
        )
        # Never the token, here or anywhere: it opens a reader's conversation.
        logger.info("issued a resume slip for %s", tenant_id)
        return token

    def redeem(
        self, token: str, tenant_id: str, origin: str | None
    ) -> uuid.UUID | None:
        """The conversation this slip points at, or None.

        `tenant_id` and `origin` are the current handshake's, already proven by
        `resolve_channel`. Both have to match what the slip was issued under,
        exactly -- the same comparison the channel binding itself makes.
        Renewed on success, so a reader who keeps changing page keeps the
        conversation for as long as they keep using it.
        """
        slip = self._live(token)
        if slip is None:
            return None
        if slip.tenant_id != tenant_id or slip.origin != origin:
            # Logged without the token and without the other side's details.
            logger.info("refused a resume slip presented to %s", tenant_id)
            return None
        slip.expires_at = time.time() + self.ttl_s
        return slip.conversation_id

    def touch(self, token: str) -> None:
        """Another answer in this conversation: the clock starts again."""
        slip = self._live(token)
        if slip is not None:
            slip.expires_at = time.time() + self.ttl_s

    def revoke(self, token: str) -> bool:
        """The reader asked for a new conversation. Stops working at once."""
        return self._slips.pop(token, None) is not None

    def _live(self, token: str) -> _Slip | None:
        slip = self._slips.get(token)
        if slip is None:
            return None
        if time.time() >= slip.expires_at:
            del self._slips[token]
            return None
        return slip

    def _sweep(self) -> None:
        now = time.time()
        for token in [t for t, s in self._slips.items() if now >= s.expires_at]:
            del self._slips[token]

    def __len__(self) -> int:
        return len(self._slips)
