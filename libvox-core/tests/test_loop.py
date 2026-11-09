"""The conversation loop, and what MARC.IA remembers between turns.

No model and no MCP server: a FakeModel returns scripted intents, because the
cases that matter are the ones a real model produces when it goes wrong — a
tool that does not exist, an id no search returned, a chain that never ends.
"""

from __future__ import annotations

import json

from test_agent_tools import FakeClient

from libvox_core.agent import prompt
from libvox_core.agent.conversation import Conversation
from libvox_core.agent.llm import Completion, ModelUnavailable, ToolIntent
from libvox_core.agent.loop import SEARCHES_PER_TURN, _UNAVAILABLE, _in, answer
from libvox_core.agent.tools import ToolBox


class FakeModel:
    """Returns scripted completions, recording what it was sent."""

    def __init__(self, *completions: Completion, unavailable: bool = False) -> None:
        self._completions = list(completions)
        self._unavailable = unavailable
        self.sent: list[list[dict]] = []

    async def complete(self, messages, *, tools=None):
        self.sent.append(messages)
        self.offered = tools
        if self._unavailable:
            raise ModelUnavailable("nope")
        if self._completions:
            return self._completions.pop(0)
        return Completion(text="pronto")


def says(text: str) -> Completion:
    return Completion(text=text)


def wants(name: str, **arguments) -> Completion:
    return Completion(intents=[ToolIntent("call_1", name, arguments)])


def fresh(**client_kwargs) -> tuple[Conversation, ToolBox]:
    return Conversation(prompt.build({})), ToolBox(FakeClient(**client_kwargs))


# --- the shape of a turn ----------------------------------------------------


async def test_an_answer_with_no_tools_comes_straight_back() -> None:
    conversation, tools = fresh()
    result = await answer(
        conversation, "bom dia", model=FakeModel(says("Bom dia!")), tools=tools
    )
    assert result.text == "Bom dia!"
    assert result.tool_calls == []


async def test_a_tool_intent_is_executed_by_the_core_and_fed_back() -> None:
    """The model asks; it never invokes anything itself."""
    conversation, tools = fresh(
        result={"index_status": "ready", "records": [{"biblio_id": 2}]}
    )
    model = FakeModel(wants("search_catalog", query="Dürrenmatt"), says("Temos um."))

    result = await answer(conversation, "tem Dürrenmatt?", model=model, tools=tools)

    assert result.text == "Temos um."
    assert [c.name for c in result.tool_calls] == ["search_catalog"]


async def test_the_system_prompt_leads_every_request() -> None:
    conversation, tools = fresh()
    model = FakeModel(says("ok"))
    await answer(conversation, "oi", model=model, tools=tools)

    first = model.sent[0][0]
    assert first["role"] == "system"
    assert prompt.POLICY in first["content"]


# --- what the second question knows -----------------------------------------


async def test_the_second_question_knows_which_book_the_first_was_about() -> None:
    """The reason a tool result is a message and not just prose.

    Without the tool line in the history, "the second one" has no biblio_id and
    the model can only invent one or search again.
    """
    conversation, tools = fresh(
        result={
            "index_status": "ready",
            "records": [{"biblio_id": 2, "title": "Ein Engel"},
                        {"biblio_id": 137, "title": "Gustav Mahler"}],
        }
    )
    await answer(
        conversation,
        "tem algo do Dürrenmatt?",
        model=FakeModel(wants("search_catalog", query="Dürrenmatt"),
                        says("Temos dois: Ein Engel e Gustav Mahler.")),
        tools=tools,
    )

    second = FakeModel(says("Sim, disponível."))
    await answer(conversation, "o segundo está disponível?", model=second, tools=tools)

    sent = json.dumps(second.sent[0], ensure_ascii=False)
    assert "137" in sent, "the id from the first turn did not survive into the second"


