"""The tool registry, which is whatever /libvox-mcp declares.

No database and no network: a fake MCP client stands in, so what is under test
is the agent's side of the boundary — what it offers the model, what it refuses
to execute, and what it lets a failure say.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

import pytest

from libvox_core.agent import tools


@dataclass
class FakeTool:
    name: str
    description: str = "does a thing"
    input_schema: dict | None = None


@dataclass
class FakeText:
    text: str


@dataclass
class FakeResult:
    structured_content: dict
    is_error: bool = False
    content: list | None = None


class FakeListing:
    def __init__(self, names: list[str]) -> None:
        self.tools = [
            FakeTool(n, input_schema={"type": "object", "properties": {}}) for n in names
        ]


class FakeClient:
    """Stands in for a live MCP connection, recording what was asked of it."""

    def __init__(self, names: list[str] | None = None, *, result: Any = None,
                 raises: Exception | None = None, is_error: bool = False,
                 error_text: str | None = None) -> None:
        self._error_text = error_text
        self._names = names if names is not None else sorted(tools.EXPECTED_TOOLS)
        self._result = result if result is not None else {"records": []}
        self._raises = raises
        self._is_error = is_error
        self.calls: list[tuple[str, dict]] = []
        self.listings = 0

    async def list_tools(self) -> FakeListing:
        self.listings += 1
        return FakeListing(self._names)

    async def call_tool(self, name: str, arguments: dict) -> FakeResult:
        self.calls.append((name, arguments))
        if self._raises:
            raise self._raises
        return FakeResult(
            self._result,
            is_error=self._is_error,
            content=[FakeText(self._error_text)] if self._error_text else None,
        )


# --- the registry -----------------------------------------------------------


async def test_the_tools_are_whatever_the_mcp_layer_declares() -> None:
    """No local list. That is the one enforcement here that is structural."""
    box = tools.ToolBox(FakeClient())
    offered = {s["function"]["name"] for s in await box.schemas()}
    assert offered == tools.EXPECTED_TOOLS


async def test_marcia_has_no_tool_the_mcp_layer_did_not_publish() -> None:
    """She cannot search the web because there is nothing to call."""
    box = tools.ToolBox(FakeClient(["search_catalog"]))
    offered = {s["function"]["name"] for s in await box.schemas()}
    assert "fetch_url" not in offered and "browse" not in offered
    assert offered == {"search_catalog"}


async def test_an_unexpected_tool_upstream_is_reported_not_swallowed(caplog) -> None:
    """A tool surface that widens without a decision is how a model gets an
    ability nobody meant to give it."""
    box = tools.ToolBox(FakeClient([*tools.EXPECTED_TOOLS, "get_biblio"]))
    await box.schemas()
    assert "get_biblio" in caplog.text


async def test_the_tool_list_is_fetched_once_per_session() -> None:
    """A set that changed mid-conversation would offer the model something in
    turn one that no longer exists in turn three."""
    client = FakeClient()
    box = tools.ToolBox(client)
    await box.schemas()
    await box.schemas()
    assert client.listings == 1


# --- what the model is allowed to execute -----------------------------------


async def test_a_tool_the_model_invented_is_refused_before_anything_runs() -> None:
    client = FakeClient()
    box = tools.ToolBox(client)

    call = await box.call("read_borrower_record", {"patron": "maria"})

    assert call.failed
    assert client.calls == []
    assert "no tool called" in call.error


async def test_a_real_call_reaches_the_tool_layer_verbatim() -> None:
    client = FakeClient(result={"index_status": "ready", "records": []})
    box = tools.ToolBox(client)

    await box.call("search_catalog", {"query": "Dürrenmatt", "limit": 3})

    assert client.calls == [("search_catalog", {"query": "Dürrenmatt", "limit": 3})]


# --- what a failure is allowed to say ---------------------------------------


async def test_a_failure_reaches_the_model_without_a_url_or_a_permission() -> None:
    """Operational detail belongs in the operator's log. The MCP layer already
    collapses it on its side; this must not reintroduce it."""
    client = FakeClient(
        raises=RuntimeError(
            "403 from http://koha.internal:8081 needs circulate permission"
        )
    )
    call = await tools.ToolBox(client).call("search_catalog", {"query": "x"})

    assert call.failed
    for leak in ("403", "koha.internal", "circulate", "8081"):
        assert leak not in call.error


async def test_a_tool_error_result_is_a_failure_and_not_an_answer() -> None:
    """An error result read as data would have the model reporting {} as the
    library's holdings."""
    client = FakeClient(result={"detail": "nope"}, is_error=True)
    call = await tools.ToolBox(client).call("search_catalog", {"query": "x"})
    assert call.failed


async def test_a_validation_error_reaches_the_model_so_it_can_correct_itself() -> None:
    """The one failure the model *can* fix.

    "limit: Input should be less than or equal to 20" tells it what to do;
    "the catalog could not answer" leaves it guessing, and it guesses by giving
    up. The MCP layer already decided what a caller may see, so re-sanitizing
    here discards the only correction the model was going to get.
    """
    client = FakeClient(
        is_error=True,
        error_text=(
            "Error executing tool search_catalog: 1 validation error\nlimit\n"
            "  Input should be less than or equal to 20"
        ),
    )
    call = await tools.ToolBox(client).call("search_catalog", {"limit": 500})

    assert call.failed
    assert "less than or equal to 20" in call.error


async def test_an_error_with_no_text_still_says_something() -> None:
    client = FakeClient(is_error=True)
    call = await tools.ToolBox(client).call("search_catalog", {"query": "x"})
    assert "could not answer" in call.error


async def test_a_failed_call_does_not_raise_into_the_loop() -> None:
    """A tool that failed is a turn the model handles, not a crash."""
    client = FakeClient(raises=RuntimeError("boom"))
    call = await tools.ToolBox(client).call("search_catalog", {"query": "x"})
    assert isinstance(call, tools.ToolCall)


# --- what the model reads back ----------------------------------------------


async def test_a_tool_result_reaches_the_model_as_json_it_can_read_later() -> None:
    """This is what makes "is the second one available?" answerable without
    searching again: the biblio_id is in the history, not just in prose."""
    client = FakeClient(
        result={"index_status": "ready", "records": [{"biblio_id": 137, "title": "X"}]}
    )
    call = await tools.ToolBox(client).call("search_catalog", {"query": "x"})

    message = json.loads(tools.as_tool_message(call))
    assert message["records"][0]["biblio_id"] == 137


async def test_a_failure_is_carried_as_data_too() -> None:
    call = tools.ToolCall("search_catalog", {}, error="The catalog could not answer.")
    assert json.loads(tools.as_tool_message(call)) == {
        "error": "The catalog could not answer."
    }


# --- the connection ---------------------------------------------------------


async def test_an_unreachable_tool_layer_names_no_url_to_the_caller() -> None:
    with pytest.raises(tools.ToolUnavailable) as raised:
        async with tools.connect("http://127.0.0.1:1/mcp", "tok"):
            pass
    assert "127.0.0.1:1" not in str(raised.value)
