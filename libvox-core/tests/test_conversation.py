"""What survives the context budget, and in what order.

Tool results are bulky — twenty bibliographic records outweigh the whole
conversation around them — so something has to go. What must never go is the
policy layer, which is the only text present on every turn and the place the
restriction lives.
"""

from __future__ import annotations

import json

from libvox_core.agent import prompt
from libvox_core.agent.conversation import Conversation, Message


def loaded(budget: int) -> Conversation:
    """A conversation with one old search and a lot of turns after it."""
    conversation = Conversation(prompt.build({}), budget_chars=budget)
    conversation.add_user("tem algo do Dürrenmatt?")
    conversation.add_tool_call("c1", "search_catalog", {"query": "Dürrenmatt"})
    conversation.add_tool_result(
        "c1",
        "search_catalog",
        json.dumps({"records": [{"biblio_id": 2, "title": "x" * 4000}]}),
    )
    conversation.add_assistant("Temos dois: Ein Engel e Der Besuch.")
    for i in range(6):
        conversation.add_user(f"pergunta {i}")
        conversation.add_assistant(f"resposta {i}")
    return conversation


def test_the_policy_layer_survives_truncation() -> None:
    """A trimming that dropped it would leave a model with a library's persona
    and none of the rules."""
    wire = loaded(budget=200).as_wire()

    assert wire[0]["role"] == "system"
    assert prompt.POLICY in wire[0]["content"]


def test_the_oldest_tool_result_goes_before_the_sentence_it_produced() -> None:
    """"We have two: Ein Engel and Der Besuch" keeps what mattered in a
    fraction of the space that twenty records took."""
    wire = loaded(budget=len(prompt.build({})) + 900).as_wire()
    rendered = json.dumps(wire, ensure_ascii=False)

    assert "xxxx" not in rendered, "the bulky tool result should have been dropped"
    assert "Temos dois" in rendered


def test_a_generous_budget_keeps_everything() -> None:
    conversation = loaded(budget=200_000)
    assert len(conversation.as_wire()) == len(conversation.messages) + 1


def test_a_tool_result_is_never_left_without_the_call_that_made_it() -> None:
    """Every OpenAI-compatible endpoint is confused by an orphan, and some
    refuse it outright."""
    wire = loaded(budget=len(prompt.build({})) + 600).as_wire()

    called = {
        call["id"]
        for message in wire
        if message.get("role") == "assistant"
        for call in message.get("tool_calls") or []
    }
    for message in wire:
        if message.get("role") == "tool":
            assert message["tool_call_id"] in called


def test_the_newest_turn_is_the_one_that_survives() -> None:
    """What is kept has to be what the current question needs."""
    wire = loaded(budget=len(prompt.build({})) + 400).as_wire()
    assert "resposta 5" in json.dumps(wire, ensure_ascii=False)


def test_a_tool_call_and_its_result_render_the_way_the_api_expects() -> None:
    conversation = Conversation("system")
    conversation.add_tool_call("c1", "search_catalog", {"query": "x"})
    conversation.add_tool_result("c1", "search_catalog", '{"records": []}')

    wire = conversation.as_wire()
    assert wire[1]["tool_calls"][0]["function"]["name"] == "search_catalog"
    assert wire[2] == {
        "role": "tool",
        "tool_call_id": "c1",
        "content": '{"records": []}',
    }


def test_a_message_carries_no_field_that_names_a_person() -> None:
    assert not {f for f in Message.__slots__} & {
        "user_id", "patron", "email", "ip", "phone", "author_id",
    }


def test_tool_arguments_go_out_as_a_json_string_not_an_object() -> None:
    """The API parses this field itself.

    Handing it a dict answers `400 the JSON object must be str, bytes or
    bytearray, not dict` -- the endpoint's own json.loads complaining. A fake
    model never produces that, so this cost a live request to find.
    """
    conversation = Conversation("system")
    conversation.add_tool_call("c1", "search_catalog", {"query": "Machado", "limit": 3})

    arguments = conversation.as_wire()[1]["tool_calls"][0]["function"]["arguments"]

    assert isinstance(arguments, str)
    assert json.loads(arguments) == {"query": "Machado", "limit": 3}


def test_a_call_with_no_arguments_still_sends_a_string() -> None:
    conversation = Conversation("system")
    conversation.add_tool_call("c1", "search_catalog", {})
    arguments = conversation.as_wire()[1]["tool_calls"][0]["function"]["arguments"]
    assert arguments == "{}"


def test_accents_survive_the_round_trip() -> None:
    """A catalog in Portuguese is most of the queries this will ever carry."""
    conversation = Conversation("system")
    conversation.add_tool_call("c1", "search_catalog", {"query": "Dürrenmatt ação"})
    arguments = conversation.as_wire()[1]["tool_calls"][0]["function"]["arguments"]
    assert json.loads(arguments)["query"] == "Dürrenmatt ação"


def test_what_the_provider_attached_goes_back_inside_the_tool_call() -> None:
    """Gemini signs the reasoning behind a tool call and validates it back.

    Its thinking models return the signature under `extra_content`, outside the
    OpenAI schema, and refuse the *next* request without it:

        400 Function call is missing a thought_signature in functionCall parts

    Observed exactly that: the search ran, and the call carrying its result
    died. What the Core knows about the contents is nothing, on purpose.
    """
    signed = {"google": {"thought_signature": "Ct4BAdHt1m8xY..."}}
    conversation = Conversation("system")
    conversation.add_tool_call(
        "c1", "search_catalog", {"query": "x"}, provider_extra=signed
    )

    call = conversation.as_wire()[-1]["tool_calls"][0]

    assert call["extra_content"] == signed
    assert call["function"]["name"] == "search_catalog"


def test_a_call_with_nothing_attached_carries_no_such_key() -> None:
    """The half that protects every other endpoint.

    A field the API does not know is a 400 from a strict one -- which is the
    failure this exists to fix, not to cause. Absent means absent, not null.
    """
    conversation = Conversation("system")
    conversation.add_tool_call("c1", "search_catalog", {"query": "x"})

    assert "extra_content" not in conversation.as_wire()[-1]["tool_calls"][0]