async def test_a_tool_result_travels_as_json_not_as_a_sentence() -> None:
    conversation, tools = fresh(
        result={"index_status": "ready", "records": [{"biblio_id": 42}]}
    )
    await answer(
        conversation, "x",
        model=FakeModel(wants("search_catalog", query="x"), says("ok")), tools=tools,
    )

    tool_messages = [m for m in conversation.as_wire() if m["role"] == "tool"]
    assert json.loads(tool_messages[0]["content"])["records"][0]["biblio_id"] == 42


# --- where the loop stops ---------------------------------------------------


async def test_the_loop_ends_and_says_so_instead_of_calling_forever() -> None:
    """A small model chains searches until the context bursts when a search
    finds nothing. Silence would read as the library having nothing.

    Two guards overlap here and the tighter one shows: `max_rounds` ends the
    turn, `SEARCHES_PER_TURN` stops the searching first. Ten were scripted,
    three rounds were allowed, and two searches reached the catalogue -- which
    is the point of having a budget as well as a limit. The turn still ends
    `truncated` with something to say, which is what this test is for.
    """
    conversation, tools = fresh()
    model = FakeModel(*[wants("search_catalog", query=f"try {i}") for i in range(10)])

    result = await answer(
        conversation, "tem algo?", model=model, tools=tools, max_rounds=3
    )

    assert result.truncated is True
    assert result.text
    assert len(result.tool_calls) == SEARCHES_PER_TURN


async def test_hitting_the_limit_asks_for_an_answer_rather_than_discarding_one() -> None:
    """Observed live: four rounds spent, Dom Casmurro found, availability
    checked, and a canned sentence returned. The answer was in the history the
    whole time -- the model just never got asked for it without tools in front
    of it.
    """
    conversation, tools = fresh()
    model = FakeModel(
        wants("search_catalog", query="a"),
        wants("search_catalog", query="b"),
        says("Temos Dom Casmurro, sem exemplares livres."),
    )

    result = await answer(
        conversation, "tem Machado?", model=model, tools=tools, max_rounds=2
    )

    assert result.truncated is True
    assert result.text == "Temos Dom Casmurro, sem exemplares livres."
    # The last call is the one without tools: the model cannot ask for more.
    assert len(model.sent) == 3


async def test_a_model_that_says_nothing_at_the_limit_still_says_something() -> None:
    conversation, tools = fresh()
    model = FakeModel(
        wants("search_catalog", query="a"), wants("search_catalog", query="b"),
        says(""),
    )
    result = await answer(
        conversation, "tem algo?", model=model, tools=tools, max_rounds=2
    )
    assert result.text


async def test_a_model_outage_is_a_sentence_and_not_a_crash() -> None:
    conversation, tools = fresh()
    result = await answer(
        conversation, "oi", model=FakeModel(unavailable=True), tools=tools
    )
    assert "tente de novo" in result.text.lower()
    assert result.truncated is False


async def test_the_loop_s_own_sentences_are_in_the_reader_s_language() -> None:
    """They were English constants, and nobody noticed while they were rare.

    Guarding the reader from tool-call syntax made the fallback routine, and a
    reader who had asked about culinária in Portuguese was answered "I looked
    several times and did not manage to finish."
    """
    for locale, expected in (("en", "try again"), ("pt-BR", "tento de novo")):
        conversation, tools = fresh()
        model = FakeModel(
            wants("search_catalog", query="a"),
            wants("search_catalog", query="b"),
            says(""),
        )
        result = await answer(
            conversation, "tem algo?", model=model, tools=tools,
            max_rounds=2, locale=locale,
        )
        assert expected in result.text.lower(), locale

    # A library configured for a language the loop does not have falls back to
    # the same default the persona does, rather than to English by accident.
    conversation, tools = fresh()
    result = await answer(
        conversation, "oi", model=FakeModel(unavailable=True), tools=tools,
        locale="fr",
    )
    assert "tente de novo" in result.text.lower()


# --- what the model is not allowed to do ------------------------------------


