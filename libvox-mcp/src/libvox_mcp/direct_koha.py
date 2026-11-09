"""EXPERIMENT — the MCP server talking straight to one Koha, with no Core.

Everything that deviates from the LibVox architecture lives in this file, so
removing the experiment is deleting one module and one branch in `server.py`.
Nothing in `core_client.py`, `auth.py` or the production path imports it.

**What this deliberately breaks, and why it must not ship**

* *Search hits the library's server.* In LibVox a patron search is answered
  from the Discovery Index precisely so it never becomes load on someone
  else's ILS. Here every search is a live query. That is the whole point of
  the experiment — to watch an agent use these tools — and it is also exactly
  the thing the architecture exists to prevent.
* *There is no tenant.* No OAuth, no credential, no `require_tenant()`. The
  process holds one library's key from its environment and serves whoever
  connects. Single-tenant by construction, which is safe only because it can
  serve exactly one library at a time.
* *Search quality is poor.* `search_by_title` is `LIKE '%term%'`: it matches
  inside words, cannot stem, and cannot rank. Searching "chess" on the sample
  catalog returns *The Duchess of Malfi*. An agent's behaviour here is a
  lower bound on what the Discovery Index will give it.

Enable with `LIBVOX_MCP_BACKEND=koha`. The default is unchanged.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

import anyio.to_thread
from libvox_driver_koha import (
    DriverError,
    KohaDriver,
    NotFound,
    Suppressed,
    Unavailable,
)
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import Field

from .config import Settings
from .models import (
    MAX_AVAILABILITY_IDS,
    AvailabilityRecord,
    CatalogRecord,
    CheckAvailabilityResult,
    SearchCatalogResult,
)

logger = logging.getLogger(__name__)

MAX_RESULTS = 20

INSTRUCTIONS = """\
Tools for searching one library's catalog.

search_catalog finds records by title. get_biblio returns the full record for
one id, when the user asks about a specific book you already found.

A search result describes what the library catalogues, not what is on the shelf
right now. Pass the ids to check_availability for that — never infer it from a
search result, and never answer from a search result you ran earlier, because
availability changes minute to minute.

Search matches text inside titles, so unrelated books can appear: a search for
"chess" also returns "The Duchess of Malfi". Read the titles before presenting
them as matches.

Matching is literal, with no translation: the term is compared to the title as
catalogued, in whatever language that library uses. One search, one answer — an
empty result means the library holds nothing under that word, and saying so is
the answer.
"""


class CatalogUnavailable(Exception):
    """The library's system could not answer. Surfaces as a tool error."""


@dataclass(slots=True)
class AppContext:
    driver: KohaDriver


def _record(biblio) -> CatalogRecord:
    """Driver's domain object into the MCP output schema.

    Two dataclasses over the same fields, deliberately not shared: the
    published tool schema should not change because a driver refactored. The
    driver also carries `abstract` and `notes`, which stop here -- they exist
    to make a record findable, not to be quoted back.
    """
    return CatalogRecord(
        biblio_id=biblio.biblio_id,
        title=biblio.title,
        author=biblio.author,
        isbn=biblio.isbn,
        publisher=biblio.publisher,
        copyright_date=biblio.copyright_date,
        item_type=biblio.item_type,
        series=biblio.series,
    )


