"""The `/libvox-mcp` server: a protocol adapter, and nothing more.

It declares the tool surface and forwards each call to `/libvox-core`, which
owns tenant resolution, the routing policy, and every credential. Keeping this
layer free of business logic is what lets it be exposed to third-party MCP
clients without putting the credential vault on the outside of the system.

Two tools, two very different execution paths — this asymmetry is the CQRS
split at the heart of LibVox:

    search_catalog      →  Core  →  Discovery Index  (never touches the ILS)
    check_availability  →  Core  →  Driver  →  ILS   (always live)

Search is the high-volume operation, so it is served entirely from the Discovery
Index — a durable PostgreSQL read model that `/libvox-sync` populates in the
background. A user searching the catalog never generates traffic against the
library's own server, which is the guarantee the whole architecture exists to
keep.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated

from mcp.server import MCPServer
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
from mcp.types import ToolAnnotations
from pydantic import AnyHttpUrl, Field

from .auth import LibVoxTokenVerifier, require_tenant
from .config import Settings
from .core_client import CoreClient
from .models import MAX_AVAILABILITY_IDS, CheckAvailabilityResult, SearchCatalogResult

logger = logging.getLogger(__name__)

MAX_RESULTS = 20

INSTRUCTIONS = """\
Tools for searching a library's catalog and checking whether a book is
currently on the shelf.

Call search_catalog first: it resolves titles, authors, series and
descriptions to record IDs. Then pass those IDs to check_availability to find
out what is actually borrowable right now. Availability is live and changes
minute to minute, so never infer it from a search result.

There are no subject headings. A patron asking for "books about the war" is
answered by what the catalog happens to say about each work, not by a
classification anyone assigned.

Always read index_status on a search result before concluding that the
library does not hold something.
"""


@dataclass(slots=True)
class AppContext:
    """Shared state for the server's lifetime."""

    core: CoreClient


