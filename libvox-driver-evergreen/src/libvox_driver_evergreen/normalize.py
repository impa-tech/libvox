"""Evergreen's records and copies, translated into the LibVox domain model.

Free functions, separate from the HTTP client, so they can be tested against
payloads captured from a live Evergreen with no network -- see `tests/`.

**One normalization serves both paths**, as in /libvox-driver-koha: the bulk
walk and the single-record lookup both go through `normalize`, so the index and
the live answer cannot disagree about the same book.

The ISBD cleaning is the Koha driver's `clean_text`, imported rather than
copied. It is not about Koha: " :" before a subtitle and " /" before a statement
of responsibility are MARC cataloguing convention, and Evergreen's records carry
exactly the same marks. Two copies of that rule would drift.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from libvox_driver_koha.contract import Availability, AvailabilityStatus, BiblioRecord
from libvox_driver_koha.errors import Suppressed, UnusableRecord
from libvox_driver_koha.normalize import clean_text

from .marc import MarcRecord, parse

logger = logging.getLogger(__name__)

# "0520078438 (alk. paper)" -> "0520078438"
_ISBN_QUALIFIER = re.compile(r"\s*\(.*?\)\s*")
_YEAR = re.compile(r"(1[5-9]\d\d|20\d\d)")

# Evergreen writes its UTC offset without a colon (-0600), which
# datetime.fromisoformat accepts only from Python 3.11.
_OFFSET = re.compile(r"([+-]\d\d)(\d\d)$")

# Leader position 06 (type of record), with 07 (bibliographic level) telling a
# serial from a book. Evergreen derives its own "format" from the same two
# positions; this is the coarse version of it, in words a reader would use.
_LEADER_TYPES = {
    "a": "book",
    "t": "book",
    "c": "score",
    "d": "score",
    "e": "map",
    "f": "map",
    "g": "video",
    "i": "audiobook",
    "j": "music recording",
    "k": "image",
    "m": "electronic resource",
    "o": "kit",
    "p": "mixed materials",
    "r": "object",
}


def flag(value: Any) -> bool:
    """Evergreen's booleans arrive as "t"/"f" from the gateway and as JSON
    true/false from the REST API."""
    if isinstance(value, str):
        return value.strip().lower() in ("t", "true", "1")
    return bool(value)


def _timestamp(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    text = _OFFSET.sub(r"\1:\2", value.strip())
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        logger.debug("unparseable Evergreen timestamp: %r", value)
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _title(marc: MarcRecord) -> str | None:
    """245: the title, its part number and name, then the subtitle.

    `$a` alone would drop "Volume 2" from a multi-part work and leave two
    records with the same name; `$b` is what a reader types after a colon."""
    for f in marc.datafields("245"):
        main = clean_text(f.first("a"))
        if not main:
            continue
        parts = [p for p in (clean_text(v) for v in f.all("n", "p")) if p]
        if parts:
            main = ". ".join([main, *parts])
        subtitle = clean_text(f.first("b"))
        return f"{main}: {subtitle}" if subtitle else main
    return None


def _author(marc: MarcRecord) -> str | None:
    """The main entry (100, 110, 111), or the first added entry when a record
    has none -- a compilation is still somebody's."""
    for tag in ("100", "110", "111", "700", "710", "711"):
        value = clean_text(marc.first(tag, "a"))
        if value:
            return value
    return None


def _isbn(marc: MarcRecord) -> str | None:
    for f in marc.datafields("020"):
        value = f.first("a")
        if value:
            cleaned = _ISBN_QUALIFIER.sub("", value).strip()
            if cleaned:
                return cleaned.split()[0]
    return None


def _publisher(marc: MarcRecord) -> str | None:
    """264 with second indicator 1 is the publisher under RDA; 260 is the same
    statement under AACR2. A catalogue holds both, record by record."""
    for f in marc.datafields("264"):
        if f.ind2 == "1" and (value := clean_text(f.first("b"))):
            return value
    return clean_text(marc.first("260", "b")) or clean_text(marc.first("264", "b"))


def _year(marc: MarcRecord) -> int | None:
    """The date of publication, from 264/260 $c, else from the fixed field.

    "c1979." and "[2000?]" are both 260 $c as catalogued; the first plausible
    four-digit year is what a reader means by "from 1979"."""
    for f in marc.datafields("264", "260"):
        match = _YEAR.search(f.first("c") or "")
        if match:
            return int(match.group(1))
    fixed = marc.control.get("008", "")
    if len(fixed) >= 11 and fixed[7:11].isdigit():
        return int(fixed[7:11])
    return None


def _item_type(marc: MarcRecord) -> str | None:
    leader = marc.leader
    if len(leader) < 8:
        return None
    if leader[6] == "a" and leader[7] in "sbi":
        return "serial"
    return _LEADER_TYPES.get(leader[6])


def _joined(marc: MarcRecord, tag: str, code: str) -> str | None:
    values = [v for v in (clean_text(f.first(code)) for f in marc.datafields(tag)) if v]
    return " ".join(values) or None