def create_direct_server(settings: Settings) -> MCPServer:
    """Build an MCP server backed by one Koha, with no Core and no auth."""
    connection = settings.require_koha()

    driver = KohaDriver(
        connection["base_url"],
        client_id=connection["client_id"],
        client_secret=connection["client_secret"],
    )

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
        logger.warning(
            "libvox-mcp in DIRECT KOHA mode (%s) — no tenant isolation, "
            "every search hits the library's server",
            connection["base_url"],
        )
        try:
            yield AppContext(driver=driver)
        finally:
            await anyio.to_thread.run_sync(driver.close)

    mcp = MCPServer(
        "LibVox (direct Koha)",
        title="LibVox — direct Koha (experiment)",
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        # No token_verifier and no AuthSettings: there is no Core to resolve a
        # credential against, and a fake verifier would be worse than none —
        # it would look like the real thing in a screenshot.
    )

    # The driver is synchronous (httpx2.Client). Running it inline would block
    # the event loop for the whole round trip to the ILS, which on a slow
    # library server is seconds.
    async def _call(fn, *args, **kwargs):
        try:
            return await anyio.to_thread.run_sync(lambda: fn(*args, **kwargs))
        except Unavailable as exc:
            logger.warning("koha unreachable: %s", exc)
            raise CatalogUnavailable(
                "The library's catalog system is not responding right now."
            ) from exc
        except DriverError as exc:
            # Deliberately not passing the driver's message through: it names
            # URLs and permissions, which belong in the operator's log and not
            # in a conversation with a member of the public.
            logger.warning("koha refused the request: %s (%s)", exc.message, exc.code)
            raise CatalogUnavailable(
                "The library's catalog could not answer that request."
            ) from exc

    @mcp.tool(
        title="Search the library catalog",
        annotations=ToolAnnotations(
            read_only_hint=True,
            idempotent_hint=True,
            open_world_hint=True,  # live ILS, unlike the indexed production path
        ),
    )
    async def search_catalog(
        query: Annotated[
            str,
            Field(
                description=(
                    "Words from the title of the book to look for, in the "
                    "reader's own words. Matched literally against the title "
                    "as catalogued, in whatever language that library uses. "
                    # No repair here, and the tool says so where the model
                    # reads it: this path is Koha's `like` and has no index
                    # behind it, so there is no vocabulary to repair a typo
                    # against. The Core path does that in the database.
                    "A misspelling finds nothing — there is no index here to "
                    "repair one."
                ),
                min_length=1,
            ),
        ],
        ctx: Context[AppContext],
        limit: Annotated[
            int,
            Field(
                ge=1,
                description=(
                    f"How many records to return. More than {MAX_RESULTS} is "
                    f"served as {MAX_RESULTS}."
                ),
            ),
        ] = 5,
    ) -> SearchCatalogResult:
        """Search this library's catalog by title.

        Returns bibliographic records, not availability: a record means the
        library catalogues the book, not that a copy is on the shelf.

        Matching is on text contained in the title, so a short query can return
        unrelated books. Check that each title is plausibly what the user meant
        before presenting it.

        There is no translation and no stemming: the term is matched literally
        against the title as catalogued, in whatever language the library
        catalogued it. One search, one answer — an empty result means the
        library holds nothing under that word, and saying so in a sentence is
        the answer. Running the query again returns the same nothing while the
        patron waits.

        When `truncated` is true, more records matched than were returned: say
        the library holds at least this many, not exactly this many.
        """
        driver = ctx.request_context.lifespan_context.driver
        # Clamped rather than refused; see the production tool for why.
        result = await _call(driver.search_by_title, query, limit=min(limit, MAX_RESULTS))

        return SearchCatalogResult(
            # In the result, not a log notification: the model reads results
            # and never reads the server's log. Without this it reports "the
            # library has N" when N is simply where the page ended.
            truncated=result.truncated,
            # There is no Discovery Index in this mode. 'ready' is still the
            # honest value for what this field means to the model — that an
            # empty result is a real "no matches" rather than a catalog that
            # has not finished indexing.
            index_status="ready",
            records=[_record(r) for r in result.records],
        )

    @mcp.tool(
        title="Get one catalog record",
        annotations=ToolAnnotations(
            read_only_hint=True,
            idempotent_hint=True,
            open_world_hint=True,
        ),
    )
    async def get_biblio(
        biblio_id: Annotated[
            int,
            Field(
                description=(
                    "Record id from a previous search_catalog result. Only use "
                    "ids that came from a search in this conversation."
                )
            ),
        ],
        ctx: Context[AppContext],
    ) -> CatalogRecord | None:
        """Fetch one catalog record by its id.

        Returns null when the library holds no such record — that is an answer,
        not a failure. Do not retry with a different id you invented.
        """
        driver = ctx.request_context.lifespan_context.driver
        try:
            return _record(await _call(driver.get_biblio, biblio_id))
        except CatalogUnavailable as exc:
            # NotFound and Suppressed arrive wrapped by _call; both mean "the
            # patron cannot have this record", and telling them apart here
            # would leak that a hidden record exists.
            cause = exc.__cause__
            if isinstance(cause, (NotFound, Suppressed)):
                return None
            raise

    @mcp.tool(
        title="Check what is on the shelf",
        annotations=ToolAnnotations(
            read_only_hint=True,
            idempotent_hint=False,  # the answer changes minute to minute
            open_world_hint=True,
        ),
    )
    async def check_availability(
        biblio_ids: Annotated[
            list[int],
            Field(
                description=(
                    "Record ids from a previous search_catalog result. Only "
                    "use ids that came from a search in this conversation."
                ),
                min_length=1,
                max_length=MAX_AVAILABILITY_IDS,
            ),
        ],
        ctx: Context[AppContext],
    ) -> CheckAvailabilityResult:
        """Check live circulation status for specific catalog records.

        Asks the library's system right now, so the answer reflects this
        minute. Never infer availability from a search result, and never reuse
        an answer from earlier in the conversation.

        Read `copies_reference_only` before saying a record is unavailable: a
        record with no loanable copy and reference copies on the shelf can be
        read at the library today, and reporting it as simply unavailable sends
        someone away from a book that is sitting there.

        `due_date` is often absent. When it is, say the copy is out; do not
        guess when it comes back.

        Records the library does not hold are left out of the result rather
        than reported as unavailable.
        """
        driver = ctx.request_context.lifespan_context.driver

        records: list[AvailabilityRecord] = []
        for biblio_id in biblio_ids:
            try:
                result = await _call(driver.check_availability, biblio_id)
            except CatalogUnavailable as exc:
                # One unknown id must not cost the patron the other answers.
                # NotFound and Suppressed both mean "not for this patron", and
                # telling them apart would leak that a hidden record exists.
                if isinstance(exc.__cause__, (NotFound, Suppressed)):
                    continue
                raise

            records.append(
                AvailabilityRecord(
                    biblio_id=result.biblio_id,
                    status=result.status,
                    copies_total=result.copies_total,
                    copies_available=result.copies_available,
                    copies_reference_only=result.copies_reference_only,
                    library=result.library,
                    call_number=result.call_number,
                    due_date=result.due_date,
                )
            )

        return CheckAvailabilityResult(records=records)

    return mcp
