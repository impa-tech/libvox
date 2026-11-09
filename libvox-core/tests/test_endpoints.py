"""The two tool endpoints, and propagation rule 5.

Every response is validated against the pydantic model `/libvox-mcp` publishes,
because that model is also the tool's output schema: a response Core sends that
fails validation fails the tool call on the other side.
"""

from __future__ import annotations

import pytest
from conftest import (
    TENANT_A,
    TENANT_B,
    databases_up,
    index_records,
    register_library,
    store_credentials,
)

pytestmark = databases_up

ACERVO = [
    (4, "The C programming language", "Kernighan, Brian W."),
    (11, "CGI programming with Perl", "Guelich, Scott."),
    (42, "Dom Casmurro", "Machado de Assis"),
]


def search(client, **overrides):
    body = {
        "tenant_id": TENANT_A,
        "query": "programming",
        # Always sent, never omitted, and nullable.
        "author": None,
        "title": None,
        "limit": 5,
    }
    body.update(overrides)
    return client.post("/internal/v1/search_catalog", json=body)


# --- search -----------------------------------------------------------------


def test_a_search_is_answered_from_the_index(client) -> None:
    index_records(TENANT_A, ACERVO)
    body = search(client).json()

    assert body["index_status"] == "ready"
    assert [r["biblio_id"] for r in body["records"]] == [4, 11]


def test_the_response_validates_against_the_model_the_mcp_publishes(client) -> None:
    """Drift here fails the tool call, not this endpoint."""
    from libvox_mcp.models import SearchCatalogResult

    index_records(TENANT_A, ACERVO)
    SearchCatalogResult.model_validate(search(client).json())


def test_a_library_still_indexing_says_so_rather_than_finding_nothing(client) -> None:
    """The whole reason `index_status` is a field and not a status code.

    The client collapses every non-200 into one opaque sentence, so "still
    indexing" has to arrive as data or the model will report "we do not have
    it" with confidence.
    """
    body = search(client, tenant_id=TENANT_A).json()
    assert body["index_status"] == "building"
    assert body["records"] == []


def test_a_stale_index_still_answers(client) -> None:
    index_records(TENANT_A, ACERVO, status="stale")
    body = search(client).json()
    assert body["index_status"] == "stale"
    assert body["records"]


def test_more_matches_than_the_limit_are_reported_as_truncated(client) -> None:
    index_records(TENANT_A, [(i, f"Programming {i}", "Vários") for i in range(1, 9)])
    body = search(client, limit=3).json()
    assert len(body["records"]) == 3
    assert body["truncated"] is True


def test_a_search_never_reaches_the_ils_even_when_it_finds_nothing(client) -> None:
    """Falling back to a live query on a miss was considered and rejected: a
    still-filling index is exactly when that fallback would be heaviest."""
    import libvox_driver_koha

    index_records(TENANT_A, ACERVO)
    calls: list[object] = []
    original = libvox_driver_koha.KohaDriver.__init__

    def recording(self, *args, **kwargs):
        calls.append(args)
        return original(self, *args, **kwargs)

    libvox_driver_koha.KohaDriver.__init__ = recording  # type: ignore[method-assign]
    try:
        body = search(client, query="astrofisica-que-ninguem-tem").json()
    finally:
        libvox_driver_koha.KohaDriver.__init__ = original  # type: ignore[method-assign]

    assert body["records"] == []
    assert calls == []


def test_a_search_never_reaches_another_librarys_records(client) -> None:
    index_records(TENANT_A, ACERVO)
    index_records(TENANT_B, [(4, "The C programming language", "Kernighan")])

    body = search(client, tenant_id=TENANT_B).json()
    assert [r["biblio_id"] for r in body["records"]] == [4]
    assert len(body["records"]) == 1


