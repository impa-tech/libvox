"""What crosses the wire to `/libvox-mcp`.

These mirror `libvox_mcp.models` field for field, and they are declared here
rather than imported from there.

**Why not import them.** `/libvox-mcp` is the protocol adapter and this is the
implementation behind it; depending on the adapter would invert that, and would
pull the whole MCP SDK into a service that speaks plain HTTP. The models are
the *contract*, not the adapter, and a contract both sides state is a contract
either side can be run without.

**What keeps them honest.** A drift test compares these to
`libvox_mcp.models` field by field and fails naming whatever diverged. It skips
when that module is not installed, so neither component hard-depends on the
other — the same arrangement `/libvox-sync` uses for the index columns.

The one thing not to change casually is `index_status`: the client collapses
every non-200 into a single opaque sentence for the model, so a condition the
model must act on has to be a field in here rather than a status code.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

IndexStatus = Literal["ready", "building", "stale"]
AvailabilityStatus = Literal["available", "checked_out", "on_hold", "unavailable"]


class CatalogRecord(BaseModel):
    biblio_id: int
    title: str
    author: str | None = None
    isbn: str | None = None
    publisher: str | None = None
    copyright_date: int | None = None
    """A year, not a date. The index stores an integer."""
    item_type: str | None = None
    series: str | None = None
    """Often what a reader calls the book. The index also searches abstract and
    notes, which are deliberately not carried here: found by, not quoted from."""


class SearchCatalogResult(BaseModel):
    index_status: IndexStatus
    records: list[CatalogRecord] = Field(default_factory=list)
    truncated: bool = False
    undated_records: int | None = None
    """How many records matched everything but the year filter and carry no
    year. `None` when no year was asked for, so a search that mentions no year
    is the same shape it always was."""
    searched_for: str | None = None
    """The query these records answer, when the index repaired a word the
    library does not have. `None` when nothing was repaired, which is almost
    always."""


class AvailabilityRecord(BaseModel):
    biblio_id: int
    status: AvailabilityStatus
    copies_total: int
    copies_available: int
    copies_reference_only: int = 0
    library: str | None = None
    """The branch holding it. Not `location`: see the driver's contract for the
    answer that name produced."""
    shelf: str | None = None
    """Where in that building — Koha's shelving location, resolved to a name."""
    call_number: str | None = None
    due_date: date | None = None
    """Serialized as YYYY-MM-DD. Almost always absent: Koha reports a due date
    only through /checkouts, behind the `circulate` permission LibVox does not
    ask libraries for."""


class CheckAvailabilityResult(BaseModel):
    records: list[AvailabilityRecord] = Field(default_factory=list)
    """One entry per record the library holds. A record it does not hold is
    omitted, never padded with an `unavailable` placeholder."""


class IlsCheckResult(BaseModel):
    """Whether a library's catalogue connection works, and what broke if not.

    Not part of the MCP contract: no tool returns this and no model sees it.
    It answers a question a librarian asks about her own configuration, in
    `/libvox-portal`.
    """

    ok: bool
    problem: str | None = None
    """The driver's own error code -- `unauthorized`, `permission_denied`,
    `unavailable` -- so the panel can say which of the three it was. "It did not
    work" sends someone to check all of them."""

    detail: str | None = None
    """One sentence, for the librarian. It names her own server and the status
    it returned, which is what the operator's-log rule protects a *patron*
    from, not the person who owns the server."""

    records_seen: int = 0
    """A catalogue that authenticates and returns nothing is the failure this
    system is most careful about: it is how a library ends up telling readers
    it holds no books."""
