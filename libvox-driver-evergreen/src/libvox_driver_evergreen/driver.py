"""The Evergreen driver: domain objects in, domain objects out.

The same two paths as /libvox-driver-koha, through the same contract:

* `check_availability(id)` and `get_biblio(id)` -- the live path, one record,
  asked for now;
* `fetch_page(cursor)` -- the bulk path, walked by /libvox-sync to build the
  Discovery Index.

Record ids are Evergreen's own `biblio.record_entry.id`: integers, stable for
the life of a record, and the same number the library's OPAC puts in its
record address. Nothing has to be mapped to fit the contract.
"""

from __future__ import annotations

import logging
from typing import Any

from libvox_driver_koha.contract import Availability, BiblioRecord, Page, SearchResult
from libvox_driver_koha.errors import BadRequest, DriverError, NotFound, UpstreamError

from .client import DEFAULT_COURTESY, DEFAULT_TIMEOUT_S, EvergreenClient
from .fieldmapper import IDL_PATH, fields_from_idl, to_dict
from .marc import records_in
from .normalize import availability_from_copies, normalize, normalize_page

logger = logging.getLogger(__name__)

DEFAULT_PAGE_SIZE = 100
DEFAULT_SEARCH_LIMIT = 20
MAX_SEARCH_LIMIT = 100

RECORD_CLASS = "bre"
"""`biblio.record_entry`, in the IDL's short names."""

RECORD_FIELDS = ["id", "marc", "edit_date", "deleted", "active"]
"""What a page asks for. `pcrud` sends every position regardless, with the rest
null, so this keeps a page of a hundred records to the hundred MARC documents
it is for."""

SRU_PATH = "/opac/extras/sru"