async def test_a_tool_the_model_invented_is_refused_before_anything_runs() -> None:
    conversation, tools = fresh()
    client = tools._client
    model = FakeModel(wants("read_borrower_record", patron="maria"), says("Não posso."))

    result = await answer(conversation, "quem pegou esse livro?", model=model, tools=tools)

    assert client.calls == []
    assert result.tool_calls[0].failed


async def test_a_tool_failure_reaches_the_model_without_operational_detail() -> None:
    conversation, tools = fresh(
        raises=RuntimeError("403 from http://koha.internal:8081, needs circulate")
    )
    await answer(
        conversation, "x",
        model=FakeModel(wants("search_catalog", query="x"), says("ok")), tools=tools,
    )

    sent = json.dumps(conversation.as_wire(), ensure_ascii=False)
    for leak in ("koha.internal", "circulate", "403", "8081"):
        assert leak not in sent


# --- what the conversation is, and is not -----------------------------------


async def test_the_conversation_carries_no_person() -> None:
    """Scoped to a session, never to a reader. The non-goal made checkable."""
    conversation, tools = fresh()
    await answer(conversation, "meu nome é Ana", model=FakeModel(says("Olá!")), tools=tools)

    from dataclasses import fields as dataclass_fields

    from libvox_core.agent.conversation import Conversation as Conv
    from libvox_core.agent.conversation import Message

    carried = {f.name for f in dataclass_fields(Message)} | {
        f.name for f in dataclass_fields(Conv)
    }
    assert not carried & {
        "user_id", "patron", "patron_id", "email", "ip", "phone", "subject", "name",
    }


async def test_the_session_credential_never_appears_in_what_is_sent() -> None:
    conversation, tools = fresh()
    model = FakeModel(says("ok"))
    await answer(conversation, "oi", model=model, tools=tools)

    sent = json.dumps(model.sent[0])
    assert "Bearer" not in sent and "authorization" not in sent.lower()


# --- tool-call syntax written as prose --------------------------------------

_LEAKED = """<tool_call>
<function=search_catalog>
<parameter=query>
comida alimento pratos
</parameter>
</function>
</tool_call>"""


async def test_a_tool_call_written_as_prose_never_reaches_a_reader() -> None:
    """Seen on the widget, in Portuguese, twice in four tries.

    The last call withdraws the tools, and a small model asked to answer
    without them sometimes writes one anyway -- as text, with no structured
    call attached. The loop read that as its final words and handed a reader
    `<tool_call><function=search_catalog>`, which is worse than saying nothing.
    """
    conversation, tools = fresh()
    model = FakeModel(
        wants("search_catalog", query="a"),
        wants("search_catalog", query="b"),
        says(_LEAKED),
    )

    result = await answer(
        conversation, "quero algo sobre culinária", model=model, tools=tools,
        max_rounds=2,
    )

    assert "<tool_call" not in result.text
    assert "search_catalog" not in result.text
    assert result.text  # something is still said
    # And it is not quietly kept in the history either, where the next turn
    # would read it back as an example of how to answer.
    assert all("<tool_call" not in (m.content or "") for m in conversation.messages)


async def test_a_real_sentence_survives_the_call_appended_after_it() -> None:
    """The model usually answers *and then* asks for a tool it is not entitled
    to. Dropping the whole reply would throw away an answer that was already
    right, so only the tail goes."""
    conversation, tools = fresh()
    model = FakeModel(says("Temos dois exemplares.\n" + _LEAKED))

    result = await answer(
        conversation, "tem?", model=model, tools=tools, max_rounds=2
    )

    assert result.text == "Temos dois exemplares."


async def test_a_reply_that_is_nothing_but_a_call_still_says_something() -> None:
    """Cutting the tail can leave nothing at all, and silence reads as a widget
    that broke rather than a search that did not finish."""
    conversation, tools = fresh()
    model = FakeModel(says(_LEAKED))

    result = await answer(
        conversation, "tem?", model=model, tools=tools, max_rounds=2
    )

    assert result.text.strip()
    assert "<tool_call" not in result.text


