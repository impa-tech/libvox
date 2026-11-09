"""Normalization against real Koha payloads, with no network.

`biblio_page.json` and `biblio_single.json` were both captured from a running
Koha, so these exercise the actual MARC-derived mess rather than an idealized
shape.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from libvox_driver_koha import (
    BiblioRecord,
    Suppressed,
    UnusableRecord,
    normalize,
    normalize_page,
)

HERE = Path(__file__).parent
PAGE = json.loads((HERE / "biblio_page.json").read_text(encoding="utf-8"))
SINGLE = json.loads((HERE / "biblio_single.json").read_text(encoding="utf-8"))


def raw_for(biblio_id: int) -> dict:
    return next(r for r in PAGE if r["biblio_id"] == biblio_id)


# --- the two endpoints agree ------------------------------------------------


def test_the_single_record_endpoint_returns_the_same_shape() -> None:
    """The reason one normalization can serve both execution paths.

    If `GET /biblios/{id}` and `GET /biblios` ever diverged, the Discovery Index
    and the live lookup would describe the same book differently.
    """
    from_list = raw_for(1)
    assert set(SINGLE) == set(from_list)
    assert normalize(SINGLE) == normalize(from_list)


# --- normalization ----------------------------------------------------------


def test_every_fixture_record_normalizes() -> None:
    records, skipped = normalize_page(PAGE)
    assert len(records) == len(PAGE)
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

    assert raw_for(3)["isbn"] == (
        "0520078438 (alk. paper) | 0520078446 (pbk. : alk. paper)"
    )
    assert normalize(raw_for(3)).isbn == "0520078438"


def test_parses_the_ils_timestamp() -> None:
    """The field incremental sync depends on. Present in every fixture record."""
    assert normalize(raw_for(1)).ils_timestamp == datetime(
        2020, 1, 29, 12, 6, 37, tzinfo=timezone.utc
    )


def test_falls_back_to_publication_year() -> None:
    """Which of the two year fields is filled varies by installation."""
    raw = dict(raw_for(1), copyright_date=None, publication_year="1975")
    assert normalize(raw).copyright_date == 1975


def test_missing_year_is_none_not_zero() -> None:
    raw = dict(raw_for(1), copyright_date=None, publication_year=None)
    assert normalize(raw).copyright_date is None


# --- records that must not be served ----------------------------------------


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
    rows = [
        raw_for(1),
        dict(raw_for(2), opac_suppressed=True),
        dict(raw_for(3), title=None),
    ]
    records, skipped = normalize_page(rows)
    assert len(records) == 1
    assert skipped == 2


# --- contract with the index ------------------------------------------------


def test_record_fields_match_the_index_columns() -> None:
    """Drift here means the pipeline writes a column the schema lacks."""
    from dataclasses import fields

    produced = {f.name for f in fields(BiblioRecord)}
    assert produced == {
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


def test_carries_the_text_the_catalog_already_has() -> None:
    """Koha returns these in the same object as the title; dropping them made
    a record whose series is the word people type unfindable."""
    record = normalize(
        dict(
            raw_for(16),
            title="Metapod",
            author=None,
            series_title="Pokemon - Generation I",
            abstract="Metapod resembles a chrysalis.",
            notes="Based on butterfly chrysalises.",
        )
    )
    assert record.series == "Pokemon - Generation I"
    assert record.abstract == "Metapod resembles a chrysalis."
    assert record.notes == "Based on butterfly chrysalises."


def test_falls_back_to_the_collection_when_there_is_no_series() -> None:
    """Koha spells the same idea two ways depending on how it was cataloged."""
    record = normalize(dict(raw_for(1), series_title=None, collection_title="Vaga-Lume"))
    assert record.series == "Vaga-Lume"


def test_as_dict_is_json_ready() -> None:
    """The invocation contract serializes this; a datetime would not survive."""
    payload = normalize(raw_for(1)).as_dict()
    assert json.loads(json.dumps(payload)) == payload
    assert payload["ils_timestamp"] == "2020-01-29T12:06:37+00:00"


def test_strips_quotation_marks_that_wrap_a_whole_title() -> None:
    """MARC 245 $a keeps them when the title is itself a quotation.

    `"Páipéir bhána agus páipéir bhreaca."` is what Koha's own biblio row
    holds for record 192 of the sample catalogue, and it reached the Discovery
    Index and a reader's screen exactly like that -- the library appearing to
    quote itself. Unlike the trailing period, a wrapping pair cannot be
    anything else.
    """
    record = normalize(dict(raw_for(1), title='"But she\'s still my grandma!"'))
    assert record.title == "But she's still my grandma!"

    typographic = normalize(dict(raw_for(1), title="“Sort of forever”"))
    assert typographic.title == "Sort of forever"


def test_unquotes_the_title_before_the_subtitle_is_joined_on() -> None:
    """The pair wraps the main title, never the sentence a reader ends up with.

    Record 253 of the sample catalogue is `"Is this English?" :` plus a
    subtitle, so a rule looking at the finished string would find no wrapper
    and leave both marks in the middle of the answer.
    """
    record = normalize(
        dict(
            raw_for(1),
            title='"Is this English?" :',
            subtitle="race, language, and culture in the classroom",
        )
    )
    assert record.title == (
        "Is this English?: race, language, and culture in the classroom"
    )


def test_every_field_is_unquoted_not_only_the_title() -> None:
    """The convention is not about titles.

    Twenty notes across the two test collections are a quoted phrase and
    nothing else -- `"A Neal Porter book."`, `"Study guide included."` -- which
    is MARC 500 written the way MARC 245 is. An author or a series recorded
    that way is the same artifact, and a rule that knew about titles alone
    would leave each of them for a later bug report.
    """
    record = normalize(
        dict(
            raw_for(1),
            author='"Prasad, Birjadish"',
            publisher='"Henry Holt"',
            series_title="“Vaga-Lume”",
            item_type='"SER"',
            abstract='"Metapod resembles a chrysalis."',
            notes='"A Neal Porter book."',
        )
    )

    assert record.author == "Prasad, Birjadish"
    assert record.publisher == "Henry Holt"
    assert record.series == "Vaga-Lume"
    assert record.item_type == "SER"
    assert record.abstract == "Metapod resembles a chrysalis."
    assert record.notes == "A Neal Porter book."


def test_isbd_punctuation_the_quotes_were_hiding_is_trimmed_too() -> None:
    """`clean_text` trims the end before it looks for a wrapper, so a value that
    carried both had the second one out of reach until the marks came off."""
    assert normalize(dict(raw_for(1), title='"E Street shuffle :"')).title == (
        "E Street shuffle"
    )


def test_the_fields_that_are_not_free_text_are_unquoted_too() -> None:
    """A quoted number does not parse, and a field that does not parse is gone.

    Free text keeps its marks and looks wrong; an ISBN, a year or a timestamp
    keeps them and becomes `None`, which no one sees. The louder half of this
    was the reason to look at the quiet half.
    """
    record = normalize(
        dict(
            raw_for(1),
            isbn='"0131103709 | 0131103628 (pbk.)"',
            copyright_date='"1986"',
            timestamp='"2020-01-29T12:06:37"',
        )
    )

    assert record.isbn == "0131103709"
    assert record.copyright_date == 1986
    assert record.ils_timestamp == datetime(2020, 1, 29, 12, 6, 37, tzinfo=timezone.utc)


def test_csv_escaping_that_leaked_into_the_catalog_is_undone() -> None:
    """`"Macmillan""s Political Science"` is one record of the Catalyst
    collection: the field quoted whole with its interior mark doubled, which is
    a spreadsheet's convention and not a title quoting anything.

    Unescaped, never guessed at. The mark that comes out is the one that went
    in -- reading `""` as the apostrophe it was probably meant to be would be
    renaming a book on a hunch.
    """
    record = normalize(dict(raw_for(1), title='"Macmillan""s Political Science"'))
    assert record.title == 'Macmillan"s Political Science'

    # Record 7815 of the same collection, which is what makes the rule worth
    # trusting: a title that really does quote another work comes back quoting
    # it, because unescaping a doubled mark and stripping a wrapper are not the
    # same operation.
    nested = normalize(
        dict(raw_for(1), title='"Orwellian Vision and Prophecy in ""Literature and Politics"""')
    )
    assert nested.title == 'Orwellian Vision and Prophecy in "Literature and Politics"'

    # And three publishers of that collection are the same shape.
    assert normalize(dict(raw_for(1), publisher='"Loyar""s Book Stol"')).publisher == (
        'Loyar"s Book Stol'
    )


def test_a_quote_inside_a_title_is_left_where_the_catalog_put_it() -> None:
    """Only a pair wrapping the whole value is punctuation around the name.

    An unbalanced mark is a catalogue as it was recorded: `J. Elferink.
    ["s-Gravenhage` is a real publisher in the sample collection, and a rule
    that trimmed marks anywhere would quietly edit it.
    """
    for untouched in (
        'The making of "Citizen Kane"',
        '"Citizen Kane" and "Vertigo"',
        'J. Elferink. ["s-Gravenhage',
        '"',
        '""',
    ):
        assert normalize(dict(raw_for(1), title=untouched)).title == untouched