class EvergreenDriver:
    """One driver instance serves one library.

    Verified against a running Evergreen 3.17.3 (November 2026):

    * `pcrud` pages `biblio.record_entry` by id with `{"id": {">": last}}`,
      ordered by id: keyset pagination, so an edit made mid-walk neither skips
      nor repeats a record, unlike Koha's offset pages.
    * Deleted records stay in the table with `deleted = t`. They are walked and
      counted as skipped, so a shortfall in the index has a number.
    * `/bib/{id}/holdings` answers `[]` for a record that does not exist, not
      404. Core only ever asks about ids its own index holds.
    """

    name = "evergreen"

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        page_size: int = DEFAULT_PAGE_SIZE,
        min_interval_s: float = 0.2,
        courtesy: float = DEFAULT_COURTESY,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        verify: bool | str = True,
        transport: Any | None = None,
    ) -> None:
        self._page_size = page_size
        self._record_fields: list[str] | None = None
        # Branch ids to names, once per driver -- the same lifetime as the
        # session, and for the same reason: one instance serves one library.
        self._library_names: dict[int, str] | None = None
        self._client = EvergreenClient(
            base_url,
            username=username,
            password=password,
            timeout_s=timeout_s,
            min_interval_s=min_interval_s,
            courtesy=courtesy,
            verify=verify,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> EvergreenDriver:  # noqa: PYI034 - py3.10, see koha
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- records ------------------------------------------------------------

    def _fields(self) -> list[str]:
        if self._record_fields is None:
            idl = self._client.text(IDL_PATH, **{"class": RECORD_CLASS})
            self._record_fields = fields_from_idl(idl, RECORD_CLASS)
        return self._record_fields

    def _rows(self, payload: list) -> list[dict[str, Any]]:
        fields = self._fields()
        return [to_dict(obj, fields, RECORD_CLASS) for obj in payload]

    def get_biblio(self, biblio_id: int) -> BiblioRecord:
        """Fetch and normalize one record. `NotFound` when there is none,
        `Suppressed` when it is deleted or inactive."""
        if not isinstance(biblio_id, int) or isinstance(biblio_id, bool):
            raise UpstreamError(f"biblio_id must be an integer, got {biblio_id!r}")

        payload = self._client.gateway(
            "open-ils.pcrud", "open-ils.pcrud.retrieve.bre", biblio_id
        )
        if not payload or payload[0] is None:
            raise NotFound(f"no record {biblio_id}")
        return normalize(self._rows(payload[:1])[0])

    def fetch_page(self, cursor: str | None = None) -> Page:
        """One page of the catalogue, after the record id in `cursor`.

        The cursor is the last id seen. An empty page ends the walk -- the same
        rule as the Koha driver, and for a stronger reason here: keyset pages
        cannot repeat, so an empty one is the end and nothing else is."""
        after = int(cursor) if cursor else 0

        payload = self._client.gateway(
            "open-ils.pcrud",
            "open-ils.pcrud.search.bre.atomic",
            {"id": {">": after}},
            {
                "select": {RECORD_CLASS: RECORD_FIELDS},
                "order_by": {RECORD_CLASS: "id"},
                "limit": self._page_size,
            },
        )
        objects = payload[0] if payload and isinstance(payload[0], list) else []
        rows = self._rows(objects)
        records, skipped = normalize_page(rows)

        ids = [int(row["id"]) for row in rows if str(row.get("id", "")).isdigit()]
        if not rows:
            next_cursor = None
        elif not ids or max(ids) <= after:
            # A server that ignored the filter would answer the same page for
            # ever. Loud, rather than a loop nobody notices.
            raise UpstreamError(
                f"the page after record {after} did not advance; the server "
                "may be ignoring the id filter"
            )
        else:
            next_cursor = str(max(ids))

        logger.info(
            "evergreen page after %d: %d rows, %d usable, %d skipped",
            after,
            len(rows),
            len(records),
            skipped,
        )
        return Page(records=records, next_cursor=next_cursor, skipped=skipped)

    def search_by_title(
        self, title: str, *, limit: int = DEFAULT_SEARCH_LIMIT
    ) -> SearchResult:
        """Records whose title matches `title`, from Evergreen's own SRU search.

        **Diagnostic, not the reader's search** -- reader search is answered by
        the Discovery Index, so that it generates no traffic against the
        library's server. This is for proving an integration and reconciling
        the index against its source.
        """
        term = (title or "").strip()
        if not term:
            raise BadRequest("a search term is required")
        if not isinstance(limit, int) or isinstance(limit, bool):
            raise BadRequest(f"limit must be an integer, got {limit!r}")
        if not 1 <= limit <= MAX_SEARCH_LIMIT:
            raise BadRequest(f"limit must be between 1 and {MAX_SEARCH_LIMIT}")

        quoted = term.replace("\\", " ").replace('"', " ")
        xml = self._client.text(
            SRU_PATH,
            version="1.1",
            operation="searchRetrieve",
            query=f'title="{quoted}"',
            maximumRecords=str(limit),
        )
        rows = []
        for marc in records_in(xml):
            # 901 $c is where Evergreen writes the record's own id into its MARC.
            record_id = marc.first("901", "c")
            if record_id and record_id.strip().isdigit():
                rows.append({"id": int(record_id), "marc": marc})
        records, skipped = normalize_page(rows)
        logger.info("evergreen title search %r: %d records", term, len(records))
        return SearchResult(records=records, truncated=len(rows) == limit)

    # -- availability -------------------------------------------------------

    def check_availability(self, biblio_id: int) -> Availability:
        """How many copies of one record are on the shelf right now.

        Always live, never cached. One REST request for the copies, plus one
        per driver for the branch names. A due date is not asked for: it lives
        on the circulation, and reading circulations is a patron-data
        permission a read-only catalogue account should not hold.
        """
        if not isinstance(biblio_id, int) or isinstance(biblio_id, bool):
            raise UpstreamError(f"biblio_id must be an integer, got {biblio_id!r}")

        copies = self._client.rest(f"/bib/{biblio_id}/holdings")
        if not isinstance(copies, list):
            raise UpstreamError(f"expected a list of copies, got {type(copies).__name__}")

        availability = availability_from_copies(
            biblio_id,
            copies,
            library_names=self._branch_names() if copies else {},
        )
        logger.info(
            "evergreen availability for record %d: %s (%d of %d loanable, %d reference)",
            biblio_id,
            availability.status,
            availability.copies_available,
            availability.copies_total,
            availability.copies_reference_only,
        )
        return availability

    def _branch_names(self) -> dict[int, str]:
        """Org unit id to name, once per driver. A failure here is not fatal:
        a copy on the shelf at an unnamed branch is still an answer."""
        if self._library_names is not None:
            return self._library_names
        try:
            rows = self._client.rest("/org_units")
        except DriverError as exc:
            logger.warning("could not resolve branch names: %s", exc)
            self._library_names = {}
            return self._library_names

        names: dict[int, str] = {}
        for row in rows if isinstance(rows, list) else []:
            if not isinstance(row, dict):
                continue
            try:
                key = int(row.get("id"))
            except (TypeError, ValueError):
                continue
            name = row.get("name")
            if isinstance(name, str) and name.strip():
                names[key] = name.strip()
        self._library_names = names
        return names


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "EvergreenDriver",
]
