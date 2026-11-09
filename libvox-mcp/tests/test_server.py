"""Tests for the /libvox-mcp tool surface.

Run against an in-memory client, so no network and no Core are needed:

    pytest
"""

from __future__ import annotations

from typing import Any

import pytest
from mcp import Client, MCPError
from mcp.server.auth.provider import AccessToken
from mcp.types import INVALID_REQUEST

from libvox_mcp import server as server_module
from libvox_mcp.auth import TENANT_CLAIM
from libvox_mcp.config import Settings
from libvox_mcp.models import (
    AvailabilityRecord,
    CatalogRecord,
    CheckAvailabilityResult,
    SearchCatalogResult,
)
from libvox_mcp.server import MAX_RESULTS

TENANT = "lib-furg"


class FakeCore:
    """Stands in for /libvox-core, recording what the MCP layer forwarded."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.index_status = "ready"

    async def aclose(self) -> None:
        pass

    async def search_catalog(self, **kwargs: Any) -> SearchCatalogResult:
        self.calls.append(("search_catalog", kwargs))
        return SearchCatalogResult(
            index_status=self.index_status,
            records=[
                CatalogRecord(
                    biblio_id=42,
                    title="Dom Casmurro",
                    author="Machado de Assis",
                    copyright_date=1899,
                )
            ],
        )

    async def check_availability(self, **kwargs: Any) -> CheckAvailabilityResult:
        self.calls.append(("check_availability", kwargs))
        return CheckAvailabilityResult(
            records=[
                AvailabilityRecord(
                    biblio_id=42,
                    status="available",
                    copies_total=3,
                    copies_available=1,
                    library="Biblioteca Central",
                    shelf="Acervo geral",
                    call_number="869.3 M149d",
                )
            ]
        )


@pytest.fixture
def core(monkeypatch: pytest.MonkeyPatch) -> FakeCore:
    """Replace the real Core client, keeping one instance we can inspect."""
    fake = FakeCore()
    monkeypatch.setattr(server_module, "CoreClient", lambda *a, **k: fake)
    return fake


@pytest.fixture
def authenticated(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate a credential that Core resolved to a tenant."""
    token = AccessToken(
        token="opaque-session-credential",
        client_id=TENANT,
        scopes=["catalog:read"],
        claims={TENANT_CLAIM: TENANT},
    )
    monkeypatch.setattr("libvox_mcp.auth.get_access_token", lambda: token)


@pytest.fixture
def mcp(core: FakeCore):
    return server_module.create_server(Settings())


async def test_declares_exactly_two_tools(mcp) -> None:
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    assert set(tools) == {"search_catalog", "check_availability"}
    assert tools["search_catalog"].annotations.read_only_hint is True


async def test_no_tool_accepts_a_tenant(mcp) -> None:
    """The rule the whole isolation design rests on.

    Any argument the model fills is attacker-controllable through the user's
    prompt, so tenant identity must not appear in a schema at all.
    """
    forbidden = {"tenant", "tenant_id", "library", "library_id", "ils_url", "url"}

    async with Client(mcp) as client:
        tools = (await client.list_tools()).tools

    for tool in tools:
        properties = set(tool.input_schema.get("properties", {}))
        assert not (properties & forbidden), (
            f"{tool.name} exposes tenant-selecting arguments: "
            f"{sorted(properties & forbidden)}"
        )


async def test_search_reports_index_status(mcp, core: FakeCore, authenticated) -> None:
    """A still-filling index must be distinguishable from an empty catalog."""
    core.index_status = "building"

    async with Client(mcp) as client:
        result = await client.call_tool("search_catalog", {"query": "Machado"})

    assert result.is_error is False
    assert result.structured_content["index_status"] == "building"


async def test_search_forwards_tenant_and_arguments(
    mcp, core: FakeCore, authenticated
) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_catalog", {"query": "Machado de Assis", "limit": 3}
        )

    name, kwargs = core.calls[-1]
    assert name == "search_catalog"
    assert kwargs["tenant_id"] == TENANT  # from the credential, not the model
    assert kwargs["query"] == "Machado de Assis"
    assert kwargs["limit"] == 3
    assert result.structured_content["records"][0]["biblio_id"] == 42


async def test_check_availability_forwards_ids(
    mcp, core: FakeCore, authenticated
) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("check_availability", {"biblio_ids": [42]})

    name, kwargs = core.calls[-1]
    assert name == "check_availability"
    assert kwargs["tenant_id"] == TENANT
    assert kwargs["biblio_ids"] == [42]
    assert result.structured_content["records"][0]["status"] == "available"


