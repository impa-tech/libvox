"""The loop: prompt, intent, execute, append, answer.

Nine steps in the root README's diagram, and the one that matters is the third:
the model returns an *intent*, and the Core executes it. The model never
invokes a tool itself, which is what keeps the session credential — and the
library's identity — outside anything it can read or write.

The loop ends. A model that keeps asking for tools would otherwise chain
searches until the context bursts, and a small open-weight model does exactly
that when a search finds nothing.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field

from .conversation import Conversation
from .llm import Model, ModelUnavailable
from .tools import ToolBox, ToolCall, as_tool_message

logger = logging.getLogger(__name__)

SEARCHES_PER_TURN = 2
"""How many catalogue searches one question may cost.

Rule 6 says twice is the limit, and prose did not hold it. Measured on
`./core.sh bench lib-local` the moment rule 6 was widened to let the model
choose its own words: *"tem algum livro sobre culinária japonesa?"* went from one
search to five -- `culinária japonesa`, `Japanese cooking`, `japonesa`, `cozinha
japonesa`, `cozinha` -- and the last of them returned a Ruby cookbook. 56 seconds
to say no, against 3.7 before.

That is the `Opus posthumous` failure this project already paid for once, and
`tests/test_prompt.py` tells that story: five searches, none of them the
reader's, reporting a book the library had as one it lacked. The repair then was
to take the freedom away. The freedom is wanted -- a reader asking for "o livro
da Capitu" should reach Dom Casmurro -- so the ceiling has to be somewhere the
model cannot talk its way past.

Per turn, not per conversation, for the same reason `already` is: a second
question deserves its own two.
"""

AVAILABILITY = "check_availability"
"""The one tool that reaches the library's own server.

It is not rationed by count -- rule 8 forbids reusing an answer, so three books
are three questions -- but it is stopped after a failure. See `_UNREACHABLE`."""

_UNREACHABLE = json.dumps(
    {
        "catalog_unreachable": (
            "check_availability already failed for this question and the "
            "library's system is not answering. Narrowing the ids does not "
            "change that. Say the catalogue could not be reached, and answer "
            "from what the search returned."
        )
    },
    ensure_ascii=False,
)
"""What a second attempt at availability gets after the first one failed.

Measured against a library whose ILS was refusing tokens: asked about five
records, the model called `check_availability` five times -- all five ids, then
three of them, then one, then another one, then the last three -- looking for a
subset that would work. Each set is a different signature, so the repeat guard
never saw it, and the turn ran out of rounds after ten minutes.

Every one of those is a live request to a server that is already failing, which
is the worst possible moment to send more. And the answer was never in doubt:
rule 10 says a failed tool means telling the reader the catalogue could not
answer, and that sentence is available after the first failure exactly as it is
after the fifth.

Per turn, like the search budget beside it: a library that recovers between two
questions deserves to be asked again."""

SEARCH = "search_catalog"
"""The one tool `SEARCHES_PER_TURN` bounds. Availability is not rationed: it
answers about records already found, and rule 8 forbids reusing an old answer."""

_SPENT = json.dumps(
    {
        "searches_spent": (
            "You have used both of this question's searches. There is no third. "
            "Answer from the results above, or say the library does not have "
            "it -- which is an answer, and a useful one."
        )
    },
    ensure_ascii=False,
)
"""What a search past the budget returns instead of running.

A refusal shaped like a tool result, so it lands where the model is already
looking. Silence would read as a tool that broke, and rule 10 would have her
tell the reader the catalogue could not answer."""

DEFAULT_MAX_ROUNDS = 6
"""Room for a model that varies a term twice and still checks the shelf.

The chain a question needs is two rounds: search, then check availability. Six
is the longest sequence that is still work rather than looping -- and the
measured evidence for the shape is one conversation that searched "livro russo",
then "русский", found what it wanted on "Russian book", and checked availability.
Four rounds fit that only because it stopped; a fifth search would have cost the
answer.