def normalize(row: dict[str, Any]) -> BiblioRecord:
    """Translate one Evergreen bibliographic record into the domain model.

    `row` is a `biblio.record_entry` read by field name: `id`, `marc`,
    `edit_date`, `deleted`, `active`. A deleted or inactive record is hidden
    from Evergreen's own catalogue and raises `Suppressed`, as a Koha record
    the library suppresses does -- it must not surface in an answer.
    """
    raw_id = row.get("id")
    try:
        biblio_id = int(raw_id)
    except (TypeError, ValueError):
        raise UnusableRecord(f"missing or non-integer record id: {raw_id!r}") from None

    if flag(row.get("deleted")) or ("active" in row and not flag(row.get("active"))):
        raise Suppressed(f"record {biblio_id} is deleted or inactive")

    # Already parsed when it came from an SRU answer, which carries many
    # records in one document; MARCXML text when it came from a page.
    source = row.get("marc")
    marc = source if isinstance(source, MarcRecord) else parse(source or "")
    title = _title(marc)
    if not title:
        raise UnusableRecord(f"record {biblio_id} has no title")

    return BiblioRecord(
        biblio_id=biblio_id,
        title=title,
        author=_author(marc),
        isbn=_isbn(marc),
        publisher=_publisher(marc),
        copyright_date=_year(marc),
        item_type=_item_type(marc),
        series=(
            clean_text(marc.first("490", "a"))
            or clean_text(marc.first("830", "a"))
            or clean_text(marc.first("440", "a"))
        ),
        abstract=_joined(marc, "520", "a"),
        notes=_joined(marc, "500", "a"),
        ils_timestamp=_timestamp(row.get("edit_date")),
    )


def normalize_page(rows: list[dict[str, Any]]) -> tuple[list[BiblioRecord], int]:
    """Normalize a page, dropping what cannot or must not be indexed. One bad
    record in a large catalogue is a log line, not a failed import."""
    records: list[BiblioRecord] = []
    skipped = 0
    for row in rows:
        try:
            records.append(normalize(row))
        except Suppressed as exc:
            logger.debug("skipping: %s", exc)
            skipped += 1
        except UnusableRecord as exc:
            logger.warning("skipping malformed record: %s", exc)
            skipped += 1
    return records, skipped


# --- availability ----------------------------------------------------------
#
# What "on the shelf" means is a cataloguing judgement, so the rules live here
# with their reasons.

# Evergreen's stock copy statuses. The ids below 100 are the system's own and
# are the same in every installation; a library adds its own above them.
_CHECKED_OUT = 1
_ON_HOLDS_SHELF = 8
_AVAILABLE_BY_DEFAULT = (0, 7)  # Available, Reshelving


def _visible(copy: dict[str, Any]) -> bool:
    """Whether the public catalogue shows this copy at all.

    Evergreen hides a copy when the copy, its shelving location or its status
    is marked not visible. A copy the library hides from its own OPAC is not
    one to count for a reader -- the same rule as a suppressed record."""
    if flag(copy.get("deleted")):
        return False
    for part in (copy, copy.get("location"), copy.get("status")):
        if isinstance(part, dict) and "opac_visible" in part and not flag(part["opac_visible"]):
            return False
    return True


def copy_state(copy: dict[str, Any]) -> str:
    """Classify one visible copy: shelf, reference, out, held, or gone."""
    status = copy.get("status") if isinstance(copy.get("status"), dict) else {}
    status_id = status.get("id")
    try:
        status_id = int(status_id)
    except (TypeError, ValueError):
        status_id = None

    available = (
        flag(status["is_available"])
        if "is_available" in status
        else status_id in _AVAILABLE_BY_DEFAULT
    )
    if available:
        # The copy and its shelving location each say whether it circulates. A
        # reference collection is usually configured on the location, so the
        # copy's own flag alone would miss a whole room of them.
        location = copy.get("location") if isinstance(copy.get("location"), dict) else {}
        if not flag(copy.get("circulate", True)) or not flag(location.get("circulate", True)):
            return "reference"
        return "shelf"
    if status_id == _CHECKED_OUT:
        return "out"
    if status_id == _ON_HOLDS_SHELF:
        return "held"
    return "gone"


def _call_number(copy: dict[str, Any]) -> str | None:
    volume = copy.get("call_number")
    if not isinstance(volume, dict):
        return None
    pieces = []
    for key in ("prefix", "label", "suffix"):
        value = volume.get(key)
        if isinstance(value, dict):
            value = value.get("label")
        if isinstance(value, str) and value.strip():
            pieces.append(value.strip())
    return clean_text(" ".join(pieces))


def availability_from_copies(
    biblio_id: int,
    copies: list[dict[str, Any]],
    *,
    library_names: dict[int, str] | None = None,
) -> Availability:
    """Summarize every visible copy of one record into the patron's answer.

    `status` describes borrowing, as in /libvox-driver-koha; the counts carry
    what four words cannot. Evergreen is usually a consortium, and a record's
    copies are spread across its branches -- the branch named is the first one
    holding a copy a reader could use.
    """
    names = library_names or {}
    visible = [c for c in copies if isinstance(c, dict) and _visible(c)]
    states = [copy_state(c) for c in visible]

    on_shelf = states.count("shelf")
    reference = states.count("reference")
    out = states.count("out")

    if on_shelf:
        status: AvailabilityStatus = "available"
    elif "held" in states or any(
        (c.get("holds_count") or 0) > 0 for c, s in zip(visible, states) if s == "out"
    ):
        status = "on_hold"
    elif out:
        status = "checked_out"
    else:
        status = "unavailable"

    usable = [c for c, s in zip(visible, states) if s in ("shelf", "reference")] or visible
    library = shelf = call_number = None
    if usable:
        first = usable[0]
        branch = first.get("circ_lib")
        try:
            branch = int(branch) if branch is not None else None
        except (TypeError, ValueError):
            branch = None
        library = names.get(branch) if branch is not None else None
        if library is None and branch is not None:
            library = str(branch)
        location = first.get("location")
        shelf = clean_text(location.get("name")) if isinstance(location, dict) else None
        call_number = _call_number(first)

    return Availability(
        biblio_id=biblio_id,
        status=status,
        copies_total=len(visible),
        copies_available=on_shelf,
        copies_reference_only=reference,
        library=library,
        shelf=shelf,
        call_number=call_number,
    )
