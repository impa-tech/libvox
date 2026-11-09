"""One message in, one answer out — whatever channel it came from.

This is where the five pieces meet: a session resolves to a library, the
library's persona builds a prompt, the stored history comes back, the loop
runs, and what happened is written down.

The Core has no per-channel entry point and cannot acquire one: nothing in the
inbound shape names a platform, so it cannot behave differently for WhatsApp
than for the widget because it cannot tell them apart.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import psycopg

from ..config import Settings
from ..sessions import Session
from . import prompt
from .conversation import Conversation
from .llm import Model
from .loop import Answer, answer
from .tools import ToolUnavailable, connect

logger = logging.getLogger(__name__)

_TOOLS_DOWN = (
    "I cannot reach the library's catalog right now. Please try again in a "
    "moment."
)


@dataclass(frozen=True, slots=True)
class Inbound:
    """What every adapter normalizes its platform's payload into.

    Four fields, and none of them names a platform. `capabilities` says what
    the channel can render, which is how delivery adapts without the Core
    branching on who is asking.
    """

    session: Session
    text: str
    locale: str | None = None
    capabilities: tuple[str, ...] = ()
    conversation_id: uuid.UUID | None = None


async def respond(
    settings: Settings,
    inbound: Inbound,
    *,
    on_tool: Callable[[str], Awaitable[None]] | None = None,
) -> tuple[Answer, uuid.UUID]:
    """Answer one message, and remember having done so.

    `on_tool` is passed straight through to the loop; see it for why an
    observer rather than a return value.
    """
    from ..tenants import widget_config
    from . import store

    tenant_id = inbound.session.tenant_id

    # The persona is read fresh rather than carried in the session: a library
    # that changes MARC.IA's name should see it change, not on the next
    # restart, but on the next message.
    with psycopg.connect(settings.tenant_dsn) as tenant_conn:
        persona = widget_config(tenant_conn, tenant_id)

    if inbound.locale:
        # What the channel says the reader is using outranks the library's
        # default. A patron writing in English to a Portuguese library is
        # answered in English.
        persona = {**persona, "locale": inbound.locale}

    system_prompt = prompt.build(persona)

    with psycopg.connect(settings.conversation_dsn) as log:
        if inbound.conversation_id is None:
            opened = store.open_conversation(
                log, tenant_id, channel=inbound.session.channel
            )
            conversation_id = opened.conversation_id
            conversation = Conversation(system_prompt)
        else:
            conversation_id = inbound.conversation_id
            conversation = store.load(
                log, tenant_id, conversation_id, system_prompt
            )

        before = len(conversation.messages)

        try:
            async with connect(
                settings.mcp_url,
                inbound.session.token,
                timeout_s=settings.mcp_timeout_s,
            ) as tools:
                result = await answer(
                    conversation,
                    inbound.text,
                    model=Model(
                        settings.llm_base_url,
                        model=settings.llm_model,
                        api_key=settings.llm_api_key,
                        timeout_s=settings.llm_timeout_s,
                        extra_body=settings.llm_extra_body,
                    ),
                    tools=tools,
                    on_tool=on_tool,
                    # The loop says two sentences of its own, and they have to
                    # be in the language the rest of the answer is in.
                    locale=persona.get("locale"),
                )
        except ToolUnavailable:
            # The tool layer being down is not the reader's problem to
            # understand. The question is still stored, because a conversation
            # missing the turn that failed is a conversation nobody can debug.
            conversation.add_user(inbound.text)
            conversation.add_assistant(_TOOLS_DOWN)
            result = Answer(text=_TOOLS_DOWN)

        store.append(
            log, tenant_id, conversation_id, conversation.messages[before:]
        )

    if "streaming" in inbound.capabilities:
        # Accepted and noted rather than honoured. Streaming needs a socket to
        # stream into, and the channel gateway is the round that brings one.
        logger.debug("channel offers streaming; answering in one piece for now")

    return result, conversation_id
