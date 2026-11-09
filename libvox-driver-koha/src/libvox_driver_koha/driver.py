"""The Koha driver: domain objects in, domain objects out.

Two entry points for the two execution paths LibVox runs:

* `get_biblio(id)` — the live path. One record, asked for now, because the
  answer has to be current.
* `fetch_page(cursor)` — the bulk path, walked by `/libvox-sync` to build the
  Discovery Index.

They share authentication and normalization on purpose. A library's catalog
answering one way to a live lookup and another way to the importer would put
the index and the live answer in disagreement about the same book.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .client import DEFAULT_COURTESY, KohaClient
from .contract import Availability, BiblioRecord, Page, SearchResult
from .errors import BadRequest, DriverError, NotFound, UpstreamError
from .normalize import (
    availability_from_items,
    clean_text,
    normalize,
    normalize_page,
)

logger = logging.getLogger(__name__)

MAX_PAGES = 10_000
"""A million records at the default page size. Not a limit anyone should reach:
it exists so a server that never returns an empty page fails loudly instead of
looping."""

DEFAULT_PAGE_SIZE = 100
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100


def escape_like(term: str) -> str:
    """Neutralize SQL LIKE wildcards in text a person typed.

    Not optional. Verified against a live Koha: a bare `%` as the search term
    matched **every record in the catalog**, because it reaches MySQL's LIKE
    as a wildcard. `_` is the same hazard one character wide, and a patron
    searching for `Windows 95_` would silently get the wrong set.

    The backslash goes first, or escaping the escape character would double
    back on the wildcards inserted after it.
    """
    return term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


class KohaDriver:
    """One driver instance serves one library.

    Verified against a running Koha (Koha Testing Docker, August 2026):

    * `_page` / `_per_page` paginate, 1-indexed, non-overlapping.
    * **No pagination headers.** No `X-Total-Count`, no `Link`. The total is
      unknowable up front, so extraction runs until a short or empty page
      arrives.
    * A page past the end returns `[]` rather than an error.
    """

    name = "koha"

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        min_interval_s: float = 0.2,
        courtesy: float = DEFAULT_COURTESY,
        timeout_s: float = 30.0,
        transport: Any | None = None,
    ) -> None:
        self._page_size = page_size
        # Branch codes to names, fetched once per driver instance. The list of
        # branches changes about as often as a library moves buildings, and one
        # instance serves one library — so this is a cache with the same
        # lifetime as the token, and for the same reason.
        self._library_names: dict[str, str] | None = None
        self._shelf_labels: dict[str, str] | None = None
        self._client = KohaClient(
            base_url,
            client_id=client_id,
            client_secret=client_secret,
            timeout_s=timeout_s,
            min_interval_s=min_interval_s,
            courtesy=courtesy,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> KohaDriver:  # noqa: PYI034 -- see below
        # ruff wants `Self`, which is right for a subclass and is `typing.Self`
        # from 3.11. This module declares 3.10, so honouring it would mean a
        # `typing_extensions` dependency for an annotation nobody reads.
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- live path ----------------------------------------------------------

    def get_biblio(self, biblio_id: int) -> BiblioRecord:
        """Fetch and normalize one record.

        Raises `NotFound` if the catalog has no such record, `Suppressed` if it
        has one the library hides, and `UnusableRecord` if it has one that
        cannot be represented. Returning `None` for all three would force every
        caller to re-derive a distinction the driver already knows.
        """
        if not isinstance(biblio_id, int) or isinstance(biblio_id, bool):
            raise UpstreamError(f"biblio_id must be an integer, got {biblio_id!r}")

        raw = self._client.get(f"/api/v1/biblios/{biblio_id}")

        if not isinstance(raw, dict):
            raise UpstreamError(
                f"expected a biblio object, got {type(raw).__name__}"
            )

        record = normalize(raw)
        logger.debug("fetched biblio %d: %s", record.biblio_id, record.title)
        return record

    def search_by_title(
        self, title: str, *, limit: int = DEFAULT_SEARCH_LIMIT
    ) -> SearchResult:
        """Find records whose title or subtitle contains `title`.

        **This is not the patron-facing search.** In LibVox a catalog search
        from the chat path is answered from the Discovery Index and generates
        no traffic against the library's server — that constraint is why the
        index exists. Wiring this method to the chat path would undo it, and
        would do so invisibly, because it works.

        What it is for: proving an integration end to end, reconciling the
        index against its source, and answering "does this library actually
        hold this?" while debugging.

        It is also weak search, by construction. `LIKE '%chess%'` matches
        inside words — on the sample catalog it returns *The Duchess of
        Malfi* — and it cannot stem, weight fields or rank. The Discovery
        Index does all three. Two components search differently on purpose;
        this one is a probe.
        """
        term = (title or "").strip()
        if not term:
            # An empty term becomes '%%', which matches the entire catalog.
            raise BadRequest("a search term is required")
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise BadRequest(f"limit must be an integer, got {limit!r}")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            # Refused rather than clamped: silently returning 100 of 1000 reads
            # as "that is all of them".
            raise BadRequest(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")

        pattern = f"%{escape_like(term)}%"
        # Koha's `q` takes SQL::Abstract as JSON. `_match` is not supported
        # across versions, so `like` is what works everywhere; the subtitle is
        # included because Koha stores it apart from the title, and a patron
        # typing the full name of a book is typing both.
        query = {
            "-or": [
                {"title": {"like": pattern}},
                {"subtitle": {"like": pattern}},
            ]
        }

        rows: Any = self._client.get(
            "/api/v1/biblios",
            q=json.dumps(query, separators=(",", ":")),
            _page=1,
            _per_page=limit,
        )
        if not isinstance(rows, list):
            raise UpstreamError(f"expected a list of biblios, got {type(rows).__name__}")

        records, skipped = normalize_page(rows)
        logger.info(
            "koha title search %r: %d rows, %d usable, %d skipped",
            term,
            len(rows),
            len(records),
            skipped,
        )
        # Truncation is measured on what Koha sent, not on what survived
        # normalization: a page filled entirely with suppressed records still
        # means there is more behind it.
        return SearchResult(records=records, truncated=len(rows) == limit)

    # -- bulk path ----------------------------------------------------------

    # -- live path: availability --------------------------------------------

    def check_availability(self, biblio_id: int) -> Availability:
        """How many copies of one record are on the shelf right now.

        Always live, never cached: the whole reason circulation is excluded
        from the Discovery Index is that a stale "available" sends someone
        across town for a book that is not there.

        One request. A due date would take a second one against /checkouts,
        which Koha gates behind the `circulate` permission — and LibVox asks
        libraries for a read-only key with `catalogue` alone, because it never
        writes to an ILS. Buying one date with the power to alter loans is not
        a trade this driver makes.
        """
        if not isinstance(biblio_id, int) or isinstance(biblio_id, bool):
            raise UpstreamError(f"biblio_id must be an integer, got {biblio_id!r}")

        items: Any = self._client.get(f"/api/v1/biblios/{biblio_id}/items")

        if not isinstance(items, list):
            raise UpstreamError(
                f"expected a list of items, got {type(items).__name__}"
            )

        # The shelving list is fetched only when a copy actually carries a
        # code. A library that puts everything in one room has none, and asking
        # for the vocabulary anyway is a fourth sequential request to someone
        # else's server for a field nobody filled in -- which is what took an
        # availability check past six seconds and past what the caller was
        # willing to wait.
        wants_shelves = any(
            isinstance(item, dict) and (item.get("location") or item.get("permanent_location"))
            for item in items
        )

        availability = availability_from_items(
            biblio_id,
            items,
            library_names=self._branch_names(),
            shelf_names=self._shelf_names() if wants_shelves else {},
        )
        logger.info(
            "koha availability for biblio %d: %s (%d of %d loanable, %d reference)",
            biblio_id,
            availability.status,
            availability.copies_available,
            availability.copies_total,
            availability.copies_reference_only,
        )
        return availability

    def _branch_names(self) -> dict[str, str]:
        """Branch code to human name, once per driver instance.

        A patron told the book is at "CPL" has learned nothing. Failure here is
        not fatal on purpose: knowing a copy is on the shelf and not knowing
        which building is a worse answer than the one above it, but it is still
        an answer, and losing it to a branch-list hiccup would be absurd.
        """
        if self._library_names is not None:
            return self._library_names

        try:
            rows = self._client.get("/api/v1/libraries")
        except DriverError as exc:
            logger.warning("could not resolve branch names: %s", exc)
            self._library_names = {}
            return self._library_names

        # Through `clean_text` like every catalogued string: a branch is named
        # by a cataloguer in the same interface, with the same conventions, and
        # this is the one value here that reaches a reader whole.
        self._library_names = {
            row["library_id"]: name
            for row in rows
            if isinstance(row, dict)
            and row.get("library_id")
            and (name := clean_text(row.get("name")))
        } if isinstance(rows, list) else {}
        return self._library_names

    def _shelf_names(self) -> dict[str, str]:
        """Shelving-location code to human name, once per driver instance.

        `GEN` is not an answer. Koha keeps these as authorised values in
        category `LOC`, and the endpoint that lists them needs `catalogue: 1` --
        the same permission LibVox already asks a library for, and the reason
        this is worth doing at all. A field that required `parameters` would not
        be, however useful.

        Failure is not fatal, for the reason `_branch_names` gives: knowing the
        shelf and not its name is worse than knowing both and better than
        knowing neither, and the code is passed through when the list cannot be
        read.
        """
        if self._shelf_labels is not None:
            return self._shelf_labels

        try:
            rows = self._client.get(
                "/api/v1/authorised_value_categories/LOC/authorised_values"
            )
        except DriverError as exc:
            logger.warning("could not resolve shelving locations: %s", exc)
            self._shelf_labels = {}
            return self._shelf_labels

        # `opac_description` before `description`: Koha keeps two labels per
        # authorised value, and the second is the staff interface's. MARC.IA
        # speaks to readers, so the public one wins where a library set it --
        # which is rarely. It was null on all ten locations of the instance this
        # was written against, so the fallback is the ordinary path, not the
        # exception.
        self._shelf_labels = {
            row["value"]: label
            for row in rows
            if isinstance(row, dict)
            and row.get("value")
            and (
                label := clean_text(
                    row.get("opac_description") or row.get("description")
                )
            )
        } if isinstance(rows, list) else {}
        return self._shelf_labels

    def fetch_page(self, cursor: str | None = None) -> Page:
        page_number = int(cursor) if cursor else 1

        rows: Any = self._client.get(
            "/api/v1/biblios", _page=page_number, _per_page=self._page_size
        )
        if not isinstance(rows, list):
            raise UpstreamError(
                f"expected a list of biblios, got {type(rows).__name__}"
            )

        records, skipped = normalize_page(rows)

        # **Only an empty page ends the walk.**
        #
        # Koha sends no total and no Link header, so there is nothing to
        # compare against. Treating a *short* page as the end was the obvious
        # reading, and it trusts the server to be healthy: one that truncates a
        # response under load — and this driver has watched servers time out
        # and drop connections mid-response — would end the walk early, and the
        # import would publish a partial catalogue as `ready`. MARC.IA would
        # then tell readers the library does not hold two thirds of its books,
        # confidently, which is the failure this whole system is built against.
        #
        # So a short page is not trusted; an empty one is. The cost is one extra
        # request per import, which the old rule already paid whenever the last
        # page happened to be exactly full.
        #
        # Duplicates are safe: Koha paginates by offset, so a catalogue edited
        # mid-walk already hands the same record over twice, and the pipeline
        # absorbs that with ON CONFLICT DO NOTHING.
        if not rows:
            next_cursor = None
        elif page_number >= MAX_PAGES:
            # A server that clamps an out-of-range page to the last one would
            # answer forever. Loud, rather than a loop nobody notices.
            raise UpstreamError(
                f"{MAX_PAGES} pages and still no empty one; the server may be "
                "repeating a page rather than paginating"
            )
        else:
            next_cursor = str(page_number + 1)

        logger.info(
            "koha page %d: %d rows, %d usable, %d skipped",
            page_number,
            len(rows),
            len(records),
            skipped,
        )
        return Page(records=records, next_cursor=next_cursor, skipped=skipped)


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "KohaDriver",
    "NotFound",
    "escape_like",
]