def create_server(settings: Settings | None = None) -> MCPServer:
    """Build the MCP server.

    A factory rather than a module-level singleton so tests can run against an
    isolated instance, and so a host embedding LibVox can supply its own
    settings.
    """
    settings = settings or Settings.from_env()

    # EXPERIMENT (temporary): talk to one Koha directly, no Core, no tenant.
    # Delete these three lines and direct_koha.py to remove it entirely.
    if settings.backend == "koha":
        from .direct_koha import create_direct_server

        return create_direct_server(settings)

    core = CoreClient(
        settings.core_url,
        timeout=settings.core_timeout,
        service_token=settings.service_token,
    )

    @asynccontextmanager
    async def lifespan(server: MCPServer) -> AsyncIterator[AppContext]:
        logger.info("libvox-mcp starting; core=%s", settings.core_url)
        try:
            yield AppContext(core=core)
        finally:
            await core.aclose()
            logger.info("libvox-mcp stopped")

    mcp = MCPServer(
        "LibVox",
        title="LibVox — the voice of the library",
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        token_verifier=LibVoxTokenVerifier(core),
        auth=AuthSettings(
            issuer_url=AnyHttpUrl(settings.issuer_url),
            resource_server_url=AnyHttpUrl(settings.resource_server_url),
            required_scopes=list(settings.required_scopes),
        ),
    )

    # NOTE: no tool below accepts a tenant, library name, or ILS URL. Any
    # argument the model fills is attacker-controllable through the user's
    # prompt; tenant comes from the credential instead. See auth.py.

    @mcp.tool(
        title="Search the library catalog",
        annotations=ToolAnnotations(
            read_only_hint=True,
            idempotent_hint=True,
            open_world_hint=False,
        ),
    )
    async def search_catalog(
        ctx: Context[AppContext],
        query: Annotated[
            str,
            Field(
                description=(
                    "What to look for: a title, an author, a series, or the "
                    "words a catalogue would use for what the reader "
                    "described. Terms are combined with AND, so every word "
                    "you add narrows the result — a whole question finds "
                    "nothing where one well-chosen word finds shelves of "
                    "books. Prefer one or two. It is matched against what the "
                    "library catalogued, which is written in whatever "
                    "language that library uses, and that is often not the "
                    "language of the question. A misspelling is repaired by "
                    "the index against the words this library actually holds, "
                    "so it needs no fixing here. Leave it out when the whole "
                    "question is an author, a title or a year — a word "
                    "invented to fill it searches for that word."
                ),
            ),
        ] = "",
        author: Annotated[
            str | None,
            Field(description="Narrow to one author, when the user named one."),
        ] = None,
        title: Annotated[
            str | None,
            Field(description="Narrow to one title, when the user named one."),
        ] = None,
        year_from: Annotated[
            int | None,
            Field(
                description=(
                    "Earliest publication year to include. With year_to it is "
                    "a decade or a span; alone it means 'from this year on'. "
                    "For a single year, set both to it."
                )
            ),
        ] = None,
        year_to: Annotated[
            int | None,
            Field(
                description=(
                    "Latest publication year to include. Alone it means "
                    "'before this year'. The year is not searchable as text — "
                    "a query of \"1992\" finds nothing, and this is how a year "
                    "is asked for."
                )
            ),
        ] = None,
        limit: Annotated[
            int,
            Field(
                ge=1,
                description=(
                    f"How many records to return. More than {MAX_RESULTS} is "
                    f"served as {MAX_RESULTS} — read `truncated` to know there "
                    "were more, rather than asking for a bigger page."
                ),
            ),
        ] = 5,
    ) -> SearchCatalogResult:
        """Search the library's catalog for books.

        Returns bibliographic records, not availability: a record here means
        the library catalogues the book, not that a copy is on the shelf. Use
        check_availability with the returned biblio_ids for that.

        Check index_status on the result. When it is 'building', the library
        was recently added and its catalog is still being indexed — say so
        rather than reporting that nothing was found.

        Terms are matched against the title, series, author and publisher the
        library recorded, and against whatever description it holds of the
        work — in the language it catalogued in, which is often not the
        language of the question. Which of them matched is not returned and is
        never yours to state: the word may be in a summary you were never
        sent, and the title in front of you may not contain it at all.

        One search per question. An empty result is an answer: say the library
        holds nothing under that word and stop. Running the same query again
        returns the same nothing while the patron waits.

        When `truncated` is true, more records matched than were returned: say
        the library holds at least this many, never exactly this many, and
        offer to narrow the search.

        A year filter reaches only records the library catalogued with a date.
        When one is used, `undated_records` says how many matches carried no
        year at all; if it is greater than zero, say there may be more rather
        than reporting the count as the whole of it.

        `searched_for` appears when your query found nothing and a word was
        repaired against the ones this library catalogued. The records answer
        that query, not yours — name the word that was looked up, because the
        reader is the only one who can tell you the repair was wrong.
        """
        tenant_id = require_tenant()
        core = ctx.request_context.lifespan_context.core
        return await core.search_catalog(
            tenant_id=tenant_id,
            query=query,
            author=author,
            title=title,
            year_from=year_from,
            year_to=year_to,
            # Clamped, not refused. This was `le=MAX_RESULTS` on the field, and
            # a model asking for fifty got a Pydantic error instead of a
            # search: one of four rounds of the loop spent on a bounds check,
            # then a retry. Seen from qwen3.6:35b-a3b -- the model that answers
            # this system's benchmark question eight times out of eight -- so
            # the schema was not communicating the number, and a bound the best
            # available model trips over is a bound in the wrong place.
            #
            # Nothing is protected by refusing. The cap exists because twenty
            # records a model reads beat a hundred it skims, not because a
            # larger page reaches anyone's server; `truncated` already says
            # there were more. And /libvox-core's own route still carries
            # `le=20`, so a caller that skips this layer is still bounded.
            limit=min(limit, MAX_RESULTS),
        )

    @mcp.tool(
        title="Check whether copies are on the shelf",
        annotations=ToolAnnotations(
            read_only_hint=True,
            idempotent_hint=False,  # live data; the same call changes over time
            open_world_hint=True,
        ),
    )
    async def check_availability(
        biblio_ids: Annotated[
            list[int],
            Field(
                description=(
                    "Record IDs returned by search_catalog. Only use IDs that "
                    "came from a search in this conversation, and only for the "
                    "records you are actually going to name in your answer — "
                    "each one is a live request to the library's own server. "
                    f"At most {MAX_AVAILABILITY_IDS} are checked; any beyond "
                    "that are ignored."
                ),
                min_length=1,
            ),
        ],
        ctx: Context[AppContext],
    ) -> CheckAvailabilityResult:
        """Check live circulation status for specific catalog records.

        Queries the library's system directly, so the answer reflects this
        moment: how many copies exist, how many are on the shelf, where they
        are, and when a borrowed copy is due back.

        Records the library does not hold are omitted from the result rather
        than reported as unavailable.
        """
        tenant_id = require_tenant()
        core = ctx.request_context.lifespan_context.core
        return await core.check_availability(
            # Clamped, not refused -- the same reasoning as `limit` above: a
            # Pydantic error costs a round of the loop and buys a retry. What
            # differs is what the cap is for. That one protects the model's
            # attention; this one protects a library's server, so the ceiling is
            # much lower and the description says so where the model reads it.
            tenant_id=tenant_id,
            biblio_ids=biblio_ids[:MAX_AVAILABILITY_IDS],
        )

    return mcp