async def test_tool_call_without_credential_is_refused(mcp, core: FakeCore) -> None:
    """No credential, no tool call — and Core is never contacted.

    This surfaces as a protocol error rather than a tool result, which is the
    point: the host sees it, the model gets nothing to reason about, and no
    message about tenant resolution ever enters the conversation.
    """
    async with Client(mcp) as client:
        with pytest.raises(MCPError) as caught:
            await client.call_tool("search_catalog", {"query": "Machado"})

    assert caught.value.error.code == INVALID_REQUEST
    assert core.calls == []


async def test_a_limit_above_the_maximum_is_served_as_the_maximum(
    mcp, core: FakeCore, authenticated
) -> None:
    """Clamped, not refused, and the difference cost a round of the loop.

    This used to be `le=MAX_RESULTS` on the field, so a model asking for fifty
    got a Pydantic error rather than a search: one of four rounds spent on a
    bounds check, then a retry. Observed from qwen3.6:35b-a3b — the model that
    answers this system's benchmark question eight times out of eight — which
    is what settled it. A bound the best available model trips over is a bound
    in the wrong place.

    Nothing is protected by refusing. The cap is there because twenty records a
    model reads beat a hundred it skims, and `truncated` already reports that
    more matched.
    """
    async with Client(mcp) as client:
        await client.call_tool("search_catalog", {"query": "Machado", "limit": 500})

    assert core.calls[0][1]["limit"] == MAX_RESULTS


async def test_a_limit_of_zero_is_still_refused(
    mcp, core: FakeCore, authenticated
) -> None:
    """`ge=1` stays. Asking for no records is not a smaller page, it is a
    request with no answer, and clamping it upward would be inventing one."""
    async with Client(mcp, raise_exceptions=False) as client:
        result = await client.call_tool(
            "search_catalog", {"query": "Machado", "limit": 0}
        )

    assert result.is_error is True
    assert core.calls == []


