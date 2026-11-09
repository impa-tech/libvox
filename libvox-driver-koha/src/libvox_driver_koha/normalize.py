"""Koha's payload shape translated into the LibVox domain model.

Free functions, separate from the HTTP client, so they can be tested against
real Koha payloads with no network — see `tests/biblio_page.json` and
`tests/biblio_single.json`, both captured from a live instance.

Verified on a running Koha (Koha Testing Docker, August 2026): `GET /biblios`
and `GET /biblios/{id}` return the *same* flat object shape, so one
normalization serves both the live lookup and the bulk import. If those two
ever diverged, the Discovery Index and the live answer would disagree about
the same book — which is the bug this shared module exists to prevent.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from .contract import Availability, AvailabilityStatus, BiblioRecord
from .errors import Suppressed, UnusableRecord

logger = logging.getLogger(__name__)

# ISBD punctuation that ends a MARC subfield: " :" before a subtitle, " /"
# before a statement of responsibility, and so on. A trailing period is
# deliberately *not* stripped — it is indistinguishable from the period in an
# initial ("Mastronarde, Donald J."), and mangling a name is worse than
# leaving a stray dot.
_ISBD_TRAILING = re.compile(r"[\s:;,/=]+$")

# "0520078438 (alk. paper)" -> "0520078438"
_ISBN_QUALIFIER = re.compile(r"\s*\(.*?\)\s*")

# A MARC subfield catalogued as a quotation keeps the quotation marks.
# `"Páipéir bhána agus páipéir bhreaca."` is what 245 $a holds and what Koha
# hands over untouched, and a reader shown it back sees the library quoting
# itself; the 500 notes do the same thing -- `"A Neal Porter book."`, twenty
# of them across the two test collections. They are cataloguing punctuation
# wrapped around the value, not part of it, unlike the trailing period above,
# which cannot be told from an initial.
#
# Every field a Koha record carries goes through this, including the ones that
# are not free text: an ISBN, a year or a timestamp arriving quoted parses as
# nothing at all, which is a field silently lost rather than a stray mark.
#
# Double quotes only, straight and typographic. An apostrophe opens a title
# often enough -- `'Salem's Lot` -- that treating it as a wrapper would rename
# books.
_QUOTE_PAIRS = (('"', '"'), ("\u201c", "\u201d"))


def _unquote(value: str) -> str:
    """Drop one pair of quotation marks wrapping a whole value.

    Only when a single pair wraps the *whole* of it. A quote inside means the
    marks are doing something within the value, and an unbalanced one is a
    catalogue as it was recorded -- `J. Elferink. ["s-Gravenhage` is a real
    publisher in a real collection, and it comes out the way it went in.
    """
    for opening, closing in _QUOTE_PAIRS:
        if not (
            len(value) > 2 and value.startswith(opening) and value.endswith(closing)
        ):
            continue

        inner = value[1:-1]
        if opening not in inner and closing not in inner:
            return inner.strip() or value

        # CSV escaping that leaked into the catalogue at load time: the field
        # quoted whole and every interior mark doubled. `"Macmillan""s
        # Political Science"` is one record of the Catalyst collection, and it
        # is not a title quoting anything -- it is a spreadsheet's convention
        # read back as data. Unescaped rather than guessed at: the mark that
        # comes out is the one that went in, apostrophe or not.
        #
        # The test is the CSV rule itself, which is why it cannot fire on
        # `"Citizen Kane" and "Vertigo"`: remove every doubled mark and a
        # single one must not remain.
        if opening == closing and opening not in inner.replace(opening * 2, ""):
            return inner.replace(opening * 2, opening).strip() or value
    return value


def clean_text(value: Any) -> str | None:
    """Trim ISBD punctuation and wrapping quotation marks, blanks to None.

    Every field goes through here, which is the point: the marks are a
    cataloguing convention and the convention is not about titles. An author
    recorded as a quotation is the same artifact as a note recorded as one.
    """
    if not isinstance(value, str):
        return None
    cleaned = _ISBD_TRAILING.sub("", value.strip())
    unquoted = _unquote(cleaned)
    if unquoted != cleaned:
        # The marks sat outside the punctuation: `"A title :"` has ISBD to trim
        # that was unreachable while it was quoted.
        unquoted = _ISBD_TRAILING.sub("", unquoted)
    return unquoted or None


def _first_isbn(value: Any) -> str | None:
    """Koha packs multiple ISBNs into one string, pipe-separated and annotated.

    `"0131103709 | 0131103628 (pbk.)"` -> `"0131103709"`. Only the first is
    kept: the domain model holds one ISBN, and the first is the one Koha
    presents as primary.
    """
    if not isinstance(value, str) or not value.strip():
        return None
    # Unquoted before the split, not after: the pair wraps the field, and the
    # field is the whole pipe-separated list.
    first = _unquote(value.strip()).split("|")[0]
    return _unquote(_ISBN_QUALIFIER.sub("", first).strip()) or None


def _year(record: dict[str, Any]) -> int | None:
    """Publication year, from whichever field the cataloguer filled in.

    In the sample catalog `copyright_date` is populated and `publication_year`
    is null throughout, but the reverse happens in other installations.
    """
    for key in ("copyright_date", "publication_year"):
        value = record.get(key)
        if isinstance(value, int):
            return value
        if isinstance(value, str):
            digits = _unquote(value.strip())
            if digits.isdigit():
                return int(digits)
    return None


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(_unquote(value.strip()))
    except ValueError:
        logger.debug("unparseable Koha timestamp: %r", value)
        return None
    # Naive timestamps from an ILS are local to that server; treating them as
    # UTC keeps comparisons total rather than raising mid-import.
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def normalize(raw: dict[str, Any]) -> BiblioRecord:
    """Translate one Koha biblio into the LibVox domain model.

    Raises rather than returning something half-formed: a record the library
    hides from its own OPAC must not surface in a conversational answer, and a
    record with no title has nothing to match on and would violate the index's
    NOT NULL besides.
    """
    if raw.get("opac_suppressed"):
        raise Suppressed(f"biblio {raw.get('biblio_id')} is hidden from the OPAC")

    biblio_id = raw.get("biblio_id")
    if not isinstance(biblio_id, int):
        raise UnusableRecord(f"missing or non-integer biblio_id: {biblio_id!r}")

    # `clean_text` unquotes each field on its own, which is what the joined title
    # needs: `"Is this English?" :` + `race, language, and culture in the
    # classroom` is one pair around the first half of what a reader ends up
    # seeing, and nothing wraps the whole of it.
    title = clean_text(raw.get("title"))
    if not title:
        raise UnusableRecord(f"biblio {biblio_id} has no title")

    # Koha keeps the subtitle in its own field; the ISBD colon that joined them
    # lives on the end of the title, which _clean has just removed.
    subtitle = clean_text(raw.get("subtitle"))
    if subtitle:
        title = f"{title}: {subtitle}"

    return BiblioRecord(
        biblio_id=biblio_id,
        title=title,
        author=clean_text(raw.get("author")),
        isbn=_first_isbn(raw.get("isbn")),
        publisher=clean_text(raw.get("publisher")),
        copyright_date=_year(raw),
        item_type=clean_text(raw.get("item_type")),
        # Koha's flat biblio object already carries these; they arrive in the
        # same request the title does and were simply being dropped. A catalog
        # whose titles are creature names and whose series is what people type
        # is unsearchable without the first one.
        series=(
            clean_text(raw.get("series_title"))
            or clean_text(raw.get("collection_title"))
        ),
        abstract=clean_text(raw.get("abstract")),
        notes=clean_text(raw.get("notes")),
        ils_timestamp=_timestamp(raw.get("timestamp")),
    )


def normalize_page(rows: list[dict[str, Any]]) -> tuple[list[BiblioRecord], int]:
    """Normalize a page, dropping what cannot or must not be indexed.

    Bulk extraction tolerates what a single lookup must not: one bad row in a
    catalog of half a million is a log line, whereas the same row asked for by
    id is the whole answer and has to raise.
    """
    records: list[BiblioRecord] = []
    skipped = 0
    for raw in rows:
        try:
            records.append(normalize(raw))
        except Suppressed as exc:
            logger.debug("skipping: %s", exc)
            skipped += 1
        except UnusableRecord as exc:
            logger.warning("skipping malformed record: %s", exc)
            skipped += 1
    return records, skipped


# --- availability ----------------------------------------------------------
#
# Deriving "on the shelf" from Koha's item flags is a cataloguing judgement,
# not plumbing, so the rules are here in one place with their reasoning rather
# than spread through the driver.

# Koha reports these as 0 / null when nothing is wrong, and as a code otherwise.
# The codes differ per library; only "is it set" is portable.
_BLOCKING_FLAGS = ("lost_status", "withdrawn", "damaged_status", "restricted_status")


def _is_set(value: Any) -> bool:
    """Whether a Koha status flag is raised.

    Koha writes 0, null, "0" and false for "fine" depending on the field and
    the version. Anything else is a code meaning something is wrong with the
    copy, and the codes are library-defined — so this asks only whether one is
    present, never what it says.
    """
    if value is None or value is False:
        return False
    if isinstance(value, str):
        return value.strip() not in ("", "0")
    return bool(value)


def item_state(item: dict[str, Any]) -> str:
    """Classify one physical copy: shelf, reference, out, or gone.

    Order matters. A copy that is lost *and* checked out is gone, not out —
    reporting it as "due back" would promise a return that is not coming.
    """
    if any(_is_set(item.get(flag)) for flag in _BLOCKING_FLAGS):
        return "gone"
    if item.get("checked_out_date"):
        return "out"

    # `effective_` resolves the item's own flag against its item type, which is
    # where most libraries actually set "reference only". The raw field alone
    # would miss a whole reference collection configured at the type level.
    if _is_set(item.get("effective_not_for_loan_status")) or _is_set(
        item.get("not_for_loan_status")
    ):
        return "reference"
    return "shelf"


def availability_from_items(
    biblio_id: int,
    items: list[dict[str, Any]],
    *,
    library_names: dict[str, str] | None = None,
    shelf_names: dict[str, str] | None = None,
) -> Availability:
    """Summarize every copy of one record into the answer a patron asked for.

    `status` describes **borrowing**, because that is what "do you have it?"
    almost always means. The counts carry what a four-value vocabulary cannot:
    a record with no loanable copy and two reference copies is `unavailable`
    with `copies_reference_only = 2`, and a caller that reads only the status
    will tell someone to stay home from a library that has the book on a table.
    """
    names = library_names or {}
    shelves = shelf_names or {}
    states = [item_state(item) for item in items]

    on_shelf = states.count("shelf")
    reference = states.count("reference")
    checked_out = states.count("out")

    if on_shelf:
        status: AvailabilityStatus = "available"
    elif checked_out and any(
        (item.get("holds_count") or 0) > 0
        for item, state in zip(items, states)
        if state == "out"
    ):
        # Someone is already in the queue, so "checked out" would understate
        # the wait by however long that queue is.
        status = "on_hold"
    elif checked_out:
        status = "checked_out"
    else:
        status = "unavailable"

    # Which branch to go to, taken from the first copy a patron could actually
    # use. A lost copy's branch is not an address worth giving anyone.
    usable = [
        item
        for item, state in zip(items, states)
        if state in ("shelf", "reference")
    ] or list(items)
    library = shelf = call_number = None
    if usable:
        first = usable[0]
        code = first.get("holding_library_id") or first.get("home_library_id")
        library = names.get(code, code) if code else None
        call_number = clean_text(first.get("callnumber"))

        # `location` is where the copy sits today; `permanent_location` is where
        # it belongs. A book on the display shelf or the book cart has the two
        # disagreeing, and the one worth telling a reader is where to walk now.
        shelf_code = clean_text(first.get("location")) or clean_text(
            first.get("permanent_location")
        )
        shelf = shelves.get(shelf_code, shelf_code) if shelf_code else None

    return Availability(
        biblio_id=biblio_id,
        status=status,
        copies_total=len(items),
        copies_available=on_shelf,
        copies_reference_only=reference,
        library=library,
        shelf=shelf,
        call_number=call_number,
    )
