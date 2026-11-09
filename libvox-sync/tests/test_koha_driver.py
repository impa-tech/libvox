"""Milestone 2: extraction and normalization, with no network.

`koha_example.json` is a real response captured from a running Koha, so these
tests exercise the actual MARC-derived mess rather than an idealized shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from libvox_driver_koha import (
    Page,
    Suppressed,
    UnusableRecord,
    iter_records,
    normalize,
    normalize_page,
)

from libvox_sync.drivers.fixture import FixtureDriver

FIXTURE = Path(__file__).parent / "koha_example.json"
RAW = json.loads(FIXTURE.read_text(encoding="utf-8"))


def raw_for(biblio_id: int) -> dict:
    return next(r for r in RAW if r["biblio_id"] == biblio_id)


# --- normalization of real Koha payloads ------------------------------------


def test_every_fixture_record_normalizes() -> None:
    records, skipped = normalize_page(RAW)
    assert len(records) == len(RAW)
    assert skipped == 0


def test_strips_isbd_punctuation_from_title() -> None:
    """MARC ends a title with ' :' or ' /'. Neither belongs in a chat answer."""
    assert raw_for(1)["title"] == "E Street shuffle :"
    assert normalize(raw_for(1)).title == "E Street shuffle"

    assert raw_for(2)["title"] == "The ideas behind the chess openings /"
    assert normalize(raw_for(2)).title == "The ideas behind the chess openings"


def test_keeps_a_trailing_period_in_a_name() -> None:
    """Deliberate: a trailing period is indistinguishable from an initial.

    "Mastronarde, Donald J." would become "Donald J" under a rule that strips
    it — mangling a name is worse than leaving a stray dot.
    """
    assert normalize(raw_for(3)).author == "Mastronarde, Donald J."


def test_strips_a_trailing_comma_from_an_author() -> None:
    assert raw_for(2)["author"] == "Fine, Reuben,"
    assert normalize(raw_for(2)).author == "Fine, Reuben"


def test_takes_the_first_isbn_and_drops_qualifiers() -> None:
    """Koha packs several ISBNs into one annotated, pipe-separated string."""
    assert raw_for(4)["isbn"] == "0131103709 | 0131103628 (pbk.)"
    assert normalize(raw_for(4)).isbn == "0131103709"

    assert raw_for(3)["isbn"] == "0520078438 (alk. paper) | 0520078446 (pbk. : alk. paper)"
    assert normalize(raw_for(3)).isbn == "0520078438"


def test_parses_the_ils_timestamp() -> None:
    """The field incremental sync depends on. Present in every fixture record."""
    record = normalize(raw_for(1))
    assert record.ils_timestamp == datetime(
        2020, 1, 29, 12, 6, 37, tzinfo=timezone.utc
    )


def test_falls_back_to_publication_year() -> None:
    """Which of the two year fields is filled varies by installation."""
    raw = dict(raw_for(1), copyright_date=None, publication_year="1975")
    assert normalize(raw).copyright_date == 1975


def test_missing_year_is_none_not_zero() -> None:
    raw = dict(raw_for(1), copyright_date=None, publication_year=None)
    assert normalize(raw).copyright_date is None


# --- records that must not reach the index ----------------------------------


def test_suppressed_records_are_refused() -> None:
    """A record the library hides from its own OPAC must not surface in chat."""
    with pytest.raises(Suppressed):
        normalize(dict(raw_for(1), opac_suppressed=True))


def test_untitled_records_are_refused() -> None:
    """Nothing to match on, and the index column is NOT NULL."""
    with pytest.raises(UnusableRecord):
        normalize(dict(raw_for(1), title=None))
    with pytest.raises(UnusableRecord):
        normalize(dict(raw_for(1), title="  "))


def test_skips_are_counted_not_swallowed() -> None:
    """A silent shortfall in the index is the hard kind of bug to notice."""
    rows = [raw_for(1), dict(raw_for(2), opac_suppressed=True), dict(raw_for(3), title=None)]
    records, skipped = normalize_page(rows)
    assert len(records) == 1
    assert skipped == 2


# --- pagination -------------------------------------------------------------


def test_paginates_to_completion() -> None:
    driver = FixtureDriver(FIXTURE, page_size=7)
    records = list(iter_records(driver))
    assert len(records) == len(RAW)
    assert [r.biblio_id for r in records] == [r["biblio_id"] for r in RAW]


def test_exactly_full_final_page_terminates() -> None:
    """Koha sends no total, so a full last page costs one empty follow-up.

    The driver must survive that instead of looping or raising.
    """
    driver = FixtureDriver(FIXTURE, page_size=len(RAW))

    first = driver.fetch_page()
    assert len(first.records) == len(RAW)
    assert first.next_cursor is not None, "a full page cannot be assumed to be the last"

    second = driver.fetch_page(first.next_cursor)
    assert second.records == []
    assert second.next_cursor is None


def test_short_page_ends_pagination_without_an_extra_request() -> None:
    driver = FixtureDriver(FIXTURE, page_size=len(RAW) + 1)
    page = driver.fetch_page()
    assert len(page.records) == len(RAW)
    assert page.next_cursor is None


def test_empty_page_is_a_clean_stop() -> None:
    assert Page().records == []
    assert Page().next_cursor is None


# --- contract with the index ------------------------------------------------


def test_record_fields_match_the_index_columns() -> None:
    """Drift here means the pipeline writes a column the schema lacks.

    No `skipif` guarding the import, unlike the `libvox_mcp` comparison in
    `test_schema.py`: that sibling is optional and this one is not. Without a
    driver there is nothing to extract, so an ImportError here is a broken
    install and should say so rather than quietly skipping.
    """
    from dataclasses import fields

    from libvox_driver_koha import BiblioRecord

    produced = {f.name for f in fields(BiblioRecord)}
    index_columns = {
        "biblio_id",
        "title",
        "author",
        "isbn",
        "publisher",
        "copyright_date",
        "item_type",
        "series",
        "abstract",
        "notes",
        "ils_timestamp",
    }
    assert produced == index_columns