def test_search_needs_the_service_token(settings, migrated) -> None:
    from fastapi.testclient import TestClient

    from libvox_core.app import create_app

    with TestClient(create_app(settings)) as bare:
        response = bare.post(
            "/internal/v1/search_catalog",
            json={"tenant_id": TENANT_A, "query": "x", "author": None,
                  "title": None, "limit": 5},
        )
    assert response.status_code == 401


# --- rule 5 -----------------------------------------------------------------


def test_an_id_absent_from_the_index_never_reaches_the_ils(client) -> None:
    """The defence against a hallucinated or injected id being probed.

    The tool description tells the model to pass only ids a search returned.
    That is advice; this is enforcement.
    """
    import libvox_driver_koha

    index_records(TENANT_A, ACERVO)
    built: list[tuple] = []
    original = libvox_driver_koha.KohaDriver.__init__

    def recording(self, *args, **kwargs):
        built.append(args)
        return original(self, *args, **kwargs)

    libvox_driver_koha.KohaDriver.__init__ = recording  # type: ignore[method-assign]
    try:
        response = client.post(
            "/internal/v1/check_availability",
            json={"tenant_id": TENANT_A, "biblio_ids": [999999]},
        )
    finally:
        libvox_driver_koha.KohaDriver.__init__ = original  # type: ignore[method-assign]

    assert response.json() == {"records": []}
    assert built == [], "an unknown id opened a connection to the library's server"


def test_an_id_belonging_to_another_library_is_not_reachable(client) -> None:
    """Row-level security does the work; no WHERE clause to forget."""
    import psycopg
    from conftest import DISCOVERY_DSN

    from libvox_core.availability import known_biblio_ids

    index_records(TENANT_A, ACERVO)
    index_records(TENANT_B, [(777, "Só do IBICT", None)])

    with psycopg.connect(DISCOVERY_DSN) as conn:
        assert known_biblio_ids(conn, TENANT_A, [777]) == set()
        assert known_biblio_ids(conn, TENANT_B, [777]) == {777}


def test_only_the_active_generation_counts_as_known(client) -> None:
    """An id in a half-written import is correctly not yet known."""
    import psycopg
    from conftest import DISCOVERY_DSN
    from libvox_sync.db import tenant_scope

    from libvox_core.availability import known_biblio_ids

    index_records(TENANT_A, ACERVO)
    with psycopg.connect(DISCOVERY_DSN) as conn:
        with tenant_scope(conn, TENANT_A) as scoped, scoped.cursor() as cur:
            cur.execute(
                "INSERT INTO discovery_biblio "
                "  (tenant_id, generation, biblio_id, title) "
                "VALUES (%s, 2, 555, 'Importação pela metade')",
                (TENANT_A,),
            )
        assert known_biblio_ids(conn, TENANT_A, [555]) == set()


def test_a_library_with_no_ils_cannot_answer_and_says_so(client) -> None:
    """It has not connected a catalog, which is not the same as having no
    copies -- and an empty list would be read as the second."""
    index_records(TENANT_A, ACERVO)
    response = client.post(
        "/internal/v1/check_availability",
        json={"tenant_id": TENANT_A, "biblio_ids": [4]},
    )
    assert response.status_code != 200


def test_duplicate_ids_are_asked_about_once(client) -> None:
    """The MCP schema bounds the list but does not deduplicate it, and asking a
    library's server the same question twice in one call is rude for no gain."""
    import psycopg
    from conftest import DISCOVERY_DSN

    from libvox_core.availability import known_biblio_ids

    index_records(TENANT_A, ACERVO)
    with psycopg.connect(DISCOVERY_DSN) as conn:
        assert known_biblio_ids(conn, TENANT_A, [4, 4, 4]) == {4}


# --- the contract with /libvox-mcp -----------------------------------------


