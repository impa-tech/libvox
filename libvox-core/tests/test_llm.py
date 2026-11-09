"""The model connection.

No network: a mock transport answers, so what is under test is how a real
endpoint's answers are read — including the ones models actually get wrong.
"""

from __future__ import annotations

import asyncio
import json

import httpx2
import pytest

from libvox_core.agent.llm import Model, ModelUnavailable


def answering(body: dict, *, status: int = 200) -> tuple[Model, list[dict]]:
    """A Model whose endpoint returns exactly this, and the requests it saw."""
    seen: list[dict] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(status, json=body)

    model = Model(
        "http://model.test/v1",
        model="qwen",
        api_key="sk-abc",
        transport=httpx2.MockTransport(respond),
    )
    return model, seen


def message(**fields) -> dict:
    return {"choices": [{"message": {"content": None, **fields}}]}


# --- reading an answer ------------------------------------------------------


async def test_plain_text_comes_back_as_text() -> None:
    model, _ = answering(message(content="Temos dois livros dele."))
    completion = await model.complete([{"role": "user", "content": "oi"}])

    assert completion.text == "Temos dois livros dele."
    assert not completion.wants_tools


async def test_a_tool_call_comes_back_as_an_intent_not_an_action() -> None:
    """The model asks; the Core executes. That indirection is what keeps the
    session credential out of the model's reach."""
    model, _ = answering(
        message(
            tool_calls=[
                {
                    "id": "call_1",
                    "function": {
                        "name": "search_catalog",
                        "arguments": '{"query": "Dürrenmatt", "limit": 3}',
                    },
                }
            ]
        )
    )
    completion = await model.complete([{"role": "user", "content": "oi"}])

    assert completion.wants_tools
    intent = completion.intents[0]
    assert intent.name == "search_catalog"
    assert intent.arguments == {"query": "Dürrenmatt", "limit": 3}


async def test_arguments_that_are_not_valid_json_do_not_crash_the_turn() -> None:
    """Small models get this wrong regularly. Empty arguments let the tool's
    own schema produce the refusal, which is where the useful message is."""
    model, _ = answering(
        message(
            tool_calls=[
                {"id": "c", "function": {"name": "search_catalog", "arguments": "{oops"}}
            ]
        )
    )
    completion = await model.complete([{"role": "user", "content": "oi"}])

    assert completion.intents[0].arguments == {}


# --- what is sent -----------------------------------------------------------


async def test_the_temperature_is_zero() -> None:
    """A librarian answering from records, not a writing assistant. Creativity
    here shows up as an invented shelf mark."""
    model, seen = answering(message(content="ok"))
    await model.complete([{"role": "user", "content": "oi"}])

    assert seen[0]["temperature"] == 0


async def test_tools_are_offered_when_there_are_any() -> None:
    model, seen = answering(message(content="ok"))
    schema = [{"type": "function", "function": {"name": "search_catalog"}}]
    await model.complete([{"role": "user", "content": "oi"}], tools=schema)

    assert seen[0]["tools"] == schema
    assert seen[0]["tool_choice"] == "auto"


# --- failure ----------------------------------------------------------------


async def test_an_endpoint_that_refuses_the_key_says_nothing_about_it() -> None:
    """A model endpoint rejecting our credential is an operator's problem, and
    must not become a sentence a patron reads."""
    model, _ = answering({"error": "invalid api key for sk-abc"}, status=401)
    with pytest.raises(ModelUnavailable) as raised:
        await model.complete([{"role": "user", "content": "oi"}])

    assert "sk-abc" not in str(raised.value)
    assert "401" not in str(raised.value)


async def test_a_body_with_no_choices_is_an_outage_not_an_empty_answer() -> None:
    """An empty answer would reach the patron as silence from the library."""
    model, _ = answering({"choices": []})
    with pytest.raises(ModelUnavailable):
        await model.complete([{"role": "user", "content": "oi"}])


def sending(extra_body: dict) -> list[dict]:
    """The payload a Model with this extra_body actually puts on the wire."""
    seen: list[dict] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        seen.append(json.loads(request.content))
        return httpx2.Response(200, json=message(content="ok"))

    model = Model(
        "http://model.test/v1",
        model="qwen",
        extra_body=extra_body,
        transport=httpx2.MockTransport(respond),
    )
    asyncio.run(model.complete([{"role": "user", "content": "oi"}]))
    return seen[0]


def test_extra_body_reaches_the_endpoint_unread() -> None:
    """Where a thinking control lives, and every vendor spells it differently.

    Granite takes enable_thinking and reasoning_effort, Gemini reasoning_effort,
    Ollama think. Knowing one here would mean knowing all of them forever, so
    this module knows none: it merges what it was given.
    """
    payload = sending({"enable_thinking": False, "reasoning_effort": "low"})

    assert payload["enable_thinking"] is False
    assert payload["reasoning_effort"] == "low"


def test_extra_body_cannot_overwrite_what_the_request_is() -> None:
    """Configuration is not allowed to change the message or the temperature.

    `temperature: 0` is a decision this module argues for: two identical
    questions about the same catalogue should not get two different answers,
    and creativity here shows up as an invented shelf mark. A deployment that
    could raise it from an environment variable would undo that quietly.
    """
    payload = sending({"model": "outro", "temperature": 1.5, "messages": []})

    assert payload["model"] == "qwen"
    assert payload["temperature"] == 0
    assert payload["messages"] == [{"role": "user", "content": "oi"}]


def test_no_extra_body_sends_exactly_what_it_always_sent() -> None:
    """The default path, unchanged: nothing added, nothing to confuse a strict
    endpoint that rejects fields it does not know."""
    assert set(sending({})) == {"model", "messages", "temperature"}
