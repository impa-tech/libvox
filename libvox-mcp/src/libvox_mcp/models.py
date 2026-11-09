"""The universal LibVox domain model exposed through MCP.

These types are ILS-agnostic on purpose: the same schema serves Koha, FOLIO,
and any future driver. Normalization from the ILS's own shape happens in the
serverless drivers, well before anything reaches this layer.

Every return type here is also the tool's published output schema — the SDK
derives it from the annotation and validates results before they leave the
server.
"""

from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

IndexStatus = Literal["ready", "building", "stale"]
"""State of a tenant's Discovery Index.

`ready`    — index is current; an empty result set is a genuine "no matches".
`building` — initial sync in progress; results are partial.
`stale`    — last sync failed or is overdue; results may be outdated.
"""

MAX_AVAILABILITY_IDS = 5
"""How many records one availability call may ask about.

**A different kind of bound from MAX_RESULTS, and the only one that protects
somebody else.** The search cap is about comprehension -- twenty records a model
reads beat a hundred it skims -- and reaches nothing but this project's own
index. This one reaches the library's live server, one HTTP request per record,
and it is the number standing between a curious model and the rule the whole
architecture is built on: LibVox must never be the reason a library's OPAC goes
down.

It was twenty, and twenty was measured doing exactly what twenty does. Asked one
question, a model checked every record its search returned: twenty sequential
GETs to another institution's Koha over thirteen seconds, for an answer that
named a handful. The README already said availability goes live "for a handful
of records"; the number did not.

Here rather than in `server.py` because the direct-Koha experiment declares the
same tool and must not answer a different question about how many ids it
accepts. One number, one meaning, whichever backend is serving.
"""

AvailabilityStatus = Literal["available", "checked_out", "on_hold", "unavailable"]
"""What a patron can do with a record right now.

It describes **borrowing**, and it is a summary. A record with no loanable copy
and two reference copies is `unavailable` with `copies_reference_only = 2` —
which is not "we do not have it". The counts are the truth; the status is the
headline.
"""


class CatalogRecord(BaseModel):
    """A bibliographic record as held in the Discovery Index."""

    biblio_id: int = Field(description="ILS identifier, stable within one library.")
    title: str = Field(description="Title as catalogued.")
    author: str | None = Field(default=None, description="Primary author, if recorded.")
    isbn: str | None = Field(default=None, description="ISBN, if recorded.")
    publisher: str | None = Field(default=None, description="Publisher, if recorded.")
    copyright_date: int | None = Field(default=None, description="Year of publication.")
    item_type: str | None = Field(default=None, description="ILS material type code.")
    series: str | None = Field(
        default=None,
        description=(
            "Series or collection this work belongs to, if any. Often the name "
            "a reader knows a book by."
        ),
    )


class SearchCatalogResult(BaseModel):
    """What `search_catalog` returns.

    `index_status` is always present so the model can tell "no matches" apart
    from "not indexed yet" — the two are indistinguishable from an empty list,
    and confusing them produces a confidently wrong answer.
    """

    index_status: IndexStatus = Field(
        description=(
            "State of the catalog index. On 'building', tell the user the "
            "collection is still being indexed rather than reporting no "
            "results. On 'stale', note that results may be outdated."
        )
    )
    records: list[CatalogRecord] = Field(
        default_factory=list, description="Matching records, most relevant first."
    )
    truncated: bool = Field(
        default=False,
        description=(
            "True when more records matched than `limit` allowed. Say the "
            "library holds at least this many, never exactly this many, and "
            "offer to narrow the search."
        ),
    )
    undated_records: int | None = Field(
        default=None,
        description=(
            "How many records matched the search but carry no publication "
            "year, and so could not be reached by a year filter. Present only "
            "when a year was asked for. When it is greater than zero, say "
            "there may be more: a year filter only reaches what the library "
            "catalogued with a date."
        ),
    )
    searched_for: str | None = Field(
        default=None,
        description=(
            "Present only when the search you sent found nothing and one of "
            "its words was repaired against the words this library actually "
            "catalogued. These records answer this query, not the one you "
            "sent. Say which word was looked up — the reader is the only one "
            "who can tell you the repair was wrong."
        ),
    )


class AvailabilityRecord(BaseModel):
    """Live circulation status for one bibliographic record."""

    biblio_id: int = Field(description="Record this availability refers to.")
    status: AvailabilityStatus = Field(description="Current circulation status.")
    copies_total: int = Field(description="Physical copies the library holds.")
    copies_available: int = Field(
        description="Copies on the shelf and available to borrow right now."
    )
    copies_reference_only: int = Field(
        default=0,
        description=(
            "Copies on the shelf that do not leave the building. The patron "
            "can read these here today, but cannot take them home. Say so "
            "rather than reporting the record as unavailable."
        ),
    )
    library: str | None = Field(
        default=None,
        description=(
            "Which of the library's branches holds the copy, by name. It is a "
            "building a patron can walk to, never a shelf or a section within "
            "one — do not describe it as one."
        ),
    )
    shelf: str | None = Field(
        default=None,
        description=(
            "Where inside that building the copy sits — 'General stacks', "
            "'Reference', 'Children's area'. Say it with the library when both "
            "are present: a reader who knows only the building still has to ask "
            "at the desk. Often absent, and its absence means nothing was "
            "recorded, not that the book is loose."
        ),
    )
    call_number: str | None = Field(
        default=None,
        description=(
            "The shelf mark, which is how a reader finds the copy once they are "
            "at the right shelf. Give it verbatim, never rounded or tidied."
        ),
    )
    due_date: date | None = Field(
        default=None,
        description=(
            "When the earliest checked-out copy is due back, when the library's "
            "system reports it. Often absent — say the copy is out rather than "
            "guessing a date."
        ),
    )


class CheckAvailabilityResult(BaseModel):
    """What `check_availability` returns."""

    records: list[AvailabilityRecord] = Field(
        default_factory=list,
        description="One entry per requested record that the library holds.",
    )