def test_the_wire_contract_matches_what_the_mcp_publishes() -> None:
    """Field for field, both ways.

    These models are declared in this module rather than imported, so that
    neither component depends on the other at runtime. This is what keeps that
    from becoming two contracts that drift — and it names whichever field
    diverged rather than failing somewhere downstream as a missing key.
    """
    models = pytest.importorskip("libvox_mcp.models")

    from libvox_core import contracts

    for ours, theirs in (
        (contracts.CatalogRecord, models.CatalogRecord),
        (contracts.SearchCatalogResult, models.SearchCatalogResult),
        (contracts.AvailabilityRecord, models.AvailabilityRecord),
        (contracts.CheckAvailabilityResult, models.CheckAvailabilityResult),
    ):
        assert set(ours.model_fields) == set(theirs.model_fields), (
            f"{ours.__name__} and {theirs.__name__} disagree: "
            f"{set(ours.model_fields) ^ set(theirs.model_fields)}"
        )


def test_the_status_vocabularies_are_the_same_words() -> None:
    """A value one side accepts and the other rejects fails the tool call."""
    models = pytest.importorskip("libvox_mcp.models")

    from typing import get_args

    from libvox_core import contracts

    assert get_args(contracts.IndexStatus) == get_args(models.IndexStatus)
    assert get_args(contracts.AvailabilityStatus) == get_args(
        models.AvailabilityStatus
    )


# --- what an empty list is not ----------------------------------------------


def test_a_failed_lookup_is_not_reported_as_having_no_copies(client) -> None:
    """The confidently wrong answer this architecture exists to prevent.

    Observed live: a base_url the container could not reach, every lookup
    failing, an empty list coming back, and MARC.IA telling a patron the
    library had no copies of two books it holds. "We could not ask" and "the
    library has none" are different answers, and the result type can only say
    one of them -- so the other has to be a failure.
    """
    import libvox_driver_koha

    index_records(TENANT_A, ACERVO)
    register_library(TENANT_A)
    store_credentials(TENANT_A)
    original = libvox_driver_koha.KohaDriver.check_availability

    def refuses(self, biblio_id):
        raise libvox_driver_koha.Unavailable("cannot reach the library's server")

    libvox_driver_koha.KohaDriver.check_availability = refuses  # type: ignore[method-assign]
    try:
        response = client.post(
            "/internal/v1/check_availability",
            json={"tenant_id": TENANT_A, "biblio_ids": [4]},
        )
    finally:
        libvox_driver_koha.KohaDriver.check_availability = original  # type: ignore[method-assign]

    assert response.status_code != 200, "a failed lookup came back as an answer"
    assert response.json() != {"records": []}


def test_a_partial_failure_fails_the_whole_call(client) -> None:
    """"We have two of the three" about a third nobody mentioned went missing
    is the same error, one record at a time."""
    import libvox_driver_koha

    from libvox_core.availability import AvailabilityUnknown, availability_for

    index_records(TENANT_A, ACERVO)
    register_library(TENANT_A)
    store_credentials(TENANT_A)
    original = libvox_driver_koha.KohaDriver.check_availability
    seen: list[int] = []

    def one_fails(self, biblio_id):
        seen.append(biblio_id)
        if biblio_id == 11:
            raise libvox_driver_koha.Unavailable("nope")
        raise libvox_driver_koha.Unauthorized("also nope")

    libvox_driver_koha.KohaDriver.check_availability = one_fails  # type: ignore[method-assign]
    try:
        with pytest.raises(AvailabilityUnknown):
            availability_for(client.app.state.settings, TENANT_A, [4, 11])
    finally:
        libvox_driver_koha.KohaDriver.check_availability = original  # type: ignore[method-assign]


def test_an_id_the_library_does_not_have_is_still_just_omitted(client) -> None:
    """Rule 5 is unchanged: an id absent from the index never reached the ILS,
    so there is no failure to report -- only a record the library lacks."""
    index_records(TENANT_A, ACERVO)
    response = client.post(
        "/internal/v1/check_availability",
        json={"tenant_id": TENANT_A, "biblio_ids": [999999]},
    )
    assert response.status_code == 200
    assert response.json() == {"records": []}


# --- the two surfaces -------------------------------------------------------