async def test_the_instructions_name_every_tool_and_no_others(mcp) -> None:
    """The guidance a model reads must match the tools it is handed.

    Caught in the wild: check_availability was added to this backend and the
    instructions still said "this server cannot check whether a copy is
    available -- say so if asked". A model that believes it refuses a question
    it could have answered, which is the same confidently-wrong failure as
    answering one it could not, pointed the other way.

    Both directions are asserted. A tool nobody is told about goes uncalled; a
    tool named in prose but never registered is an error the model walks into.
    """
    import re

    # snake_case words in the prose that are field names, not tools.
    not_tools = {
        "index_status", "biblio_id", "biblio_ids", "item_type", "copyright_date",
        "search_result", "tool_selection",
    }

    async with Client(mcp) as client:
        registered = {t.name for t in (await client.list_tools()).tools}

    text = server_module.INSTRUCTIONS
    mentioned = {w for w in re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", text)}
    mentioned -= not_tools

    assert registered <= mentioned, (
        f"registered but never mentioned: {registered - mentioned}"
    )
    assert mentioned <= registered, (
        f"named in the instructions but not a tool: {mentioned - registered}"
    )


async def test_availability_tells_a_reader_where_in_the_building(
    mcp, core: FakeCore, authenticated
) -> None:
    """The library names a building; the shelf names where to walk inside it.

    A reader told only "Biblioteca Central" has to ask at the desk, which is the
    question MARC.IA was supposed to save them. Both travel, and the schema says
    to give both.
    """
    async with Client(mcp) as client:
        result = await client.call_tool("check_availability", {"biblio_ids": [42]})

    record = result.structured_content["records"][0]

    assert record["library"] == "Biblioteca Central"
    assert record["shelf"] == "Acervo geral"
    assert record["call_number"] == "869.3 M149d"


def test_the_shelf_is_described_as_a_place_and_not_a_code() -> None:
    """`GEN` is not an answer, and the description is what stops the model
    reporting one. It is also what keeps `shelf` from being read as the branch
    -- the mistake this contract already made once, under the name `location`.
    """
    field = AvailabilityRecord.model_fields["shelf"]

    assert "inside that building" in field.description
    assert "General stacks" in field.description


async def test_availability_is_clamped_to_a_handful(
    mcp, core: FakeCore, authenticated
) -> None:
    """The one bound in this system that protects somebody else.

    Every id here becomes a live HTTP request to the library's own Koha. Asked
    one question, a model checked every record its search returned: twenty
    sequential GETs to another institution's server over thirteen seconds, for
    an answer that named a handful.

    Clamped rather than refused, like `limit` on search -- a validation error
    costs the model a round of the loop and buys a retry. What differs is what
    the cap is for: that one protects the model's attention and reaches only our
    own index; this one reaches a library.
    """
    async with Client(mcp) as client:
        await client.call_tool(
            "check_availability", {"biblio_ids": list(range(1, 21))}
        )

    _, kwargs = core.calls[-1]
    assert kwargs["biblio_ids"] == [1, 2, 3, 4, 5]


async def test_the_model_is_told_the_calls_reach_the_library(mcp) -> None:
    """The number is a backstop; the description is what keeps a model from
    asking for twenty in the first place."""
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    described = str(tools["check_availability"].input_schema)
    assert "live request to the library's own server" in described
    assert "At most 5 are checked" in described


# --- the year, which no query can reach --------------------------------------


async def test_the_tool_publishes_a_year_range(mcp) -> None:
    """`copyright_date` comes back in every record and could never be searched
    for: it is not in the text vector, so "1992" as a query finds nothing."""
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    properties = tools["search_catalog"].input_schema.get("properties", {})
    assert {"year_from", "year_to"} <= set(properties)


async def test_a_search_does_not_have_to_carry_a_query(mcp) -> None:
    """"Are there any books from 1992?" has no searchable word in it.

    `query` was mandatory here, which also made the author-only and title-only
    searches the index has always supported unreachable by a model.
    """
    async with Client(mcp) as client:
        required = set(
            {t.name: t for t in (await client.list_tools()).tools}["search_catalog"]
            .input_schema.get("required", [])
        )

    assert "query" not in required


async def test_a_year_range_reaches_core(mcp, core: FakeCore, authenticated) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool(
            "search_catalog", {"year_from": 1990, "year_to": 1999}
        )

    _, kwargs = core.calls[-1]
    assert kwargs["year_from"] == 1990 and kwargs["year_to"] == 1999
    assert kwargs["query"] == ""
    assert result.is_error is False


async def test_the_model_is_told_a_year_filter_misses_undated_records(mcp) -> None:
    """The number without the caveat reads as the whole of what the library
    holds, on a catalogue where most records were never dated."""
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    assert "undated_records" in (tools["search_catalog"].description or "")


@pytest.mark.anyio
async def test_the_model_is_not_told_which_field_matched(mcp) -> None:
    """It said "no título" about two records whose titles did not contain the
    word, and nothing it was given could have told it otherwise.

    A search reaches the abstract and the notes; `CatalogRecord` carries
    neither -- found by, not quoted from. So the only field the model can see
    is the title, and asked why three records came back for `friendshpi` it
    named the one field it had. The result cannot answer that question, and
    the description now says so where the search is described.
    """
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    description = tools["search_catalog"].description or ""

    assert "never yours to state" in description
    assert "may not contain it at all" in description


@pytest.mark.anyio
async def test_the_model_is_told_when_a_word_was_repaired(mcp) -> None:
    """Records that answer a different query than the one sent are a claim the
    model must not make silently -- and the reader is the only one who can say
    the repair was wrong."""
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    assert "searched_for" in (tools["search_catalog"].description or "")


@pytest.mark.anyio
async def test_the_tool_says_the_terms_are_combined_with_and(mcp) -> None:
    """The description told the model to use the reader's words exactly, which
    was POLICY rule 6 repeated here. Rule 6 is gone -- a reader who asks for
    something to study English is describing a need, not naming a book, and
    there is no sentence of theirs to preserve -- so this had to go with it or
    the tool would contradict the prompt.

    What replaces it is the thing the model could not know and cannot guess:
    `plainto_tsquery` combines terms with AND (`search.py`), so every word
    added narrows. Measured against the Catalyst index: `estudar ingles` and
    `ingles` return nothing, `english` returns dictionaries and grammars and
    `english grammar` five relevant records. An expanding instinct is exactly
    wrong here, and nothing in the schema said so.

    Spelling stays out of the model's hands for the reason it always did: the
    index repairs against the words this library holds, and a model repairs
    against what it read elsewhere.
    """
    async with Client(mcp) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}

    query = tools["search_catalog"].input_schema["properties"]["query"]["description"]

    assert "exactly as they wrote them" not in query
    assert "combined with AND" in query
    assert "Prefer one or two" in query
    assert "often not the language of the question" in query
    assert "repaired by the index" in query
