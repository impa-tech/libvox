"""The direct-Koha experiment.

Temporary by design — see `direct_koha.py`. These tests exist so the experiment
does not quietly acquire production behaviour while nobody is looking, and so
the day it is removed the deletion is obvious: this file goes with it.
"""

from __future__ import annotations

import pytest
from libvox_driver_koha import (
    Availability,
    BiblioRecord,
    NotFound,
    PermissionDenied,
    SearchResult,
    Suppressed,
    Unavailable,
)
from mcp import Client

from libvox_mcp import direct_koha
from libvox_mcp.config import Settings
from libvox_mcp.server import MAX_RESULTS

KOHA = {
    "koha_url": "http://koha.test",
    "koha_client_id": "id",
    "koha_client_secret": "secret",
}

MACHADO = BiblioRecord(
    biblio_id=42,
    title="Dom Casmurro",
    author="Machado de Assis",
    copyright_date=1899,
    item_type="BK",
)


class FakeDriver:
    """Stands in for KohaDriver, recording what the MCP layer asked for."""

    def __init__(self, *args, **kwargs) -> None:
        self.calls: list[tuple] = []
        self.result = SearchResult(records=[MACHADO], truncated=False)
        self.record: BiblioRecord | None = MACHADO
        self.raises: Exception | None = None
        self.availability: dict[int, Availability] = {}
        self.closed = False

    def close(self) -> None:
        self.closed = True

    def search_by_title(self, title, *, limit):
        self.calls.append(("search_by_title", title, limit))
        if self.raises:
            raise self.raises
        return self.result

    def get_biblio(self, biblio_id):
        self.calls.append(("get_biblio", biblio_id))
        if self.raises:
            raise self.raises
        return self.record

    def check_availability(self, biblio_id):
        self.calls.append(("check_availability", biblio_id))
        if self.raises:
            raise self.raises
        if biblio_id not in self.availability:
            raise NotFound(f"no biblio {biblio_id}")
        return self.availability[biblio_id]


@pytest.fixture
def driver(monkeypatch: pytest.MonkeyPatch) -> FakeDriver:
    fake = FakeDriver()
    monkeypatch.setattr(direct_koha, "KohaDriver", lambda *a, **k: fake)
    return fake


@pytest.fixture
def mcp(driver: FakeDriver):
    return direct_koha.create_direct_server(Settings(backend="koha", **KOHA))


# --- the tool surface -------------------------------------------------------


async def test_declares_the_tools_it_can_actually_serve(mcp) -> None:
    """`check_availability` is here now that the driver can answer it.

    It used to be absent for a good reason — a tool that always fails teaches
    an agent the wrong lesson — and it is present for the same reason: the Koha
    driver reads `/biblios/{id}/items` and returns real counts.

    `get_biblio` is the one that does not exist in production. It is still
    useful for looking at a single record by hand, but it means this
    experiment's tool surface is one wider than the surface it exists to
    observe, and tool-selection accuracy in small models is sensitive to
    exactly that.
    """
    async with Client(mcp) as client:
        tools = {t.name for t in (await client.list_tools()).tools}

    assert tools == {"search_catalog", "check_availability", "get_biblio"}


async def test_no_tool_accepts_a_tenant(mcp) -> None:
    """Holds even here, where there is no tenant to leak.

    The experiment is single-tenant by construction; if a tenant argument ever
    appeared it would mean someone extended it toward multi-tenant use without
    reading why that is the one thing this design forbids.
    """
    forbidden = {"tenant", "tenant_id", "library", "library_id", "ils_url", "url"}

    async with Client(mcp) as client:
        for tool in (await client.list_tools()).tools:
            properties = set(tool.input_schema.get("properties", {}))
            assert not (properties & forbidden), tool.name


async def test_search_returns_records(mcp, driver: FakeDriver) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("search_catalog", {"query": "Machado"})

    assert driver.calls[-1] == ("search_by_title", "Machado", 5)
    assert result.structured_content["records"][0]["title"] == "Dom Casmurro"


async def test_truncation_reaches_the_model_in_the_result(
    mcp, driver: FakeDriver
) -> None:
    """In the result, not a log notification — the model reads one and not the
    other. Without it, "the library has 3" when there are 30."""
    driver.result = SearchResult(records=[MACHADO], truncated=True)

    async with Client(mcp) as client:
        result = await client.call_tool("search_catalog", {"query": "x", "limit": 1})

    assert result.structured_content["truncated"] is True


