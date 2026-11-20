"""Records and copies, from captured Evergreen payloads to the domain model."""

from __future__ import annotations

import copy

import pytest
from libvox_driver_koha.errors import Suppressed, UnusableRecord

from libvox_driver_evergreen.fieldmapper import fields_from_idl, to_dict
from libvox_driver_evergreen.marc import parse
from libvox_driver_evergreen.normalize import (
    availability_from_copies,
    copy_state,
    normalize,
    normalize_page,
)

from .conftest import HOLDINGS_AVAILABLE, HOLDINGS_CHECKED_OUT, IDL_BRE, ORG_UNITS, PCRUD_PAGE

FIELDS = fields_from_idl(IDL_BRE, "bre")
ROWS = [to_dict(o, FIELDS, "bre") for o in PCRUD_PAGE["payload"][0]]
NAMES = {int(o["id"]): o["name"] for o in ORG_UNITS}


# --- the IDL -----------------------------------------------------------------


def test_positions_come_from_the_idl() -> None:
    """The MARC is at position 11 in 3.17.3. The test pins that the driver
    *reads* it there, not that it is written down there."""
    assert FIELDS.index("marc") == 11
    assert FIELDS.index("id") == 9
    assert ROWS[0]["id"] == 8


def test_an_unknown_class_is_an_error() -> None:
    from libvox_driver_koha.errors import UpstreamError

    with pytest.raises(UpstreamError):
        fields_from_idl(IDL_BRE, "nope")


# --- records -------------------------------------------------------------------


def test_a_page_keeps_the_live_records_and_counts_the_deleted() -> None:
    records, skipped = normalize_page(ROWS)

    assert [r.biblio_id for r in records] == [8, 9]
    assert skipped == 1  # record 10 is deleted


def test_the_fields_a_reader_searches_by() -> None:
    record = normalize(ROWS[1])

    assert record.title == "Critical entertainments: music old and new"
    assert record.author == "Rosen, Charles"
    assert record.publisher == "Harvard University Press"
    assert record.copyright_date == 2000
    assert record.item_type == "book"
    assert record.ils_timestamp is not None and record.ils_timestamp.year == 2024


def test_a_score_is_not_called_a_book() -> None:
    """Leader 06 'c' is notated music: record 8 is a piano concerto score."""
    assert normalize(ROWS[0]).item_type == "score"


def test_a_deleted_record_is_suppressed() -> None:
    with pytest.raises(Suppressed):
        normalize(ROWS[2])


def test_an_inactive_record_is_suppressed() -> None:
    row = dict(ROWS[1], active="f")
    with pytest.raises(Suppressed):
        normalize(row)


def test_a_record_without_a_title_is_unusable() -> None:
    row = dict(ROWS[1], marc='<record xmlns="http://www.loc.gov/MARC21/slim"/>')
    with pytest.raises(UnusableRecord):
        normalize(row)


def test_a_dtd_is_refused_before_parsing() -> None:
    hostile = (
        '<!DOCTYPE r [<!ENTITY a "aaaa">]>'
        '<record xmlns="http://www.loc.gov/MARC21/slim">&a;</record>'
    )
    with pytest.raises(UnusableRecord):
        parse(hostile)


def test_rda_publisher_wins_over_aacr2() -> None:
    marc = (
        '<record xmlns="http://www.loc.gov/MARC21/slim"><leader>00000nam a2200000 a 4500</leader>'
        '<datafield tag="245" ind1="0" ind2="0"><subfield code="a">T</subfield></datafield>'
        '<datafield tag="264" ind1=" " ind2="1"><subfield code="b">IMPA,</subfield>'
        '<subfield code="c">c2019.</subfield></datafield>'
        '<datafield tag="020" ind1=" " ind2=" "><subfield code="a">9788524404740 (broch.)</subfield></datafield>'
        "</record>"
    )
    record = normalize({"id": 5, "marc": marc, "deleted": "f", "active": "t"})
    assert record.publisher == "IMPA"
    assert record.copyright_date == 2019
    assert record.isbn == "9788524404740"


# --- copies --------------------------------------------------------------------


def test_record_one_as_the_live_server_reported_it() -> None:
    """The smoke test against the running server gave 23 loanable of 31, four
    in a reference room, at West Emnet. The captured payload must agree."""
    availability = availability_from_copies(1, HOLDINGS_AVAILABLE, library_names=NAMES)

    assert availability.status == "available"
    assert availability.copies_total == 31
    assert availability.copies_available == 23
    assert availability.copies_reference_only == 4
    assert availability.library == "West Emnet Public Library"
    assert availability.shelf == "Easy Reader"
    assert availability.call_number == "780 B1"
    assert availability.due_date is None


def test_checked_out_copies_are_not_on_the_shelf() -> None:
    states = [copy_state(c) for c in HOLDINGS_CHECKED_OUT]
    assert states.count("out") == 3


def test_every_copy_out_is_checked_out() -> None:
    out = [c for c in HOLDINGS_CHECKED_OUT if c["status"]["id"] == 1]
    assert availability_from_copies(8, out).status == "checked_out"


def test_a_copy_on_the_holds_shelf_means_on_hold() -> None:
    held = copy.deepcopy(HOLDINGS_CHECKED_OUT[2])
    held["status"].update(id=8, name="On holds shelf", is_available=False)
    out = copy.deepcopy(HOLDINGS_CHECKED_OUT[2])
    assert availability_from_copies(8, [held, out]).status == "on_hold"


def test_a_non_circulating_location_is_reference() -> None:
    item = copy.deepcopy(HOLDINGS_AVAILABLE[0])
    item["location"]["circulate"] = False
    assert copy_state(item) == "reference"
    result = availability_from_copies(1, [item])
    assert result.status == "unavailable"
    assert result.copies_reference_only == 1


def test_a_copy_hidden_from_the_opac_is_not_counted() -> None:
    hidden = copy.deepcopy(HOLDINGS_AVAILABLE[0])
    hidden["location"]["opac_visible"] = False
    deleted = copy.deepcopy(HOLDINGS_AVAILABLE[1])
    deleted["deleted"] = True

    result = availability_from_copies(1, [hidden, deleted])
    assert result.copies_total == 0
    assert result.status == "unavailable"


def test_a_lost_copy_is_gone() -> None:
    lost = copy.deepcopy(HOLDINGS_AVAILABLE[0])
    lost["status"].update(id=3, name="Lost", is_available=False)
    assert copy_state(lost) == "gone"


def test_an_unknown_branch_falls_back_to_its_id() -> None:
    assert availability_from_copies(1, HOLDINGS_AVAILABLE[:1]).library == "4"