async def test_the_guard_catches_a_call_that_opens_with_a_reasoning_tag() -> None:
    """The first version of the guard listed opening tool-call tags only, and
    this reply -- from hermes3:8b, on the widget path -- walked past it onto a
    reader's screen. It opens with a scratchpad tag and closes with the tool
    tag, so there is no opening marker to find.
    """
    conversation, tools = fresh()
    leaked = (
        '<THINKING> {"arguments": {"query": "opus posthumous", "limit": 5}, '
        '"name": "search_catalog"} </tool_call>'
    )
    model = FakeModel(says(leaked))

    result = await answer(conversation, "tem?", model=model, tools=tools)

    assert "search_catalog" not in result.text
    assert "THINKING" not in result.text
    assert result.text.strip()


async def test_a_reasoning_scratchpad_is_not_a_reader_s_business_either() -> None:
    """Not a tool call, and just as much not an answer."""
    conversation, tools = fresh()
    model = FakeModel(says("Temos dois exemplares.<think>should I check?</think>"))

    result = await answer(conversation, "tem?", model=model, tools=tools)

    assert result.text == "Temos dois exemplares."


async def test_a_bare_call_envelope_with_no_tag_at_all_is_caught() -> None:
    """No markup to recognize -- just the JSON a tool call is made of. Anchored
    at the start, so an answer that quotes JSON further in still survives."""
    conversation, tools = fresh()
    model = FakeModel(says('{"name": "search_catalog", "arguments": {"query": "x"}}'))

    result = await answer(conversation, "tem?", model=model, tools=tools)

    assert "search_catalog" not in result.text
    assert result.text.strip()


async def test_an_invented_tag_name_does_not_get_a_free_pass() -> None:
    """The third shape, and the one that settled the design.

    A tag list caught `<tool_call>`, then had to be widened for `<THINKING>`,
    and was walked past again by `<SCRATCHPAD>` -- a tag nobody had seen, from
    the same model, on the very next run. Each fix held until the next batch.

    The tool's own name is the signal instead. `search_catalog` is an internal
    identifier; a sentence written for a reader has no use for one, whatever
    tag it happens to be wearing.
    """
    conversation, tools = fresh()
    leaked = (
        '<SCRATCHPAD> {"arguments": {"query": "opus posthumous"}, '
        '"name": "search_catalog"}'
    )
    model = FakeModel(says(leaked))

    result = await answer(conversation, "tem?", model=model, tools=tools)

    assert "search_catalog" not in result.text
    assert "SCRATCHPAD" not in result.text
    assert result.text.strip()


async def test_a_sentence_that_precedes_an_invented_tag_is_still_lost() -> None:
    """Deliberate, and the one place this is stricter than the tag list was.

    Cutting at the earliest bracket costs a sentence that came before an
    envelope wearing an unknown tag. That is the right trade: a reader shown
    half an answer followed by JSON has been told the system is broken, and
    prose naming a tool has already stopped being prose.
    """
    conversation, tools = fresh()
    model = FakeModel(says('Deixa eu ver. <XYZ>{"name": "search_catalog"}</XYZ>'))

    result = await answer(conversation, "tem?", model=model, tools=tools)

    assert "search_catalog" not in result.text


async def test_an_answer_that_names_no_tool_survives_intact() -> None:
    """The guard must not eat ordinary answers, including ones with brackets."""
    conversation, tools = fresh()
    model = FakeModel(says("Temos 2 exemplares (um deles {reservado})."))

    result = await answer(conversation, "tem?", model=model, tools=tools)

    assert result.text == "Temos 2 exemplares (um deles {reservado})."


# --- the invariant a rating depends on --------------------------------------