**It was four, and four was tight for a reason that is now gone.** Rule 7 used to
ask for three searches before a library could be said to hold nothing, so *every*
question answered "we do not have it" spent the budget and landed on the final
call -- which is where a reader was handed the agent's plan instead of an answer.

Raising it is not free, and the ceiling is not this number. A round is a model
call plus a tool call, each re-sending the whole history, so token cost grows
with the square of the rounds: six sends roughly twice what four sends for the
same answer. Nothing bounds a whole response -- there are per-call timeouts and
no overall one -- so this is also what bounds the worst case a reader waits
through. Ten was asked for and refused on those grounds; ten wants an
`asyncio.timeout` around `respond()` first, so the rounds stop being the only
brake.

If a rule ever asks for more searches again, this number has to be reconciled
with it. Nobody did that the last time, and it took a week to surface."""

# The two sentences the loop says on its own account, in the reader's language.
#
# They used to be English constants, which nobody noticed while they were rare:
# the fallback fired only on an outage, and the other one lost a race with the
# model's own words. Guarding the reader from tool-call syntax made the first
# one routine, and a Portuguese reader asking about culinária was answered
# "I looked several times and did not manage to finish."
#
# The same two locales the persona has, defaulting the same way. A channel that
# adds a language adds it here too -- there is no third place these live.
_NOTHING_TO_SAY = {
    "pt-BR": (
        "Procurei várias vezes e não consegui concluir. Me diga um pouco mais "
        "sobre o que você procura e eu tento de novo."
    ),
    "en": (
        "I looked several times and did not manage to finish. Tell me a little "
        "more about what you are after and I will try again."
    ),
}

_UNAVAILABLE = {
    "pt-BR": (
        "Não consigo falar com os sistemas da biblioteca agora. Tente de novo "
        "daqui a pouco."
    ),
    "en": (
        "I cannot reach the library's systems right now. Please try again in a "
        "moment."
    ),
}


def _in(sentences: dict[str, str], locale: str | None) -> str:
    return sentences.get(locale or "pt-BR", sentences["pt-BR"])

# Machinery written as prose, which is a different thing from machinery. A
# small model asked for an answer with the tools withdrawn sometimes writes a
# call anyway, in whatever markup its training used -- and because there is no
# structured call attached, the loop reads it as the model's final words and
# hands it to the reader. Three shapes seen so far, on two models:
#
#     <tool_call><function=search_catalog><parameter=query>comida
#     <THINKING> {"arguments": {...}, "name": "search_catalog"} </tool_call>
#     <SCRATCHPAD> {"arguments": {...}, "name": "search_catalog"}
#
# **The tag names are not the signal, and chasing them was the first mistake.**
# This began as a list of opening tags; the second shape walked past it because
# it opens with a scratchpad tag, and the third walked past the widened list
# because it invented a tag nobody had seen. Each fix held until the next run.
#
# What every one of them has in common is the name of a tool this system
# offered. Those are internal identifiers -- `search_catalog`,
# `check_availability` -- and a sentence written for a reader has no use for
# one, in any language. So the tool names are the test, and the tag list stays
# only for the leak that names no tool: a bare reasoning scratchpad.
_MACHINERY = (
    "<tool_call",
    "</tool_call",
    "<function=",
    "</function",
    "<|tool_call",
    "<|python_tag|>",
    "<think",
    "</think",
    "<reasoning",
    "</reasoning",
)


# Said to the model on the final call, and to nobody else.
#
# Withdrawing the tools without a word was a question in itself, and not the one
# anybody meant to ask. The model reads a history full of searching, finds no
# tools, and answers what the history implies comes next -- so a reader who
# asked for chess books got *"I've already searched for xadrez multiple times…
# Let me search in English and Spanish now"*, in English, citing a rule number.
#
# That is not leaked reasoning. `_spoken` never had a chance at it: no `<think`,
# no tool name, no bracket -- plain prose, and no marker list distinguishes a
# plan from an answer. The fix is to stop asking the question that produces one.
#
# Appended to the payload and never to the `Conversation`: stored, it would come
# back on the next turn and show up on the operator's screen as something the
# library said.
_WITHDRAWN = {
    "role": "system",
    "content": (
        "The tools are gone. Answer now, from what is already in this "
        "conversation, and say nothing about tools or about what you would do "
        "next. If the searches found nothing, say that in one sentence."
    ),
}


# How a reasoning dump announces itself, when it is not wrapped in a tag.
#
# `_MACHINERY` above covers the tagged forms and cuts at the marker, keeping
# what came before. These are the opposite shape: the reasoning is first and the
# answer, if any, is somewhere at the end of thousands of characters with no
# delimiter between them. Cutting is not available; the only honest options are
# to show it or to refuse it.
#
# Leading position is the whole test. A real answer does not open with "Thinking
# Process:", and a sentence that merely contains the phrase later is not caught.
_REASONING_OPENERS = (
    "thinking process",
    "thought process",
    "let me think",
    "okay, let me",
    "first, i need to",
    "i need to analyze",
)


def _opening_reasoning(text: str) -> str:
    """The marker this text opens with, or empty."""
    head = text.lstrip().lower()
    return next((m for m in _REASONING_OPENERS if head.startswith(m)), "")


def _for_the_reader(text: str, names: Iterable[str], locale: str | None) -> str:
    """What a reader is shown, or a refusal when the reply is not an answer.

    **A reader once received the model's entire monologue** -- thousands of words
    reasoning about the system's own numbered rules, drafting and redrafting a
    sentence, in English. The endpoint had put the reasoning in `content`
    instead of `reasoning_content`, and `_spoken` had nothing to cut on.
    """
    marker = _opening_reasoning(text)
    if not marker:
        return _spoken(text, names) or _in(_NOTHING_TO_SAY, locale)

    # Neither the text nor a prefix of it: the reasoning restates the reader's
    # question, and the patron's words belong in the conversation table and
    # nowhere else. The marker and the size say enough to identify the shape.
    logger.error(
        "the model's reasoning arrived inside `content` (opens with %r, %d chars), "
        "so no answer can be separated from it and the reader was told the "
        "system is unavailable. The endpoint is not putting reasoning in "
        "`reasoning_content`. On LiteLLM that is the model's "
        "custom_llm_provider: `ollama` calls Ollama's /api/generate, which has "
        "no reasoning field at all; `ollama_chat` calls /api/chat, which does.",
        marker,
        len(text),
    )
    return _in(_UNAVAILABLE, locale)


def _spoken(text: str, tool_names: Iterable[str] = ()) -> str:
    """What of the model's reply is fit to show a reader.

    Cut rather than discarded: the model often writes a real sentence and then
    appends a call it was not entitled to make, and throwing the sentence away
    too would cost an answer that was already correct.
    """
    haystack = text.lower()

    cut = min(
        (haystack.index(marker) for marker in _MACHINERY if marker in haystack),
        default=len(text),
    )

    # A tool name in the text means what follows -- and usually what precedes
    # it, since the envelope opens first -- is a call and not an answer. Cutting
    # at the earliest bracket in the whole reply rather than at the name itself,
    # because `<SCRATCHPAD> {"name": "search_catalog"}` has to lose the tag too,
    # and prose containing a tool name has already stopped being prose.
    if any(name and name.lower() in haystack for name in tool_names):
        brackets = [i for i in (text.find("<"), text.find("{")) if i != -1]
        cut = min(cut, min(brackets, default=0))

    said = text[:cut].strip()

    if said != text.strip():
        logger.warning("the model wrote machinery as prose; not shown")
    return said


@dataclass(slots=True)
class Answer:
    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    truncated: bool = False
    """The loop hit its limit. The reader is told, rather than handed silence
    or a confident answer built on half a search."""


async def answer(
    conversation: Conversation,
    text: str,
    *,
    model: Model,
    tools: ToolBox,
    max_rounds: int = DEFAULT_MAX_ROUNDS,
    on_tool: Callable[[str], Awaitable[None]] | None = None,
    locale: str | None = None,
) -> Answer:
    """One turn of conversation, however many tool calls it takes.

    `on_tool` is told the name of each tool as it starts, and nothing else --
    not its arguments, not its result. It exists so a channel can say "looking
    in the catalogue" while a call to a library's server is in flight, instead
    of animating dots for a duration it invented. An observer, deliberately:
    it cannot change what runs, and a channel that raises inside it should not
    cost the reader an answer.
    """
    conversation.add_user(text)
    schemas = await tools.schemas()
    # The identifiers the model was offered, which is what tells a call written
    # as prose apart from an answer. See `_spoken`.
    names = [
        schema.get("function", {}).get("name", "")
        for schema in schemas
        if isinstance(schema, dict)
    ]
    performed: list[ToolCall] = []

    # Every (tool, arguments) this turn has already run.
    #
    # **A rule the model can ignore is not a bound.** Policy rule 7 says never
    # run the same search twice, and a reader asking about pokemon watched it
    # run `search_catalog{"query": "pokemon"}` six times, identically, until the
    # round limit. The trigger was `truncated: true` -- five records matched of
    # more -- which rule 8 says to report as "at least five" and which the model
    # instead read as a reason to try again. The same query returns the same
    # five, forever.
    #
    # So the second identical call does not run. It costs nothing to detect and
    # it saves two different things: an index query, and -- on
    # `check_availability` -- one live HTTP request per record against a
    # library's own server, which is the traffic this project is most careful
    # about.
    #
    # What comes back is deliberately *not* the earlier result. Handing back the
    # same records would leave the model's input unchanged, and unchanged input
    # is what a loop is. It gets a sentence naming what it already did, which is
    # new information and the only kind that can end the cycle.
    #
    # **Per turn, and that is required rather than incidental.** Policy rule 9
    # says availability changes minute to minute and must never be reused from
    # earlier in the conversation, so a reader's second question has to be
    # allowed to check the same book again. This dict is local to one `answer`,
    # so it bounds a runaway inside a turn and forgets between them. Hoisting it
    # onto the conversation would silently repeal rule 9.
    already: dict[tuple[str, str], str] = {}

    # Spent searches, bounded by SEARCHES_PER_TURN. Beside `already` because
    # they are the same kind of thing: a runaway the model cannot be talked out
    # of, stopped by the loop rather than by the prompt.
    searches = 0
    availability_failed = False

    for round_number in range(max_rounds):
        # Withdrawn rather than refused, once the budget is spent. The refusal
        # below still exists and still fires -- a model can ask for a tool it
        # was not offered -- but paying a whole round for the model to be told
        # no is a round the reader waits through. Measured: the miss question
        # asked for a third search in seven runs of eight, and each of those
        # rounds cost about ten seconds to produce nothing.
        spent = set()
        if searches >= SEARCHES_PER_TURN:
            spent.add(SEARCH)
        if availability_failed:
            spent.add(AVAILABILITY)
        offered = [s for s in schemas if s["function"]["name"] not in spent]

        try:
            completion = await model.complete(conversation.as_wire(), tools=offered)
        except ModelUnavailable:
            # Already logged without the endpoint or the key. The reader gets a
            # sentence, not a stack trace, and the conversation stays usable.
            #
            # **Stored, like every other sentence a reader is shown.** This was
            # the one return that did not, and the gap was invisible until
            # something needed to count answers: a reader saw "não consigo falar
            # com os sistemas da biblioteca agora" and the transcript had no
            # turn at all. Nineteen of a hundred and seventy-six conversations
            # carried one, all from an afternoon when the model endpoint
            # answered 400 to every request.
            #
            # service.py makes the same argument three lines into its own
            # ToolUnavailable branch: a conversation missing the turn that
            # failed is a conversation nobody can debug.
            unavailable = _in(_UNAVAILABLE, locale)
            conversation.add_assistant(unavailable)
            return Answer(text=unavailable, tool_calls=performed)

        if not completion.wants_tools:
            # Through `_spoken`, not raw: a reply carrying no structured call
            # can still *contain* one written out as text, and this is the
            # branch that would print it.
            said = _for_the_reader(completion.text, names, locale)
            conversation.add_assistant(said)
            return Answer(text=said, tool_calls=performed)

        for intent in completion.intents:
            conversation.add_tool_call(
                intent.call_id,
                intent.name,
                intent.arguments,
                provider_extra=intent.provider_extra,
            )
            if on_tool is not None:
                try:
                    await on_tool(intent.name)
                except Exception:  # noqa: BLE001 -- see below
                    # Blind on purpose: this is a progress notification, and
                    # every way it can fail (a closed socket, a slow channel,
                    # an adapter bug) has the same right answer. Letting any of
                    # them out would lose an answer the library has already
                    # paid a model to produce.
                    logger.debug("a channel could not be told about %s", intent.name)
            # Keyed on the arguments as the model wrote them, sorted so that
            # the same call with its keys in a different order still matches.
            signature = (intent.name, json.dumps(intent.arguments, sort_keys=True))

            if signature in already:
                logger.info(
                    "%s repeated with the same arguments; not running it again",
                    intent.name,
                )
                # A repeat costs a round even though it costs no query, and the
                # budget is about what the reader waits through. Counting it is
                # what turns the second identical ask into the last one: the
                # tool comes off the table and she answers from what she has.
                # Measured without it -- availability down, the model reached
                # for search twice more with byte-identical arguments and spent
                # two rounds being told it had already asked.
                if intent.name == SEARCH:
                    searches += 1
                conversation.add_tool_result(
                    intent.call_id, intent.name, already[signature]
                )
                continue

            if intent.name == AVAILABILITY and availability_failed:
                logger.info("availability asked again after failing; not run")
                conversation.add_tool_result(
                    intent.call_id, intent.name, _UNREACHABLE
                )
                continue

            if intent.name == SEARCH and searches >= SEARCHES_PER_TURN:
                logger.info(
                    "search %d asked for and not run; the turn's budget is %d",
                    searches + 1,
                    SEARCHES_PER_TURN,
                )
                conversation.add_tool_result(intent.call_id, intent.name, _SPENT)
                continue

            call = await tools.call(intent.name, intent.arguments)
            performed.append(call)
            if intent.name == SEARCH:
                searches += 1
            if intent.name == AVAILABILITY and call.failed:
                availability_failed = True
            result = as_tool_message(call)
            conversation.add_tool_result(intent.call_id, intent.name, result)

            # Recorded only after it ran, and holding the refusal rather than
            # the result -- see `already`.
            already[signature] = json.dumps(
                {
                    "repeated_call": (
                        f"You already called {intent.name} with these exact "
                        "arguments in this conversation, and it returned what "
                        "is above. Running it again returns the same thing. "
                        "Answer from what you have, or call it with different "
                        "arguments."
                    )
                },
                ensure_ascii=False,
            )

        logger.info(
            "round %d: %d tool call(s)", round_number + 1, len(completion.intents)
        )

    # Out of rounds, with the model still asking for tools. Discarding what it
    # found would be the worst of the options: by now a search has usually run
    # and an availability check has usually followed, and the answer is sitting
    # in the history unspoken. Observed exactly that -- four rounds spent, Dom
    # Casmurro found, availability checked, and a canned sentence returned.
    #
    # So: one more call, with the tools withdrawn. The model cannot ask for
    # anything else and has to answer from what it already has.
    logger.warning("the loop hit %d rounds; asking for an answer without tools", max_rounds)
    try:
        final = await model.complete(conversation.as_wire() + [_WITHDRAWN], tools=None)
    except ModelUnavailable:
        unavailable = _in(_UNAVAILABLE, locale)
        conversation.add_assistant(unavailable)
        return Answer(text=unavailable, tool_calls=performed, truncated=True)

    # The likeliest place for tool-call syntax as prose: the tools have just
    # been taken away from a model that spent every round asking for them.
    text = _for_the_reader(final.text, names, locale)
    conversation.add_assistant(text)
    return Answer(text=text, tool_calls=performed, truncated=True)