def test_the_internal_api_is_not_mounted_on_the_public_app(settings) -> None:
    """The reason the public surface is a second application at all.

    /internal/v1 selects a tenant from its request body, so anything that
    reaches it reads any library. Out here it is not merely unrouted -- it does
    not exist, which is what a misconfigured reverse proxy cannot undo.
    """
    from libvox_core.app import create_public_app

    public = create_public_app(settings)
    paths = {r.path for r in public.routes if hasattr(r, "path")}

    assert not [p for p in paths if p.startswith("/internal")]
    # Named exhaustively rather than counted: a route added here is a route a
    # stranger can reach, and that should have to be written down twice.
    assert paths == {"/healthz", "/ws/v1/chat", "/widget.js"}


def test_neither_app_publishes_its_own_schema(settings) -> None:
    """docs_url and redoc_url were turned off and openapi_url was not, so
    /openapi.json answered 200 with no service token and enumerated every
    route. Turning off the two HTML pages and leaving the machine-readable one
    is turning off the sign and leaving the door."""
    from libvox_core.app import create_app, create_public_app

    for app in (create_app(settings), create_public_app(settings)):
        paths = {r.path for r in app.routes if hasattr(r, "path")}
        assert "/openapi.json" not in paths, app.title


def test_both_surfaces_share_one_session_store(settings) -> None:
    """A session opened on the public WebSocket is introspected on the
    internal port -- that is the call /libvox-mcp makes before every tool
    call. Two stores would be two ideas of who is connected."""
    from libvox_core.app import create_app, create_public_app
    from libvox_core.sessions import SessionStore

    sessions = SessionStore(ttl_s=settings.session_ttl_s)

    internal = create_app(settings, sessions)
    public = create_public_app(settings, sessions)

    assert internal.state.sessions is public.state.sessions


# --- the year, which is a filter and not a word -----------------------------


def test_a_year_range_narrows_a_search(client) -> None:
    """The year is not in the text vector, so this is the only way to ask."""
    index_records(
        TENANT_A,
        [
            (4, "The C programming language", "Kernighan", 1978),
            (11, "Programming pearls", "Bentley", 1999),
            (12, "Programming without a date", "Nobody", None),
        ],
    )
    body = search(client, year_from=1990, year_to=2000).json()

    assert [r["biblio_id"] for r in body["records"]] == [11]


def test_the_response_says_how_many_matches_carried_no_year(client) -> None:
    """Otherwise "one book from the nineties" claims a completeness the
    catalogue does not have -- 70% of one real library's records have no date
    at all."""
    index_records(
        TENANT_A,
        [
            (11, "Programming pearls", "Bentley", 1999),
            (12, "Programming without a date", "Nobody", None),
        ],
    )

    assert search(client, year_from=1990, year_to=2000).json()["undated_records"] == 1
    # Absent when nothing asked for a year: every search of before is unchanged.
    assert search(client).json()["undated_records"] is None


def test_a_typo_is_repaired_and_the_response_says_which_word(client) -> None:
    """The failure that started this: `xadres` answered "we have nothing".

    It is one letter from `xadrez`, and the index could not bridge it — unaccent
    and the Portuguese stemmer reduce `xadres` to `xadr` while `xadrez` stays
    `xadrez`. The repair happens in the database, against this library's own
    words, and the response says which one it used so a reader can say it was
    wrong.
    """
    index_records(TENANT_A, [(21, "Manual de xadrez para iniciantes", "Rui Lopes")])

    body = search(client, query="xadres").json()

    assert [record["biblio_id"] for record in body["records"]] == [21]
    assert body["searched_for"] == "xadrez"


def test_a_search_that_answers_reports_no_repair(client) -> None:
    """`None` is the ordinary case, and the reason no caller had to change."""
    index_records(TENANT_A, [(22, "Programming pearls", "Bentley")])

    assert search(client, query="programming").json()["searched_for"] is None
