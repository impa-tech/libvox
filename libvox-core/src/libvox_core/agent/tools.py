"""The tools MARC.IA has, which are whatever `/libvox-mcp` declares.

There is no local list. `list_tools()` on the connection, and that is the whole
registry — which is the one enforcement in this system that is structural
rather than advisory: MARC.IA cannot search the web because there is nothing to
call.

**Over HTTP, with a Bearer, and not over stdio.** That is not a preference. The
MCP module says it outright: *"Authorization exists only over HTTP … On stdio
every caller is anonymous, so every tool call is refused for want of a tenant.
That is the correct behavior, not a bug."* The session credential rides an
`Authorization` header, because propagation rule 2 keeps it out of tool
arguments and the MCP layer's own docstring says headers are not identity —
this one is a credential the server hands to its verifier, not a header it
trusts.

**One connection per session.** The bearer belongs to a session, so the client
is opened with one and dies with it. A connection outliving its session would
be a credential outliving its scope.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# What the MCP layer publishes, and therefore what MARC.IA can do. Not a
# configuration: a fact asserted by a test, so a third tool appearing upstream
# is noticed here rather than absorbed.
EXPECTED_TOOLS = frozenset({"search_catalog", "check_availability"})


class ToolUnavailable(RuntimeError):
    """The tool layer could not be reached or refused the connection."""


@dataclass(frozen=True, slots=True)
class ToolCall:
    """One thing the model asked for, and what came back."""

    name: str
    arguments: dict[str, Any]
    result: dict[str, Any] | None = None
    error: str | None = None

    @property
    def failed(self) -> bool:
        return self.error is not None


class ToolBox:
    """A live MCP connection, scoped to one session."""

    def __init__(self, client: Any) -> None:
        self._client = client
        self._schemas: list[dict[str, Any]] | None = None

    async def schemas(self) -> list[dict[str, Any]]:
        """The tool definitions, in the shape an OpenAI-compatible API wants.

        Fetched once per connection, which is once per session: a tool set that
        changed mid-conversation would mean the model was offered something in
        turn one that no longer exists in turn three.
        """
        if self._schemas is not None:
            return self._schemas

        listed = (await self._client.list_tools()).tools
        declared = {tool.name for tool in listed}

        if declared - EXPECTED_TOOLS:
            # Not fatal -- refusing to answer because upstream grew a tool would
            # be worse -- but loud. A tool surface that widens without anyone
            # deciding is how a model gets an ability nobody meant to give it.
            logger.warning(
                "the MCP layer declares tools this agent did not expect: %s",
                sorted(declared - EXPECTED_TOOLS),
            )

        self._schemas = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": (tool.description or "").strip(),
                    "parameters": tool.input_schema or {"type": "object"},
                },
            }
            for tool in listed
        ]
        return self._schemas

    async def call(self, name: str, arguments: dict[str, Any]) -> ToolCall:
        """Execute one tool call, and never raise into the loop.

        A tool that failed is a turn the model has to handle, not a crash: it
        gets told the catalog could not answer, and decides what to say. What
        it must never get is the reason — a 403, an ILS URL, a permission name
        — which the MCP layer already collapses on its side and which this must
        not reintroduce.
        """
        known = {schema["function"]["name"] for schema in await self.schemas()}
        if name not in known:
            # The model invented a tool. Refused here rather than passed on, so
            # nothing is executed and the model is told plainly.
            logger.warning("the model asked for a tool that does not exist: %r", name)
            return ToolCall(
                name=name,
                arguments=arguments,
                error=f"There is no tool called {name!r}. Use only the tools offered.",
            )

        try:
            result = await self._client.call_tool(name, arguments)
        except Exception:
            logger.warning("tool call failed: %s", name, exc_info=True)
            return ToolCall(
                name=name,
                arguments=arguments,
                error="The library's catalog could not answer that request.",
            )

        if getattr(result, "is_error", False):
            # Two failures wear the same clothes here, and telling them apart
            # is what lets the model recover from one of them.
            #
            # A validation error -- `limit: Input should be less than or equal
            # to 20` -- is something the model can fix by asking again, and
            # replacing it with "the catalog could not answer" leaves it
            # guessing. An upstream failure is not fixable and its detail must
            # not travel; but the MCP layer has *already* collapsed that on its
            # side, deliberately, so whatever reaches here is what it decided a
            # caller may see. Re-sanitizing it discards the only correction the
            # model was going to get.
            detail = _first_text(result)
            logger.warning("tool %s refused: %s", name, detail or "no detail")
            return ToolCall(
                name=name,
                arguments=arguments,
                error=detail or "The library's catalog could not answer that request.",
            )

        return ToolCall(
            name=name, arguments=arguments, result=result.structured_content or {}
        )


def _first_text(result: Any, *, limit: int = 400) -> str:
    """The text an MCP error result carries, if any.

    Trimmed: a pydantic validation error ends in a documentation URL that helps
    a developer and costs a model context for nothing.
    """
    for item in getattr(result, "content", None) or []:
        text = getattr(item, "text", None)
        if text:
            return " ".join(str(text).split())[:limit]
    return ""


def as_tool_message(call: ToolCall) -> str:
    """What the model sees as the result of its own call.

    JSON, so that a later turn can read a `biblio_id` out of it — this is what
    makes "is the second one available?" answerable without searching again.
    """
    if call.failed:
        return json.dumps({"error": call.error})
    return json.dumps(call.result, ensure_ascii=False, default=str)


@asynccontextmanager
async def connect(
    mcp_url: str, session_token: str, *, timeout_s: float = 90.0
) -> AsyncIterator[ToolBox]:
    """Open a tool connection bound to one session.

    The token travels as a Bearer on the HTTP client, which is the only place
    it can travel: not a tool argument (rule 2), not the model's context
    (rule 1), and not stdio (where it would not travel at all).
    """
    import httpx2
    from mcp import Client
    from mcp.client.streamable_http import streamable_http_client

    # The timeout is passed, not defaulted. httpx2's own is five seconds, and a
    # tool call reaches a library's own server -- often on another continent,
    # through four sequential requests. See Settings.mcp_timeout_s for the
    # ordering this belongs to.
    http = httpx2.AsyncClient(
        headers={"Authorization": f"Bearer {session_token}"},
        timeout=timeout_s,
    )
    try:
        async with Client(streamable_http_client(mcp_url, http_client=http)) as client:
            yield ToolBox(client)
    except ToolUnavailable:
        raise
    except Exception as exc:
        # The URL is in the operator's log and nowhere near a conversation.
        logger.warning("could not reach the tool layer at %s: %s", mcp_url, exc)
        raise ToolUnavailable("the tool layer is unreachable") from exc
    finally:
        await http.aclose()
