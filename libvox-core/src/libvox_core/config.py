"""Runtime configuration, read from the environment.

Core reaches two databases and one class of ILS. It holds no state of its own
that survives a restart, which is deliberate for now and stated as a limitation
rather than a design — see `sessions.py`.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

# 8091, not 8080. The MCP client's default is 8080, which is Koha Testing
# Docker's OPAC -- a developer working on this project has one running, and the
# collision is a confusing failure. /libvox-portal picked 8090 for the same
# reason, and the databases 5433 and 5434.
DEFAULT_PORT = 8091

# Loopback, not 0.0.0.0. The /internal/v1 surface takes tenant_id in the request
# body and authenticates only the calling service, so anything that can reach it
# can ask for any library's data. Binding it to the world by default would make
# that a deployment accident rather than a decision.
DEFAULT_HOST = "127.0.0.1"

# The public surface: the WebSocket a reader's browser connects to, and the
# widget it loads. A different port from the internal API, and bound to every
# interface rather than loopback, because those two facts are opposites and one
# process should not pretend otherwise.
#
# They are separate ASGI applications, so /internal/v1 is not merely unrouted
# out here -- it does not exist. A reverse proxy that forgets a rule cannot
# expose an API that selects a tenant from its request body.
# Every interface, not loopback: a reader's browser is on another machine.
DEFAULT_PUBLIC_HOST = "0.0.0.0"
DEFAULT_PUBLIC_PORT = 8092

DEFAULT_SESSION_TTL_S = 3600

# /libvox-mcp's own default port. It has to be running in streamable-http mode,
# which is the only mode where a session credential can travel at all.
DEFAULT_MCP_URL = "http://127.0.0.1:8000/mcp"

# Ollama's default, spoken through its OpenAI-compatible surface. A developer
# with a local model needs no configuration; anything else is one variable.
DEFAULT_LLM_BASE_URL = "http://127.0.0.1:11434/v1"
DEFAULT_LLM_MODEL = "qwen3:14b"

# The conversation log. A different *database* from the Tenant DB -- which is
# what the one-writer rule is about -- on the same PostgreSQL instance, because
# a whole server for two tables buys nothing.
DEFAULT_CONVERSATION_DSN = (
    "postgresql://libvox_app:libvox_app@127.0.0.1:5435/libvox_conversations"
)

# Retention only. The app role is subject to row-level security and, with no
# tenant scope set, sees no conversations at all -- so a DELETE from it matches
# nothing and reports success. Forgetting spans libraries by definition, which
# the app role has no way to do and should not.
#
# Mirrors /libvox-sync's `admin_dsn`: operator work that outgrows the
# unprivileged role gets its own connection string, not a wider grant on the
# one the service uses. The service never loads this.
DEFAULT_CONVERSATION_ADMIN_DSN = (
    "postgresql://libvox:libvox@127.0.0.1:5435/libvox_conversations"
)


class ConfigurationError(RuntimeError):
    """Core cannot start safely with the environment it was given."""


def _json_object(name: str) -> dict[str, Any]:
    """A JSON object from the environment, or nothing, or a refusal.

    Refusing on malformed JSON rather than falling back to `{}` is the point. A
    deployment that meant to turn thinking off and quietly did not would show up
    as a latency number nobody could explain, days later, in a table.
    """
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return {}

    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ConfigurationError(f"{name} is not valid JSON: {exc}") from exc

    if not isinstance(value, dict):
        raise ConfigurationError(
            f"{name} must be a JSON object, got {type(value).__name__}"
        )

    return value


@dataclass(frozen=True, slots=True)
class Settings:
    discovery_dsn: str
    """The Discovery Index, as the unprivileged role so row-level security
    applies. Read-only in practice: /libvox-sync writes it."""

    tenant_dsn: str
    """The Tenant DB, owned by /libvox-portal. **Read-only.** The architecture
    gives that database exactly one writer, and adding a second one here would
    make the registry something two components disagree about."""

    crypto_key: str | None = None
    """Decrypts stored ILS credentials, per driver call and never cached.

    The same key /libvox-portal encrypted with. Absent unless a command needs
    it, so the service still starts for search-only work on a machine without
    one -- and fails with instructions the first time availability is asked."""

    service_token: str | None = None
    """Shared secret the MCP instance presents. Authenticates the *caller*,
    never a tenant: the tenant arrives in the request body, already resolved."""

    environment: str = "development"

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    public_host: str = DEFAULT_PUBLIC_HOST
    public_port: int = DEFAULT_PUBLIC_PORT
    """Where the browser-facing surface listens. See the constants."""

    mcp_url: str = DEFAULT_MCP_URL
    """Where /libvox-mcp listens, in streamable-http mode.

    Not stdio: authorization exists only over HTTP, and on stdio every caller
    is anonymous so every tool call is refused for want of a tenant."""

    llm_base_url: str = DEFAULT_LLM_BASE_URL
    llm_model: str = DEFAULT_LLM_MODEL
    llm_api_key: str | None = None

    llm_extra_body: dict[str, Any] = field(default_factory=dict)
    """Extra fields merged into every request to the model, from JSON in the
    environment, and never read here.

    The mirror of `ToolIntent.provider_extra`, which carries back what a
    provider attached to a tool call. One direction opaque in, the other opaque
    out, and neither teaches this project a vendor.

    It exists because a thinking control is spelled differently by everyone --
    Granite takes `enable_thinking` and `reasoning_effort`, Gemini takes
    `reasoning_effort`, Ollama takes `think` -- and because whether thinking
    helps *here* is measurable rather than arguable: three models of very
    different sizes all answer this catalogue chain 8 of 8, so reasoning before
    a tool call may be time spent reaching the same answer. `./core.sh bench`
    with and without settles it in eight questions.

    Malformed JSON is a refusal at startup, not a silently empty dict: a
    deployment that meant to turn thinking off and quietly did not would show up
    as a latency number nobody could explain.
    """
    mcp_timeout_s: float = 90.0
    """How long to wait on the tool layer.

    **The link that gave up first, and it was the outermost one.** This was
    unset, so httpx2's own default applied -- five seconds -- while everything
    it waits on is more patient: the Koha driver allows thirty seconds per
    request and retries once, and /libvox-mcp allows sixty for the call it
    makes back here. A library on the other side of the world answered an
    availability check in six seconds, correctly, and Core had already stopped
    listening: the log carries `available (2 of 3 loanable)` and, six
    milliseconds later, "could not reach the tool layer". The reader was told
    the catalogue was unreachable about an answer that had arrived.

    So the rule is an ordering, not a number: **each timeout must exceed the
    one it waits on.** driver 30s < /libvox-mcp 60s < this 90s. A reader is not
    made to wait ninety seconds by it -- the loop's round limit and the channel
    bound that -- but no layer may abandon work another layer is still doing.
    """

    llm_timeout_s: float = 120.0
    """Generous, and deliberately so. The deployment this targets is a
    self-hosted open-weight model, where the first request after an idle period
    pays for loading tens of gigabytes of weights -- a 35B answered in over 60s
    from cold on the endpoint this was developed against. A timeout shorter
    than the cold start turns a slow first question into an outage.

    One OpenAI-compatible endpoint. vLLM in production, Ollama in
    development, and the same shape for both -- which is what makes the model
    swappable without touching anything else."""

    conversation_dsn: str = DEFAULT_CONVERSATION_DSN
    """The conversation log, which Core owns and writes.

    Core is read-only on the Tenant DB and this is a different database, so the
    architecture's one-writer-per-database rule holds. They share an instance,
    which the rule says nothing about."""

    conversation_admin_dsn: str = DEFAULT_CONVERSATION_ADMIN_DSN
    """Used by `prune` and nothing else. See the constant for why it is not
    the same connection the service writes conversations through."""

    session_ttl_s: int = DEFAULT_SESSION_TTL_S
    """How long a session credential lives. Short by rule: propagation rule 7
    asks for opaque, short-lived and single-tenant, so compromise is bounded in
    time as well as in blast radius."""

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    def require_crypto_key(self) -> str:
        if not self.crypto_key:
            raise ConfigurationError(
                "LIBVOX_CRYPTO_KEY is not set, so stored ILS credentials cannot "
                "be decrypted and no availability question can be answered.\n"
                "It must be byte-identical to the one /libvox-portal encrypted "
                "with; credentials stored under one key cannot be read with "
                "another."
            )
        return self.crypto_key

    @classmethod
    def from_env(cls) -> Settings:
        from libvox_sync.config import Settings as SyncSettings

        # The DSN defaults live in /libvox-sync, which owns the Discovery Index
        # schema. Repeating them here would be two places to edit when a port
        # moves.
        sync_defaults = SyncSettings()

        environment = os.environ.get("LIBVOX_ENV", "development").strip().lower()
        service_token = os.environ.get("LIBVOX_CORE_SERVICE_TOKEN") or None

        # Refused at startup rather than left to a reverse proxy nobody
        # configured. Without a token, /internal/v1 is an unauthenticated API
        # that selects a tenant from its own request body.
        if environment != "development" and not service_token:
            raise ConfigurationError(
                f"LIBVOX_CORE_SERVICE_TOKEN is required when LIBVOX_ENV is "
                f"{environment!r}. The internal API takes tenant_id from the "
                "request body, so anything that can reach it can read any "
                "library's data.\n"
                '  export LIBVOX_CORE_SERVICE_TOKEN="$(openssl rand -hex 32)"'
            )

        return cls(
            discovery_dsn=os.environ.get("LIBVOX_DB_DSN", sync_defaults.dsn),
            tenant_dsn=os.environ.get(
                "LIBVOX_TENANT_DB_DSN", sync_defaults.tenant_dsn
            ),
            crypto_key=os.environ.get("LIBVOX_CRYPTO_KEY") or None,
            service_token=service_token,
            environment=environment,
            host=os.environ.get("LIBVOX_CORE_HOST", DEFAULT_HOST),
            port=int(os.environ.get("LIBVOX_CORE_PORT", DEFAULT_PORT)),
            public_host=os.environ.get("LIBVOX_CORE_PUBLIC_HOST", DEFAULT_PUBLIC_HOST),
            public_port=int(
                os.environ.get("LIBVOX_CORE_PUBLIC_PORT", DEFAULT_PUBLIC_PORT)
            ),
            conversation_dsn=os.environ.get(
                "LIBVOX_CONVERSATION_DB_DSN", DEFAULT_CONVERSATION_DSN
            ),
            conversation_admin_dsn=os.environ.get(
                "LIBVOX_CONVERSATION_ADMIN_DSN", DEFAULT_CONVERSATION_ADMIN_DSN
            ),
            mcp_url=os.environ.get("LIBVOX_MCP_URL", DEFAULT_MCP_URL),
            llm_base_url=os.environ.get(
                "LIBVOX_LLM_BASE_URL", DEFAULT_LLM_BASE_URL
            ).rstrip("/"),
            llm_model=os.environ.get("LIBVOX_LLM_MODEL", DEFAULT_LLM_MODEL),
            llm_api_key=os.environ.get("LIBVOX_LLM_API_KEY") or None,
            llm_extra_body=_json_object("LIBVOX_LLM_EXTRA_BODY"),
            mcp_timeout_s=float(os.environ.get("LIBVOX_MCP_TIMEOUT_S", "90")),
            llm_timeout_s=float(os.environ.get("LIBVOX_LLM_TIMEOUT_S", "120")),
            session_ttl_s=int(
                os.environ.get("LIBVOX_SESSION_TTL_S", DEFAULT_SESSION_TTL_S)
            ),
        )
