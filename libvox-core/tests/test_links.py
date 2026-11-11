"""Linking a bold title to its OPAC page, without a model or a database."""

from __future__ import annotations

from libvox_core.agent.tools import ToolCall
from libvox_core.links import MAX_SEEN, builder_for, record_links, remember

OPAC = "https://koha-opac-demo.ibict.br"
build = builder_for("koha", OPAC)


def href(biblio_id: int) -> str:
    return f"{OPAC}/cgi-bin/koha/opac-detail.pl?biblionumber={biblio_id}"


def test_the_ibict_example() -> None:
    links = record_links(
        "Temos **Dom Casmurro**, de Machado de Assis.", {64: "Dom Casmurro"}, build
    )
    assert links == [{"text": "Dom Casmurro", "href": href(64)}]


def test_koha_punctuation_and_a_dropped_subtitle_still_match() -> None:
    seen = {64: "Dom Casmurro : romance /", 7: "Quincas Borba /"}
    links = record_links("1. **Dom Casmurro.**\n2. **Quincas Borba**", seen, build)
    assert links == [
        {"text": "Dom Casmurro.", "href": href(64)},
        {"text": "Quincas Borba", "href": href(7)},
    ]


def test_a_title_that_would_fit_two_books_links_neither() -> None:
    seen = {1: "Newton : a biography", 2: "Newton : the man"}
    assert record_links("**Newton**", seen, build) == []


def test_an_exact_title_wins_over_a_longer_one() -> None:
    seen = {1: "Newton", 2: "Newton polyhedra"}
    assert record_links("**Newton**", seen, build) == [
        {"text": "Newton", "href": href(1)}
    ]


def test_bold_that_is_not_a_book_links_nothing() -> None:
    seen = {64: "Dom Casmurro"}
    text = "**Atenção:** o **IA** não sabe; mas **Dom Casmurro** está aqui."
    assert record_links(text, seen, build) == [
        {"text": "Dom Casmurro", "href": href(64)}
    ]


def test_a_book_named_twice_is_linked_once() -> None:
    links = record_links("**Dom Casmurro** e de novo **Dom Casmurro**", {64: "Dom Casmurro"}, build)
    assert len(links) == 1


def test_triple_asterisks_are_bold_too() -> None:
    assert record_links("***Dom Casmurro***", {64: "Dom Casmurro"}, build) == [
        {"text": "Dom Casmurro", "href": href(64)}
    ]


def test_no_address_no_driver_no_unsafe_scheme_means_no_builder() -> None:
    assert builder_for("koha", None) is None
    assert builder_for("koha", "") is None
    assert builder_for("folio", OPAC) is None
    assert builder_for("koha", "javascript:alert(1)//") is None


def _search(records: list[dict]) -> ToolCall:
    return ToolCall("search_catalog", {}, result={"records": records})


def test_only_successful_searches_are_remembered() -> None:
    seen: dict[int, str] = {}
    remember(
        seen,
        [
            _search([{"biblio_id": 64, "title": "Dom Casmurro"}]),
            ToolCall("search_catalog", {}, error="boom"),
            ToolCall("check_availability", {}, result={"availability": [{"biblio_id": 9}]}),
            _search([{"biblio_id": "65", "title": "string id"}, {"biblio_id": 66}]),
        ],
    )
    assert seen == {64: "Dom Casmurro"}


def test_what_a_socket_remembers_is_bounded() -> None:
    seen: dict[int, str] = {}
    remember(seen, [_search([{"biblio_id": n, "title": f"t{n}"} for n in range(MAX_SEEN + 10)])])
    assert len(seen) == MAX_SEEN
    assert 0 not in seen and MAX_SEEN + 9 in seen