async def test_every_path_that_answers_a_reader_stores_what_it_said() -> None:
    """One assistant message per answer, on all four returns.

    This is not tidiness. A reader who is shown a sentence the log does not
    contain puts the transcript one turn behind the conversation, and anything
    that counts answers — a rating naming "the third one" — then attaches to
    the wrong message, silently, in the direction that lands a thumbs-down on
    the previous answer.

    `ModelUnavailable` on the first round was the one that did not store, and
    it was invisible until something needed to count: nineteen of a hundred and
    seventy-six conversations carried such a turn, all from an afternoon when
    the model endpoint answered 400 to every request.
    """
    outage = FakeModel(unavailable=True)
    plain = FakeModel(says("Temos dois exemplares."))
    machinery = FakeModel(says("<tool_call><function=search_catalog>"))
    exhausted = FakeModel(
        wants("search_catalog", query="a"),
        wants("search_catalog", query="b"),
        says("Achei, sim."),
    )

    for label, model in (
        ("model outage", outage),
        ("a plain answer", plain),
        ("machinery as prose", machinery),
        ("out of rounds", exhausted),
    ):
        conversation, tools = fresh()
        result = await answer(
            conversation, "tem?", model=model, tools=tools, max_rounds=2
        )

        spoken = [
            m for m in conversation.messages
            if m.role == "assistant" and not m.tool_name
        ]

        assert len(spoken) == 1, f"{label}: stored {len(spoken)}, expected 1"
        assert spoken[0].content == result.text, label
        assert result.text.strip(), label


async def test_the_final_call_says_the_tools_are_gone() -> None:
    """Withdrawing them silently was a question, and not the one anyone meant.

    A reader asking for chess books was answered with *"I've already searched
    for xadrez multiple times… Let me search in English and Spanish now"* -- in
    English, citing a rule number. The model was not leaking reasoning: it read
    a history full of searching, found no tools, and answered what the history
    implied came next, because nothing asked it anything else.
    """
    conversation, tools = fresh()
    model = FakeModel(*[wants("search_catalog", query=f"try {i}") for i in range(10)])

    await answer(conversation, "tem xadrez?", model=model, tools=tools, max_rounds=2)

    assert model.offered is None, "the final call still offered tools"
    assert "tools are gone" in model.sent[-1][-1]["content"]


async def test_that_instruction_is_never_stored() -> None:
    """Stored, it would come back on the next turn and appear on the operator's
    screen as something the library said."""
    conversation, tools = fresh()
    model = FakeModel(*[wants("search_catalog", query=f"try {i}") for i in range(10)])

    await answer(conversation, "tem xadrez?", model=model, tools=tools, max_rounds=2)

    assert not any("tools are gone" in m.content for m in conversation.messages)

    # Precisely: the payload was the stored conversation as it stood, plus the
    # instruction and nothing else. `as_wire()` now ends with the answer that
    # call produced, so the comparison drops it.
    assert model.sent[-1][:-1] == conversation.as_wire()[:-1]


async def test_the_same_call_twice_does_not_run_twice() -> None:
    """A rule the model can ignore is not a bound.

    Policy rule 7 says never run the same search twice. A reader asking about
    pokemon watched `search_catalog{"query": "pokemon"}` run six times,
    identically, until the round limit -- triggered by `truncated: true`, which
    rule 8 says to report as "at least five" and which the model read as a
    reason to try again. The same query returns the same five, forever.
    """
    conversation, tools = fresh()
    model = FakeModel(*[wants("search_catalog", query="pokemon") for _ in range(6)])

    result = await answer(
        conversation, "tem algo sobre pokemon?", model=model, tools=tools, max_rounds=4
    )

    # Four rounds asked for it; the index saw one.
    assert len(result.tool_calls) == 1


