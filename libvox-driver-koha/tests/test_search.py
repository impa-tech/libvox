"""Title search: the query it builds, and the wildcards it must neutralize."""

from __future__ import annotations

import json

import pytest

from libvox_driver_koha import (
    MAX_SEARCH_LIMIT,
    BadRequest,
    KohaDriver,
    escape_like,
)

from .conftest import BIBLIO_PAGE, CREDENTIALS, SEARCH_CHESS, FakeKoha


def driver_for(koha: FakeKoha) -> KohaDriver:
    return KohaDriver(
        CREDENTIALS["base_url"],
        client_id=CREDENTIALS["client_id"],
        client_secret=CREDENTIALS["client_secret"],
        min_interval_s=0.0,
        transport=koha.transport,
    )


def sent_query(koha: FakeKoha) -> dict:
    """The `q` Koha actually received, decoded."""
    request = koha.resource_requests()[-1]
    return json.loads(request.url.params["q"])


def matching(rows: list[dict], needle: str) -> list[dict]:
    return [r for r in rows if needle.lower() in (r["title"] or "").lower()]


# --- escaping ---------------------------------------------------------------


def test_a_bare_percent_would_match_the_whole_catalog() -> None:
    """Verified on a live Koha: `%` as the term returned every record.

    This is the reason the function exists, so it is the first thing asserted.
    """
    assert escape_like("%") == "\\%"
    assert escape_like("50% off") == "50\\% off"


def test_underscore_is_a_wildcard_too() -> None:
    """One character wide, and just as wrong. `Windows 95_` must not match
    `Windows 951`."""
    assert escape_like("Windows 95_") == "Windows 95\\_"


def test_the_backslash_is_escaped_first() -> None:
    r"""Otherwise escaping the escape character doubles back over the wildcards
    inserted after it, and `\%` would arrive as a literal-backslash-then-any."""
    assert escape_like("\\") == "\\\\"
    assert escape_like("\\%") == "\\\\\\%"


def test_ordinary_text_is_left_alone() -> None:
    assert escape_like("Introdução à Algoritmos") == "Introdução à Algoritmos"


def test_wildcards_typed_by_a_reader_reach_koha_escaped(koha: FakeKoha) -> None:
    with driver_for(koha.page([])) as driver:
        driver.search_by_title("100%")

    pattern = sent_query(koha)["-or"][0]["title"]["like"]
    assert pattern == "%100\\%%", "the patron's % must not act as a wildcard"


# --- the query ---------------------------------------------------------------


def test_it_searches_title_and_subtitle(koha: FakeKoha) -> None:
    """Koha stores the subtitle apart from the title, and someone typing the
    full name of a book is typing both."""
    with driver_for(koha.page([])) as driver:
        driver.search_by_title("chess")

    query = sent_query(koha)
    assert query == {
        "-or": [
            {"title": {"like": "%chess%"}},
            {"subtitle": {"like": "%chess%"}},
        ]
    }


def test_results_are_normalized(koha: FakeKoha) -> None:
    with driver_for(koha.page(SEARCH_CHESS)) as driver:
        result = driver.search_by_title("chess")

    assert [r.title for r in result.records] == [
        "The ideas behind the chess openings",  # ISBD slash stripped
        "The Duchess of Malfi",
        "Pawn structure chess",
    ]
    assert result.truncated is False


def test_no_match_is_an_empty_result_not_an_error(koha: FakeKoha) -> None:
    with driver_for(koha.page([])) as driver:
        result = driver.search_by_title("zzzznadaaqui")

    assert result.records == []
    assert result.truncated is False


# --- limits ------------------------------------------------------------------


def test_a_full_page_is_reported_as_truncated(koha: FakeKoha) -> None:
    """'3 results' and 'the first 2 of unknown many' are different answers."""
    with driver_for(koha.page(BIBLIO_PAGE)) as driver:
        result = driver.search_by_title("a", limit=2)

    assert len(result.records) == 2
    assert result.truncated is True


def test_truncation_counts_what_koha_sent_not_what_survived(koha: FakeKoha) -> None:
    """A page filled entirely with suppressed records still has more behind it."""
    hidden = [dict(row, opac_suppressed=True) for row in BIBLIO_PAGE[:2]]
    with driver_for(koha.page(hidden)) as driver:
        result = driver.search_by_title("a", limit=2)

    assert result.records == []
    assert result.truncated is True, "an empty result must not imply the end"


def test_an_empty_term_is_refused_before_dialling(koha: FakeKoha) -> None:
    """'' becomes '%%', which matches the entire catalog."""
    with driver_for(koha.page([])) as driver:
        for term in ("", "   ", None):
            with pytest.raises(BadRequest):
                driver.search_by_title(term)  # type: ignore[arg-type]

    assert koha.resource_requests() == []


def test_an_out_of_range_limit_is_refused_not_clamped(koha: FakeKoha) -> None:
    """Silently returning 100 of 1000 reads as 'that is all of them'."""
    with driver_for(koha.page([])) as driver:
        for bad in (0, -1, MAX_SEARCH_LIMIT + 1):
            with pytest.raises(BadRequest):
                driver.search_by_title("chess", limit=bad)


def test_the_limit_becomes_per_page(koha: FakeKoha) -> None:
    with driver_for(koha.page(BIBLIO_PAGE)) as driver:
        driver.search_by_title("a", limit=5)

    params = koha.resource_requests()[-1].url.params
    assert params["_per_page"] == "5"
    assert params["_page"] == "1"


# --- what this method is not -------------------------------------------------


def test_substring_matching_is_why_this_is_not_reader_search(koha: FakeKoha) -> None:
    """Documented in a test because it is the honest limitation.

    `LIKE '%chess%'` matches inside words — on the real sample catalog it
    returns *The Duchess of Malfi*. The Discovery Index stems and weights;
    this is a probe against the library's own server, and deliberately weak.
    """
    with driver_for(koha.page(SEARCH_CHESS)) as driver:
        titles = [r.title for r in driver.search_by_title("chess").records]

    assert "The Duchess of Malfi" in titles, (
        "a patron searching for chess should not be shown a Jacobean tragedy"
    )
