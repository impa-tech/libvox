"""What MARC.IA remembers, and what she forgets first.

The history goes back to the model on every turn — that is what makes *"and is
the second one available?"* answerable. But what has to go back is not only the
words:

    user       "do you have anything by Dürrenmatt?"
    assistant  → search_catalog{"query": "Dürrenmatt"}
    tool       ← {"index_status": "ready", "records": [{"biblio_id": 2, …}]}
    assistant  "Two: Ein Engel kommt nach Babylon and …"
    user       "is the second one available?"

Without the `tool` line the model has no `biblio_id` for the second book and
can only invent one or search again. So a tool result is a message like any
other, and it carries JSON rather than prose.

**Scoped to a session, never to a person.** No name, no identifier, nothing
that would let two conversations be recognised as the same reader. That is the
root README's non-goal, and it is what makes the privacy claim checkable rather
than aspirational.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# Characters, not tokens: counting tokens properly needs the model's own
# tokenizer, which differs per model and would tie this to one. Four characters
# per token is the usual rough ratio, and being wrong here costs a slightly
# shorter history, not a wrong answer.
DEFAULT_BUDGET_CHARS = 48_000


@dataclass(slots=True)
class Message:
    role: str
    content: str
    tool_name: str | None = None
    tool_arguments: dict[str, Any] | None = None
    tool_call_id: str | None = None

    provider_extra: dict[str, Any] | None = None
    """What the provider attached to this call, kept so it can be given back.

    See ToolIntent.provider_extra in llm.py. It is stored rather than held in
    memory because the history goes back to the model on every turn and a
    conversation is rebuilt from the database between them -- so without it in
    a row, a reader's *second* question fails exactly as the first one did.
    """

    def as_wire(self) -> dict[str, Any]:
        """The shape an OpenAI-compatible endpoint expects."""
        if self.role == "tool":
            return {
                "role": "tool",
                "tool_call_id": self.tool_call_id or "",
                "content": self.content,
            }
        if self.tool_name:
            return {
                "role": "assistant",
                "content": self.content or None,
                "tool_calls": [
                    {
                        # Given back before anything else in the object, and
                        # only when a provider put something here. A field the
                        # endpoint does not know is a 400 from a strict one,
                        # which is the failure this exists to fix rather than
                        # to cause.
                        **(
                            {"extra_content": self.provider_extra}
                            if self.provider_extra
                            else {}
                        ),
                        "id": self.tool_call_id or "",
                        "type": "function",
                        "function": {
                            "name": self.tool_name,
                            # A JSON *string*, not an object. The API parses
                            # this field itself, so handing it a dict answers
                            # `400 the JSON object must be str, bytes or
                            # bytearray, not dict` -- which is the endpoint's
                            # json.loads complaining, and which no fake model
                            # will ever produce for you.
                            "arguments": json.dumps(
                                self.tool_arguments or {}, ensure_ascii=False
                            ),
                        },
                    }
                ],
            }
        return {"role": self.role, "content": self.content}

    @property
    def size(self) -> int:
        return len(self.content or "") + len(self.tool_name or "")


@dataclass(slots=True)
class Conversation:
    """One session's history. No person attached, by construction."""

    system_prompt: str
    messages: list[Message] = field(default_factory=list)
    budget_chars: int = DEFAULT_BUDGET_CHARS

    def add_user(self, text: str) -> None:
        self.messages.append(Message("user", text))

    def add_assistant(self, text: str) -> None:
        self.messages.append(Message("assistant", text))

    def add_tool_call(
        self,
        call_id: str,
        name: str,
        arguments: dict,
        *,
        provider_extra: dict[str, Any] | None = None,
    ) -> None:
        self.messages.append(
            Message(
                "assistant",
                "",
                tool_name=name,
                tool_arguments=arguments,
                tool_call_id=call_id,
                provider_extra=provider_extra,
            )
        )

    def add_tool_result(self, call_id: str, name: str, payload: str) -> None:
        self.messages.append(
            Message("tool", payload, tool_name=name, tool_call_id=call_id)
        )

    def as_wire(self) -> list[dict[str, Any]]:
        """The messages to send, trimmed to the budget.

        What never goes, in order:

        1. **The policy layer.** It is the only text present on every turn, and
           it is where the restriction lives. A truncation that dropped it
           would leave a model with a library's persona and none of the rules.
        2. **The current question**, and the tool exchange answering it.
        3. **Recent turns**, newest backwards.
        4. **Old tool results go before the sentences they produced** — "we
           have two: Ein Engel and Der Besuch" keeps what mattered in a
           fraction of the space that twenty records took.

        What that means in practice, and it is the right behaviour: in a long
        conversation MARC.IA forgets the ids from the beginning and searches
        again. One more query against the index costs the library nothing,
        and an invented id reaching check_availability would.
        """
        kept: list[Message] = []
        used = len(self.system_prompt)

        # Newest first, so what survives is what the current question needs.
        for message in reversed(self.messages):
            if used + message.size > self.budget_chars:
                if message.role == "tool":
                    # Dropped entirely rather than summarized: a partial JSON
                    # record read by a model is worse than an absent one.
                    logger.debug("dropping an old tool result to stay in budget")
                    continue
                break
            kept.append(message)
            used += message.size

        kept.reverse()
        if len(kept) < len(self.messages):
            logger.info(
                "history trimmed to %d of %d messages", len(kept), len(self.messages)
            )

        # An orphaned tool result -- one whose assistant call was trimmed away
        # -- confuses every OpenAI-compatible endpoint, and some refuse it
        # outright. Drop a `tool` message with no call before it.
        answered = {m.tool_call_id for m in kept if m.role == "assistant" and m.tool_name}
        kept = [
            m for m in kept if m.role != "tool" or m.tool_call_id in answered
        ]

        return [{"role": "system", "content": self.system_prompt}] + [
            m.as_wire() for m in kept
        ]