async def test_a_complete_result_is_not_marked_truncated(mcp) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("search_catalog", {"query": "x"})

    assert result.structured_content["truncated"] is False


async def test_search_reports_ready(mcp) -> None:
    """There is no index in this mode, but the field's meaning to the model is
    'an empty result is a real no-match', and live results satisfy that."""
    async with Client(mcp) as client:
        result = await client.call_tool("search_catalog", {"query": "x"})

    assert result.structured_content["index_status"] == "ready"


async def test_the_limit_reaches_the_driver(mcp, driver: FakeDriver) -> None:
    async with Client(mcp) as client:
        await client.call_tool("search_catalog", {"query": "x", "limit": 3})

    assert driver.calls[-1][2] == 3


async def test_a_limit_above_the_maximum_is_served_as_the_maximum(
    mcp, driver: FakeDriver
) -> None:
    """Clamped, matching the production tool. The two differ in enough ways
    already; how they treat a page size should not be one of them."""
    async with Client(mcp) as client:
        await client.call_tool("search_catalog", {"query": "x", "limit": 500})

    assert driver.calls[-1][2] == MAX_RESULTS


# --- get_biblio -------------------------------------------------------------


async def test_get_biblio_returns_the_record(mcp) -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("get_biblio", {"biblio_id": 42})

    assert result.structured_content["result"]["title"] == "Dom Casmurro"


@pytest.mark.parametrize("failure", [NotFound("gone"), Suppressed("hidden")])
async def test_an_unavailable_record_is_null_not_an_error(
    mcp, driver: FakeDriver, failure: Exception
) -> None:
    """Both mean 'the patron cannot have this'. Telling them apart in the answer
    would reveal that a hidden record exists — exactly what suppression hides.
    """
    driver.raises = failure

    async with Client(mcp) as client:
        result = await client.call_tool("get_biblio", {"biblio_id": 42})

    assert result.is_error is False
    assert result.structured_content["result"] is None


# --- what the model is allowed to learn about a failure ---------------------


async def test_an_outage_is_a_tool_error(mcp, driver: FakeDriver) -> None:
    driver.raises = Unavailable("connection refused to http://koha.internal:8081")

    async with Client(mcp, raise_exceptions=False) as client:
        result = await client.call_tool("search_catalog", {"query": "x"})

    assert result.is_error is True


async def test_operational_detail_never_reaches_the_conversation(
    mcp, driver: FakeDriver
) -> None:
    """URLs and permission names belong in the operator's log.

    Anything returned here lands in the model's context, where a member of the
    public can ask it to repeat what it just saw.
    """
    driver.raises = PermissionDenied(
        "the Koha API user lacks the permissions this call needs: "
        "{'catalogue': '1'}",
        required_permissions={"catalogue": "1"},
    )

    async with Client(mcp, raise_exceptions=False) as client:
        result = await client.call_tool("search_catalog", {"query": "x"})

    assert result.is_error is True
    text = str(result.content)
    assert "catalogue" not in text
    assert "koha" not in text.lower()


# --- configuration ----------------------------------------------------------


def test_missing_credentials_fail_at_startup_not_on_first_call() -> None:
    """A server that starts and then errors on every call reads as a broken
    integration, and the real cause never reaches anyone."""
    with pytest.raises(SystemExit, match="KOHA_CLIENT_SECRET"):
        Settings(backend="koha", koha_url="http://k", koha_client_id="i").require_koha()


