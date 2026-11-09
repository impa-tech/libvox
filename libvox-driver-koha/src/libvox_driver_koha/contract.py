"""What every ILS driver produces, independent of which ILS it talks to.

Nothing here mentions Koha. It lives in this package only because Koha is the
first driver; when `/libvox-driver-folio` arrives, this module moves to a shared
`libvox-common` and both drivers import it. Keeping it separate now is what
makes that a file move rather than an untangling.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Literal, Protocol, runtime_checkable

AvailabilityStatus = Literal["available", "checked_out", "on_hold", "unavailable"]
"""The four answers a patron can be given about one record.

Deliberately the same set as `libvox_mcp.models.AvailabilityStatus`. It
describes **borrowing**, and it is a summary: a library that holds three copies
in three states is one status and three counts, and the counts are the truth.
"""


@dataclass(frozen=True, slots=True)
class BiblioRecord:
    """One bibliographic record, normalized.

    The same seven descriptive fields as `libvox_mcp.models.CatalogRecord`,
    plus the ILS modification time that drives incremental sync. Downstream of
    a driver, nothing knows what a MARC field is.
    """

    biblio_id: int
    title: str
    author: str | None = None
    isbn: str | None = None
    publisher: str | None = None
    copyright_date: int | None = None
    item_type: str | None = None

    series: str | None = None
    """The series or collection a work belongs to.

    Worth its own field because it is often the word a patron actually types.
    A catalog of Pokémon records has titles like "Metapod" and a series of
    "Pokémon - Generation I": someone asking for pokémon finds nothing at all
    unless this is carried."""

    abstract: str | None = None
    notes: str | None = None
    """Descriptive text the ILS already has. Indexed downstream, not returned
    to a model: it is what lets a search for "chrysalis" reach Metapod, and it
    is far too long to put twenty of in a tool result."""

    ils_timestamp: datetime | None = None

    def as_dict(self) -> dict[str, object]:
        """JSON-ready form, for the invocation contract."""
        return {
            "biblio_id": self.biblio_id,
            "title": self.title,
            "author": self.author,
            "isbn": self.isbn,
            "publisher": self.publisher,
            "copyright_date": self.copyright_date,
            "item_type": self.item_type,
            "series": self.series,
            "abstract": self.abstract,
            "notes": self.notes,
            "ils_timestamp": (
                self.ils_timestamp.isoformat() if self.ils_timestamp else None
            ),
        }


@dataclass(frozen=True, slots=True)
class SearchResult:
    """What a title lookup found, and whether that was all of it."""

    records: list[BiblioRecord] = field(default_factory=list)

    truncated: bool = False
    """The ILS had at least one more match than the limit allowed. Reported
    rather than hidden: "3 results" and "the first 20 of unknown many" are
    different answers, and a caller that cannot tell them apart will state the
    wrong one confidently."""

    def as_dict(self) -> dict[str, object]:
        return {
            "records": [record.as_dict() for record in self.records],
            "count": len(self.records),
            "truncated": self.truncated,
        }


@dataclass(frozen=True, slots=True)
class Availability:
    """Live circulation status for one bibliographic record.

    Never cached, anywhere. A stale "available" sends someone across town for a
    book that is not there, which is worse than saying nothing — see the root
    README on why circulation is excluded from the Discovery Index.
    """

    biblio_id: int
    status: AvailabilityStatus

    copies_total: int = 0
    """Every physical copy the library holds, in whatever state."""

    copies_available: int = 0
    """On the shelf and borrowable, right now."""

    copies_reference_only: int = 0
    """On the shelf, but they do not leave the building.

    Counted separately rather than folded into either side, because "we have it
    and you can take it home" and "we have it and you must read it here" are
    different answers to the only question a patron asked. Folding them into
    `copies_available` would promise a loan the library will refuse at the desk;
    dropping them would hide a copy that is sitting right there.
    """

    library: str | None = None
    """Which of the library's branches holds it, by name — 'Centerville'.

    **Called `library` and not `location` on purpose.** It used to be the
    latter, and a model reading a field with that name told a reader the book
    was *"na seção Midway"*. Midway is a branch; a seção is a shelving
    location, which in Koha is a different field (`location`, 'GEN') that this
    contract does not carry. The name was the whole of the mistake.

    `library` is also Koha's own word — `library_id`, `/api/v1/libraries`,
    `home_library_id`. Inside LibVox "library" otherwise means the tenant, and
    there is exactly one place that cannot be confused: a tool result, where
    the tenant never appears at all.

    Falls back to Koha's raw code when the branch list could not be fetched:
    "CPL" tells a patron less than "Centerville" but more than nothing.
    """

    shelf: str | None = None
    """Where in that building — 'General stacks', 'Reference', 'Audio visual'.

    Koha's *shelving location*: the `location` field on an item, an authorised
    value in category `LOC`. Resolved to its description the same way a branch
    code is, and for the same reason — 'GEN' tells a reader nothing.

    **Called `shelf` rather than `location`, deliberately.** `location` is
    Koha's own name for it and would be the accurate one, but this contract
    spent its first weeks calling the *branch* that, and a model reading the
    misnamed field told a reader a book was "na seção Midway" when Midway is a
    building. Reusing the word now would be correct and unreadable; `shelf` is
    neither the old mistake nor a synonym for `library`.

    Null is ordinary. A library that puts everything in one room has no reason
    to fill this in.
    """

    call_number: str | None = None

    due_date: date | None = None
    """When the earliest checked-out copy comes back — **always None here.**

    Koha reports a due date only through /checkouts, which requires the
    `circulate` permission. LibVox asks libraries for a read-only key with
    `catalogue` and nothing else, and never writes to an ILS, so requesting
    circulate to fill one field would buy a date with the power to alter loans.
    The field stays for the day an ILS offers it without that trade.
    """

    def as_dict(self) -> dict[str, object]:
        """JSON-ready form, for the invocation contract."""
        return {
            "biblio_id": self.biblio_id,
            "status": self.status,
            "copies_total": self.copies_total,
            "copies_available": self.copies_available,
            "copies_reference_only": self.copies_reference_only,
            "library": self.library,
            "shelf": self.shelf,
            "call_number": self.call_number,
            "due_date": self.due_date.isoformat() if self.due_date else None,
        }


@dataclass(frozen=True, slots=True)
class Page:
    """One page of bulk extraction results."""

    records: list[BiblioRecord] = field(default_factory=list)

    next_cursor: str | None = None
    """Opaque, driver-defined. `None` means the extraction is complete."""

    skipped: int = 0
    """Rows the driver refused to emit — suppressed from the public catalog, or
    too malformed to normalize. Counted so a silent shortfall in the index is
    visible rather than mysterious."""


@runtime_checkable
class Driver(Protocol):
    """What a consumer requires of an ILS integration.

    Two methods for two execution paths: `get_biblio` answers a live question
    about one record, `fetch_page` walks the whole catalog for the Discovery
    Index. A driver implements both because both need the same authentication
    and the same normalization.
    """

    @property
    def name(self) -> str:
        """Short identifier for logs, e.g. 'koha'."""
        ...

    def get_biblio(self, biblio_id: int) -> BiblioRecord:
        """Fetch and normalize one record. Raises on absence — see errors.py."""
        ...

    def search_by_title(self, title: str, *, limit: int = 20) -> SearchResult:
        """Find records whose name contains `title`. Diagnostic, not the
        patron-facing search — see the note on `KohaDriver.search_by_title`."""
        ...

    def fetch_page(self, cursor: str | None = None) -> Page:
        """Fetch one page, starting from `cursor` (`None` for the first)."""
        ...

    def check_availability(self, biblio_id: int) -> Availability:
        """How many copies are on the shelf right now. Always live."""
        ...


def iter_records(driver: Driver) -> Iterator[BiblioRecord]:
    """Walk every page of a driver.

    Pagination stops when a driver reports no next cursor. Drivers are
    responsible for their own throttling — the consumer must not be the thing
    deciding how hard to hit someone else's library server.
    """
    cursor: str | None = None
    while True:
        page = driver.fetch_page(cursor)
        yield from page.records
        if page.next_cursor is None:
            return
        cursor = page.next_cursor