async def test_a_third_search_is_refused_by_the_loop_and_not_by_the_prompt() -> None:
    """Rule 6 says twice is the limit. Prose did not hold it.

    Measured the moment rule 6 was widened to let her choose her own words:
    `tem algum livro sobre culinária japonesa?` went from one search to five --
    `culinária japonesa`, `Japanese cooking`, `japonesa`, `cozinha japonesa`,
    `cozinha` -- and the last returned a Ruby cookbook. 56 seconds to say no,
    against 3.7 before. That is the `Opus posthumous` failure again, and this
    time the freedom is wanted, so the ceiling moved somewhere the model cannot
    argue with it.

    Three different queries, so `already` does not catch them: the budget is
    what stops the third, and it is a different guard for a different failure.
    """
    client = FakeClient()
    conversation, tools = Conversation(prompt.build({})), ToolBox(client)
    model = FakeModel(
        wants("search_catalog", query="culinária japonesa"),
        wants("search_catalog", query="Japanese cooking"),
        wants("search_catalog", query="cozinha"),
        says("Não encontrei nada sobre culinária japonesa."),
    )

    await answer(conversation, "tem algo sobre culinária japonesa?",
                 model=model, tools=tools, max_rounds=5)

    asked = [m for m in conversation.messages
             if m.role == "assistant" and m.tool_name == "search_catalog"]
    spent = [m for m in conversation.messages
             if m.role == "tool" and "searches_spent" in m.content]
    reached = [c for c in client.calls if c[0] == "search_catalog"]

    # Three were asked for; two reached the catalogue.
    assert len(asked) == 3
    assert len(reached) == 2
    assert len(spent) == 1

    # And it is told what to do instead, not merely refused -- silence reads as
    # a broken tool, and rule 10 would have her blame the catalogue for it.
    assert "say the library does not have it" in spent[0].content


async def test_availability_is_not_asked_twice_after_it_fails() -> None:
    """A failing ILS is the worst moment to send it more requests.

    Measured against a library whose Koha had stopped issuing tokens: asked
    about five records, the model called `check_availability` five times --
    all five ids, then three, then one, then another, then the last three --
    hunting for a subset that would work. Every set is a different signature,
    so the repeat guard never saw any of them, and the turn burned its rounds
    and ten minutes before giving up.

    The answer never depended on it. Rule 10 says a failed tool means telling
    the reader the catalogue could not answer, and that sentence was available
    after the first failure exactly as after the fifth.
    """
    client = FakeClient(is_error=True, error_text="nope")
    conversation, tools = Conversation(prompt.build({})), ToolBox(client)
    model = FakeModel(
        wants("check_availability", biblio_ids=[1, 2, 3]),
        wants("check_availability", biblio_ids=[1]),
        wants("check_availability", biblio_ids=[2, 3]),
        says("Não consegui falar com o catálogo agora."),
    )

    await answer(conversation, "estão disponíveis?", model=model, tools=tools,
                 max_rounds=5)

    reached = [c for c in client.calls if c[0] == "check_availability"]
    refused = [m for m in conversation.messages
               if m.role == "tool" and "catalog_unreachable" in m.content]

    # Three were asked for; one reached the library.
    assert len(reached) == 1
    assert len(refused) == 2


async def test_a_library_that_recovers_is_asked_again_next_turn() -> None:
    """Per turn, like the search budget beside it. A failure in one question
    must not leave the next one unable to check a shelf."""
    client = FakeClient()
    conversation, tools = Conversation(prompt.build({})), ToolBox(client)

    await answer(conversation, "e agora?",
                 model=FakeModel(wants("check_availability", biblio_ids=[1]),
                                 says("ok")),
                 tools=tools, max_rounds=3)
    await answer(conversation, "e este?",
                 model=FakeModel(wants("check_availability", biblio_ids=[2]),
                                 says("ok")),
                 tools=tools, max_rounds=3)

    assert len([c for c in client.calls if c[0] == "check_availability"]) == 2


async def test_availability_is_not_rationed() -> None:
    """The budget is for searching. Availability answers about records already
    found, and rule 8 forbids reusing an earlier answer -- so a conversation
    that checks three books checks three times."""
    client = FakeClient()
    conversation, tools = Conversation(prompt.build({})), ToolBox(client)
    model = FakeModel(
        wants("search_catalog", query="xadrez"),
        wants("check_availability", biblio_ids=[1]),
        wants("check_availability", biblio_ids=[2]),
        wants("check_availability", biblio_ids=[3]),
        says("pronto"),
    )

    await answer(conversation, "e esses três?", model=model, tools=tools, max_rounds=6)

    assert not [m for m in conversation.messages
                if m.role == "tool" and "searches_spent" in m.content]
    assert len([c for c in client.calls if c[0] == "check_availability"]) == 3


