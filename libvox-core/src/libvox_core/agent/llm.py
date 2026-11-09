"""The model connection: one OpenAI-compatible endpoint, no SDK.

`httpx2` and a POST, because that is all this needs and because a vendor SDK
would put an abstraction between the project and its own system prompt — which
is where the restriction policy lives. The endpoint being OpenAI-compatible is
what makes the model swappable without touching anything else: vLLM in
production, Ollama in development, and the same shape for both.

**Nothing here decides anything.** It sends messages and tool schemas, and
returns either text or an intent to call a tool. Executing that intent is the
Core's job, and that indirection is what keeps the session credential out of
the model's reach — see the root README on the Cognitive Middleware.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


class ModelUnavailable(RuntimeError):
    """The model endpoint could not be reached, or answered unusably."""


@dataclass(frozen=True, slots=True)
class ToolIntent:
    """The model asking for a tool. It never executes one itself."""

    call_id: str
    name: str
    arguments: dict[str, Any]

    provider_extra: dict[str, Any] | None = None
    """Whatever the provider attached to this call, carried back untouched.

    Deliberately unopened and deliberately not named after what put it here.
    Gemini's thinking models sign the reasoning that precedes a tool call and
    return the signature under `extra_content`; sending the tool result without
    it answers `400 Function call is missing a thought_signature`, because the
    signature is what they validate the history against.

    The Core cannot reconstruct that -- it is a cryptographic token over
    something only the model saw -- so it keeps it and hands it back. Knowing
    what is inside would buy nothing and would make the next provider with a
    field of its own a second edit here.
    """


@dataclass(frozen=True, slots=True)
class Completion:
    """One answer from the model: words, or intents, or both."""

    text: str = ""
    intents: list[ToolIntent] = field(default_factory=list)

    @property
    def wants_tools(self) -> bool:
        return bool(self.intents)


def _parse_arguments(raw: Any, *, name: str) -> dict[str, Any]:
    """Tool arguments arrive as a JSON *string*, and models get them wrong.

    A malformed argument object is not an outage — it is the model failing at
    something it can be told about and retry. So this returns an empty dict and
    lets the tool's own schema produce the refusal, which is where the useful
    message is.
    """
    if isinstance(raw, dict):
        return raw
    if not raw:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        logger.warning("model sent unparseable arguments for %s", name)
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _diagnosis(exc: Exception, *, limit: int = 300) -> str:
    """What an operator needs, for the operator's log only.

    The status and whatever the endpoint said about it. Never the request --
    the key is in its headers, and the conversation is in its body.
    """
    response = getattr(exc, "response", None)
    if response is None:
        return ""

    try:
        detail = response.text[:limit]
    except Exception:  # noqa: BLE001 - a body we cannot read is not a crash
        detail = "<unreadable body>"
    return f" (HTTP {response.status_code}: {detail})"


class Model:
    """One OpenAI-compatible chat endpoint."""

    def __init__(
        self,
        base_url: str,
        *,
        model: str,
        api_key: str | None = None,
        timeout_s: float = 60.0,
        extra_body: dict[str, Any] | None = None,
        transport: Any | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._api_key = api_key
        self._timeout_s = timeout_s
        self._extra_body = extra_body or {}
        # A seam for tests, the same one KohaDriver has. Driving a MockTransport
        # keeps the request shape under test -- the temperature, the tool
        # payload, the authorization header -- instead of replacing the client
        # and testing nothing but our own mock.
        self._transport = transport

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
    ) -> Completion:
        """Send the conversation, get back words or an intent.

        `temperature=0`: this is a librarian answering from records, not a
        writing assistant. Two identical questions about the same catalog
        should not get two different answers, and creativity here shows up as
        an invented shelf mark.
        """
        import httpx2

        payload: dict[str, Any] = {
            "model": self._model,
            "messages": messages,
            "temperature": 0,
        }

        # Whatever the deployment wants sent, merged in without being read --
        # the mirror of ToolIntent.provider_extra, which carries back what the
        # provider attached. Neither direction teaches this module a provider.
        #
        # It is where a thinking control lives, and every vendor spells that
        # differently: Granite takes `enable_thinking` and `reasoning_effort`,
        # Gemini takes `reasoning_effort`, Ollama takes `think`. Knowing any of
        # them here would mean knowing all of them, forever.
        #
        # First, so the five fields below cannot be overwritten by
        # configuration. `model` and `messages` are not settings, and
        # `temperature: 0` is a decision this file argues for two paragraphs
        # above -- creativity here shows up as an invented shelf mark.
        payload = {**self._extra_body, **payload}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"content-type": "application/json"}
        if self._api_key:
            headers["authorization"] = f"Bearer {self._api_key}"

        try:
            async with httpx2.AsyncClient(
                timeout=self._timeout_s, transport=self._transport
            ) as client:
                response = await client.post(
                    f"{self._base_url}/chat/completions",
                    json=payload,
                    headers=headers,
                )
                response.raise_for_status()
                body = response.json()
        except Exception as exc:  # noqa: BLE001 - see below
            # Blind on purpose. Anything httpx raises -- a timeout, a refused
            # connection, a 401, a proxy's HTML error page -- becomes the same
            # opaque failure *to the caller*, because the alternative is
            # deciding case by case which exception messages are safe to let
            # near a conversation. None of them are.
            #
            # The log is a different audience. The first version of this logged
            # only the exception class, which told an operator staring at a
            # broken deployment precisely nothing: "HTTPStatusError" does not
            # distinguish a wrong key from a model that refuses tool calls. The
            # status and the endpoint's own complaint go here, where the
            # operator is, and nowhere near the model.
            logger.warning(
                "the model endpoint failed: %s%s",
                type(exc).__name__,
                _diagnosis(exc),
            )
            raise ModelUnavailable("the model could not be reached") from None

        return self._read(body)

    def _read(self, body: Any) -> Completion:
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError):
            logger.warning("the model returned a body with no choices")
            raise ModelUnavailable("the model returned nothing usable") from None

        intents = [
            ToolIntent(
                call_id=str(call.get("id") or f"call_{index}"),
                name=str(call.get("function", {}).get("name", "")),
                arguments=_parse_arguments(
                    call.get("function", {}).get("arguments"),
                    name=str(call.get("function", {}).get("name", "")),
                ),
                # Copied, not read. See ToolIntent.provider_extra.
                provider_extra=(
                    call.get("extra_content")
                    if isinstance(call.get("extra_content"), dict)
                    else None
                ),
            )
            for index, call in enumerate(message.get("tool_calls") or [])
        ]

        return Completion(text=(message.get("content") or "").strip(), intents=intents)