def test_an_unknown_backend_is_refused_rather_than_defaulted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Falling back to 'core' would look like it worked."""
    monkeypatch.setenv("LIBVOX_MCP_BACKEND", "postgres")
    with pytest.raises(ValueError, match="LIBVOX_MCP_BACKEND"):
        Settings.from_env()


def test_the_production_default_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("LIBVOX_MCP_BACKEND", raising=False)
    assert Settings.from_env().backend == "core"


# --- availability -----------------------------------------------------------


def shelf(biblio_id: int, **overrides) -> Availability:
    fields = {
        "biblio_id": biblio_id,
        "status": "available",
        "copies_total": 1,
        "copies_available": 1,
        "library": "Centerville",
    }
    fields.update(overrides)
    return Availability(**fields)


async def test_availability_reaches_the_model_with_both_counts(mcp, driver) -> None:
    """The reason this field exists at all.

    A record with no loanable copy and two reference copies is not "we do not
    have it". If the count stopped at the driver, the model would have no way
    to say so.
    """
    driver.availability = {
        7: shelf(7, status="unavailable", copies_total=2, copies_available=0,
                 copies_reference_only=2)
    }

    async with Client(mcp) as client:
        result = await client.call_tool("check_availability", {"biblio_ids": [7]})

    record = result.structured_content["records"][0]
    assert record["status"] == "unavailable"
    assert record["copies_reference_only"] == 2
    assert record["copies_total"] == 2


async def test_every_requested_id_is_asked_about(mcp, driver) -> None:
    driver.availability = {1: shelf(1), 2: shelf(2)}

    async with Client(mcp) as client:
        result = await client.call_tool("check_availability", {"biblio_ids": [1, 2]})

    assert [c for c in driver.calls] == [
        ("check_availability", 1),
        ("check_availability", 2),
    ]
    assert len(result.structured_content["records"]) == 2


async def test_a_record_the_library_lacks_is_left_out_not_reported_unavailable(
    mcp, driver
) -> None:
    """"Unavailable" would say the library holds it and cannot lend it."""
    driver.availability = {1: shelf(1)}

    async with Client(mcp) as client:
        result = await client.call_tool("check_availability", {"biblio_ids": [1, 999]})

    returned = [r["biblio_id"] for r in result.structured_content["records"]]
    assert returned == [1]


async def test_one_missing_id_does_not_cost_the_patron_the_others(mcp, driver) -> None:
    driver.availability = {5: shelf(5)}

    async with Client(mcp) as client:
        result = await client.call_tool("check_availability", {"biblio_ids": [4, 5, 6]})

    assert len(result.structured_content["records"]) == 1


async def test_a_suppressed_record_is_indistinguishable_from_a_missing_one(
    mcp, driver
) -> None:
    """Telling them apart would leak that a hidden record exists."""
    driver.raises = Suppressed("biblio 3 is hidden from the OPAC")

    async with Client(mcp) as client:
        result = await client.call_tool("check_availability", {"biblio_ids": [3]})

    assert result.structured_content["records"] == []


async def test_an_unreachable_catalog_is_an_error_not_an_empty_answer(
    mcp, driver
) -> None:
    """Empty would read as "the library has none of these", which is a lie."""
    driver.raises = Unavailable("connection refused")

    async with Client(mcp, raise_exceptions=False) as client:
        result = await client.call_tool("check_availability", {"biblio_ids": [1]})

    assert result.is_error is True


async def test_the_permission_error_does_not_reach_the_patron(mcp, driver) -> None:
    """URLs and permission names belong in the operator's log."""
    driver.raises = PermissionDenied(
        "the Koha API user lacks permissions",
        required_permissions={"circulate": "circulate_remaining_permissions"},
    )

    async with Client(mcp, raise_exceptions=False) as client:
        result = await client.call_tool("check_availability", {"biblio_ids": [1]})

    assert result.is_error is True
    assert "circulate" not in str(result.content).lower()


# --- the seam between the driver and this module ---------------------------


def test_the_model_can_carry_everything_the_driver_reports() -> None:
    """Nothing the driver learned may die at this boundary.

    `copies_reference_only` existed in the driver before it existed here, and
    for a while the count of copies a patron could read on the spot stopped at
    the seam and never reached the model. A field added on one side and not the
    other fails exactly that quietly — no error, just an answer that is missing
    something a librarian would have said.
    """
    from dataclasses import fields as dataclass_fields

    from libvox_mcp.models import AvailabilityRecord

    produced = {f.name for f in dataclass_fields(Availability)}
    carried = set(AvailabilityRecord.model_fields)

    assert produced <= carried, f"the driver reports what this model drops: {produced - carried}"


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

    text = direct_koha.INSTRUCTIONS
    mentioned = {w for w in re.findall(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+)+\b", text)}
    mentioned -= not_tools

    assert registered <= mentioned, (
        f"registered but never mentioned: {registered - mentioned}"
    )
    assert mentioned <= registered, (
        f"named in the instructions but not a tool: {mentioned - registered}"
    )