async def test_the_repeat_is_told_something_new() -> None:
    """Handing back the earlier result would leave the model's input unchanged,
    and unchanged input is what a loop is."""
    conversation, tools = fresh()
    model = FakeModel(*[wants("search_catalog", query="pokemon") for _ in range(4)])

    await answer(conversation, "tem pokemon?", model=model, tools=tools, max_rounds=3)

    repeats = [m for m in conversation.messages
               if m.role == "tool" and "repeated_call" in m.content]
    assert repeats, "the second identical call was answered with the first result"
    assert "different arguments" in repeats[0].content


async def test_a_different_argument_still_runs() -> None:
    """The guard is on identity, not on the tool. Varying the term is work."""
    conversation, tools = fresh()
    model = FakeModel(
        wants("search_catalog", query="pokemon"),
        wants("search_catalog", query="pokémon"),
        says("Dois títulos."),
    )

    result = await answer(conversation, "tem?", model=model, tools=tools, max_rounds=4)

    assert len(result.tool_calls) == 2


async def test_the_guard_forgets_between_turns() -> None:
    """Rule 9: availability changes minute to minute and must never be reused
    from earlier in the conversation. So a reader's second question has to be
    able to check the same book again -- the guard bounds a runaway inside a
    turn, and hoisting it onto the conversation would repeal that rule."""
    conversation, tools = fresh()

    model = FakeModel(wants("check_availability", biblio_ids=[89]), says("Disponível."))
    first = await answer(conversation, "o 89 está?", model=model, tools=tools)

    model = FakeModel(wants("check_availability", biblio_ids=[89]), says("Ainda está."))
    second = await answer(conversation, "e agora?", model=model, tools=tools)

    assert len(first.tool_calls) == 1
    assert len(second.tool_calls) == 1, "the second turn was refused as a repeat"


async def test_a_reasoning_dump_is_refused_rather_than_shown() -> None:
    """A reader received the model's entire monologue -- thousands of words
    reasoning about this system's own numbered rules, drafting and redrafting a
    sentence, in English. The endpoint had put the reasoning in `content`
    instead of `reasoning_content`, and `_spoken` had nothing to cut on: the
    answer, if there was one, sat at the end of 15 KB with no delimiter.

    Refusing is not extracting. Where the answer is cannot be known; that this
    is not one can.
    """
    conversation, tools = fresh()
    dump = "Thinking Process:\n\n1. Analyze the Request:\n   * Rule 8 says…\n" * 200
    model = FakeModel(says(dump))

    result = await answer(conversation, "tem pokemon?", model=model, tools=tools)

    assert "Thinking Process" not in result.text
    assert result.text == _in(_UNAVAILABLE, None)
    # And the transcript matches what the reader saw, as every other path does.
    assert conversation.messages[-1].content == result.text


async def test_a_real_answer_that_merely_mentions_thinking_is_untouched() -> None:
    """Leading position is the whole test, so the marker is not a banned word."""
    conversation, tools = fresh()
    said = "O livro trata do thinking process de Kahneman. Há 2 exemplares."
    model = FakeModel(says(said))

    result = await answer(conversation, "tem?", model=model, tools=tools)

    assert result.text == said


async def test_length_alone_is_not_the_signal() -> None:
    """A library with a long shelf list is a long answer, not a dump."""
    conversation, tools = fresh()
    said = "Encontrei estes títulos: " + ", ".join(f"Livro {i}" for i in range(400))
    model = FakeModel(says(said))

    result = await answer(conversation, "o que tem?", model=model, tools=tools)

    assert result.text == said
