"""The load stage: extracted records into the Discovery Index.

This is the half the schema has been waiting for. Migration 001 has carried
generations, a weighted Portuguese full-text vector and row-level isolation
since the beginning, and nothing wrote to it — `extract` normalizes and prints.

A full import writes a **new generation** and swaps to it in one row. That is
what makes deletions correct without asking the ILS what it deleted: a record
the library removed simply never appears in the new generation, and the swap
publishes the whole set at once. Search never observes a mixed state, because
`discovery_active` joins on `discovery_tenant.active_generation` and that is a
single value.

The pipeline takes an already-resolved job — a tenant and a driver — and does
not know where either came from. When a broker exists, the job arrives from an
event instead of from argv, and nothing in this module changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg

from .db import tenant_scope
from .embedding import Embedder, text_of

logger = logging.getLogger(__name__)

# Columns written by hand. `search` is GENERATED ALWAYS and cannot be supplied;
# `synced_at` has a default. Naming them explicitly means a column added to the
# table later does not silently change the shape of the insert.
_COLUMNS = (
    "tenant_id",
    "generation",
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
)

DEFAULT_BATCH_SIZE = 25
"""How many records to write before committing, and therefore how often the
progress screen can move.

Each batch commits on its own -- safe because the generation is not live yet --
so this is the granularity at which an import becomes visible to anyone
watching. At 500 a 436-record library showed nothing at all until it was
finished, because the batch never filled; at 25 it moves fifty times.

**It has nothing to do with the library's server.** Pages are fetched at
`page_size` and this only decides when what was fetched is written, so lowering
it costs a few more small transactions in our own Postgres and not one extra
request to anyone else."""

# Enough to identify a failure, not enough to paste a catalog into a column
# that an operator reads on one line.
_MAX_ERROR_CHARS = 500


class SyncAlreadyRunning(RuntimeError):
    """Another import holds this library's lock."""


class EmptyExtraction(RuntimeError):
    """The ILS answered, and answered with nothing."""


@dataclass(frozen=True, slots=True)
class ImportResult:
    tenant_id: str
    generation: int
    written: int
    skipped: int
    pages: int


def full_import(
    conn: psycopg.Connection,
    tenant_id: str,
    driver,
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    allow_empty: bool = False,
    embedder: Embedder | None = None,
) -> ImportResult:
    """Import a library's whole catalog into a fresh generation, then publish it.

    `conn` is the Discovery Index, as the unprivileged role. `driver` is any
    object with `fetch_page(cursor)` — this module never learns which ILS it is
    talking to.

    There is deliberately no `limit`. A partial catalog published as `ready`
    tells the model the index is current when it holds the first few hundred
    records of a library — and the patron gets "we do not have that", stated
    with confidence. `extract --limit` exists for looking at data; nothing here
    exists for importing a fraction of one.
    """
    if not _lock(conn, tenant_id):
        raise SyncAlreadyRunning(
            f"another import is already running for {tenant_id!r}"
        )

    try:
        previous = _register(conn, tenant_id)
        _mark_importing(conn, tenant_id)
        _drop_inactive_generations(conn, tenant_id, keep=previous)
        generation = _next_generation(conn, tenant_id)

        logger.info(
            "%s: importing into generation %d (active is %s)",
            tenant_id,
            generation,
            previous if previous is not None else "none",
        )

        try:
            written, skipped, pages = _load(
                conn, tenant_id, generation, driver, batch_size
            )
        except Exception as exc:
            _record_failure(conn, tenant_id, previous, exc)
            raise

        if written == 0 and not allow_empty:
            # A library with a catalog of zero is almost always a credential
            # without the `catalogue` flag, not a library without books.
            # Publishing it would have MARC.IA tell patrons the library holds
            # nothing, confidently, which is worse than an error nobody sees.
            failure = EmptyExtraction(
                f"{driver.name} returned no usable records for {tenant_id!r}; "
                f"{skipped} row(s) were skipped. Refusing to publish an empty "
                "index — check that the API user has Koha's `catalogue` flag "
                "and a non-null userid. Pass --allow-empty if the catalog is "
                "genuinely empty."
            )
            _record_failure(conn, tenant_id, previous, failure)
            raise failure

        _build_vocabulary(conn, tenant_id, generation)
        _embed(conn, tenant_id, generation, previous, embedder)
        _publish(conn, tenant_id, generation)
        logger.info(
            "%s: generation %d live — %d record(s), %d skipped, %d page(s)",
            tenant_id,
            generation,
            written,
            skipped,
            pages,
        )
        return ImportResult(tenant_id, generation, written, skipped, pages)
    finally:
        _mark_idle(conn, tenant_id)
        _unlock(conn, tenant_id)


# --- the lock --------------------------------------------------------------


def _lock_key(tenant_id: str) -> str:
    return f"libvox_sync:full_import:{tenant_id}"


def _lock(conn: psycopg.Connection, tenant_id: str) -> bool:
    """Take a session-level advisory lock for this library.

    Session-level rather than transaction-level on purpose: the import spans
    many transactions, and a transaction-scoped lock would be released after
    the first batch. It also survives a rollback, so a failed batch does not
    invite a second worker in halfway through.

    `hashtext` can collide, and two colliding libraries would serialize against
    each other needlessly. That is a scheduling inefficiency, not a
    correctness problem — the generation each writes is still its own.
    """
    with conn.cursor() as cur:
        cur.execute("SELECT pg_try_advisory_lock(hashtext(%s))", (_lock_key(tenant_id),))
        taken = bool(cur.fetchone()[0])
    conn.commit()
    return taken


def _unlock(conn: psycopg.Connection, tenant_id: str) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_unlock(hashtext(%s))", (_lock_key(tenant_id),))
        conn.commit()
    except psycopg.Error:  # pragma: no cover - the session is going away anyway
        logger.debug("could not release the advisory lock for %s", tenant_id)


# --- index state -----------------------------------------------------------


def _register(conn: psycopg.Connection, tenant_id: str) -> int | None:
    """Make sure the tenant has an index-state row, and read the live generation.

    Inside a scope because `discovery_tenant` carries the same policy as
    everything else: an INSERT for a tenant other than the bound one is refused
    by WITH CHECK rather than quietly written.
    """
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "INSERT INTO discovery_tenant (tenant_id) VALUES (%s) "
            "ON CONFLICT (tenant_id) DO NOTHING",
            (tenant_id,),
        )
        cur.execute(
            "SELECT active_generation FROM discovery_tenant WHERE tenant_id = %s",
            (tenant_id,),
        )
        return cur.fetchone()[0]


def _mark_importing(conn: psycopg.Connection, tenant_id: str) -> None:
    """Say, in the index itself, that an import is running.

    The panel cannot see the advisory lock -- it lives in another database --
    and until the first batch lands there is nothing else to see: the request
    was claimed the moment it was made, so it is no longer pending, and no row
    of the new generation exists yet. For those seconds the import screen showed
    the previous import, finished, and stopped refreshing. Migration 007 has the
    whole account.

    Its own transaction, and committed before the load begins, or a watcher
    would not see it until the thing it announces is over.
    """
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "UPDATE discovery_tenant SET import_started_at = now() "
            " WHERE tenant_id = %s",
            (tenant_id,),
        )


def _mark_idle(conn: psycopg.Connection, tenant_id: str) -> None:
    """No import is running any more, whatever the reason.

    In `finally` beside the unlock, so the column brackets exactly what the lock
    brackets: published, failed, or raised on the way out, the answer to "is one
    running" is the same and it is no.

    Swallowed like _record_failure's write, and for the same reason -- when the
    database is what failed, the exception worth propagating is the original
    one.
    """
    try:
        with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
            cur.execute(
                "UPDATE discovery_tenant SET import_started_at = NULL "
                " WHERE tenant_id = %s",
                (tenant_id,),
            )
    except psycopg.Error:  # pragma: no cover - the database is the thing failing
        logger.exception("%s: could not clear the import stamp", tenant_id)


def _drop_inactive_generations(
    conn: psycopg.Connection, tenant_id: str, *, keep: int | None
) -> None:
    """Delete every generation that is not the live one.

    A crashed import leaves a generation nobody will ever activate. It is
    invisible to search — that is the point of generations — but it is not
    free, so the next run clears it before writing its own.
    """
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        if keep is None:
            cur.execute("DELETE FROM discovery_biblio")
            dropped = cur.rowcount
            cur.execute("DELETE FROM discovery_vocabulary")
        else:
            cur.execute("DELETE FROM discovery_biblio WHERE generation <> %s", (keep,))
            dropped = cur.rowcount
            cur.execute(
                "DELETE FROM discovery_vocabulary WHERE generation <> %s", (keep,)
            )
        if dropped:
            logger.info(
                "%s: cleared %d row(s) from a generation that was never published",
                tenant_id,
                dropped,
            )


def forget_tenant(conn: psycopg.Connection, tenant_id: str) -> int:
    """Drop everything the index holds for one library. Returns rows removed.

    For a library that no longer exists in the registry. The root README says
    the deletion event *"is what tells a consumer to drop that tenant's
    index"*, and until now nothing consumed it: two libraries deleted in the
    portal left 1,745 records behind.

    **Inside the tenant's own scope**, which is what makes this safe to call
    with a name that came from an event: the policy on `discovery_biblio`
    allows a DELETE only for the bound tenant, so a wrong name deletes nothing
    rather than someone else's catalogue. The isolation that stops a reader
    seeing another library is the same rule stopping this from over-reaching.

    Not idempotent-by-accident either: a library that was never indexed is zero
    rows and no error, which is what a consumer replaying an old event needs.
    """
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute("DELETE FROM discovery_biblio")
        records = cur.rowcount or 0
        cur.execute("DELETE FROM discovery_vocabulary")
        cur.execute("DELETE FROM discovery_tenant")
        state = cur.rowcount or 0

    if records or state:
        logger.info(
            "%s: forgotten — %d record(s) dropped from the index",
            tenant_id,
            records,
        )

    return records


def _next_generation(conn: psycopg.Connection, tenant_id: str) -> int:
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT COALESCE(MAX(generation), 0) + 1 FROM discovery_biblio "
            "WHERE tenant_id = %s",
            (tenant_id,),
        )
        return int(cur.fetchone()[0])


def _build_vocabulary(
    conn: psycopg.Connection, tenant_id: str, generation: int
) -> int:
    """Collect every word this library catalogued, for repairing typos.

    Built here, before `_publish`, for the reason `_load` already commits batch
    by batch: the generation is not live yet, so a half-built vocabulary is
    invisible. It is also why this is not incremental — the whole generation is
    read once, in one statement, and the previous one is dropped by the same
    delete that drops its records.

    `word` is folded and unaccented because that is what a typo is compared
    against; `display` keeps the library's own spelling so an answer can say
    "I searched for memórias" rather than "memorias". Words shorter than four
    letters are dropped: nothing under five is ever repaired, and a candidate
    that can never be chosen is a row that costs an index entry for nothing.
    """
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        # A no-op on the import path, where the generation is new. It is here so
        # the function can simply be run again -- rebuilding the word list of a
        # generation that already has one is a repair, not a conflict.
        cur.execute(
            "DELETE FROM discovery_vocabulary WHERE generation = %s", (generation,)
        )
        cur.execute(
            "INSERT INTO discovery_vocabulary "
            "            (tenant_id, generation, word, display, ndoc) "
            "SELECT %s, %s, "
            "       unaccent(lower(token)), "
            "       mode() WITHIN GROUP (ORDER BY token), "
            "       count(DISTINCT b.biblio_id) "
            "  FROM discovery_biblio b, "
            "       LATERAL ts_parse('default', lower(concat_ws(' ', "
            "           b.title, b.series, b.author, b.publisher, "
            "           b.abstract, b.notes))) "
            # 1 and 2 are whole words, ASCII or not; 10 and 11 the parts of a
            # hyphenated one, which is how "anglo-saxon" reaches the vocabulary
            # as two words a reader might type on their own.
            " WHERE b.tenant_id = %s AND b.generation = %s "
            "   AND tokid IN (1, 2, 10, 11) "
            "   AND length(token) >= 4 "
            " GROUP BY unaccent(lower(token))",
            (tenant_id, generation, tenant_id, generation),
        )
        written = cur.rowcount or 0

    logger.info("%s: vocabulary of %d word(s)", tenant_id, written)
    return written


def _publish(conn: psycopg.Connection, tenant_id: str, generation: int) -> None:
    """Make the new generation live, then drop the old one.

    One row changes, so there is no window in which search sees half of each.
    The delete comes after, inside the same transaction: a reader that gets in
    between would see the new generation, which is the correct answer.
    """
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "UPDATE discovery_tenant "
            "   SET active_generation = %s, index_status = 'ready', "
            "       last_sync_at = now(), last_error = NULL "
            " WHERE tenant_id = %s",
            (generation, tenant_id),
        )
        cur.execute(
            "DELETE FROM discovery_biblio WHERE generation <> %s", (generation,)
        )
        cur.execute(
            "DELETE FROM discovery_vocabulary WHERE generation <> %s", (generation,)
        )


def _record_failure(
    conn: psycopg.Connection,
    tenant_id: str,
    previous: int | None,
    exc: BaseException,
) -> None:
    """Say what went wrong, without lying about what is being served.

    `index_status` is a contract with `libvox_mcp.models.IndexStatus`, and the
    model uses it to decide whether an empty result means "no matches" or "not
    indexed yet". So a failed re-import must NOT become `building`: the
    previous generation is still answering perfectly well, and calling it
    `building` would have MARC.IA apologize for an index that works. It becomes
    `stale` — there are records, and they are known to be behind.

    A first import that fails has nothing to serve, and `building` is already
    the truth.
    """
    message = str(exc).strip() or exc.__class__.__name__
    if len(message) > _MAX_ERROR_CHARS:
        message = message[: _MAX_ERROR_CHARS - 1] + "…"

    status = "stale" if previous is not None else "building"
    try:
        with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
            cur.execute(
                "UPDATE discovery_tenant "
                "   SET index_status = %s, last_sync_at = now(), last_error = %s "
                " WHERE tenant_id = %s",
                (status, message, tenant_id),
            )
    except psycopg.Error:  # pragma: no cover - the database is the thing failing
        logger.exception("%s: could not record the failure", tenant_id)


# --- the load itself -------------------------------------------------------


def _load(
    conn: psycopg.Connection,
    tenant_id: str,
    generation: int,
    driver,
    batch_size: int,
) -> tuple[int, int, int]:
    """Walk the driver's pages, writing batches into the new generation.

    Each batch commits on its own. That is safe precisely because the
    generation is not live yet — a half-written generation is invisible, so
    there is no state a reader could catch mid-flight. It also keeps a single
    transaction from being held open across an entire catalog.

    Pages are walked here rather than through `iter_records` because the page
    is what carries `skipped`, and a silent shortfall in the index is the kind
    of thing that gets noticed months later by a patron.
    """
    written = skipped = pages = 0
    batch: list[tuple] = []
    cursor: str | None = None

    while True:
        page = driver.fetch_page(cursor)
        pages += 1
        skipped += page.skipped

        for record in page.records:
            batch.append((tenant_id, generation, *_row(record)))
            if len(batch) >= batch_size:
                written += _write(conn, tenant_id, batch)
                batch = []

        if page.next_cursor is None:
            break
        cursor = page.next_cursor

    written += _write(conn, tenant_id, batch)
    return written, skipped, pages


def _embed(
    conn: psycopg.Connection,
    tenant_id: str,
    generation: int,
    previous: int | None,
    embedder: Embedder | None,
) -> int:
    """Give the new generation its vectors, before anyone can see it.

    Same slot and same reason as `_build_vocabulary`: the generation is not
    live yet, so a long step here delays publication rather than degrading a
    live index, and a failure leaves the previous generation serving.

    **Inherit first.** Every row whose embedded text is unchanged copies its
    vector from the generation being replaced. A daily reimport of a stable
    catalogue then asks the embedding service for nothing at all; without it,
    the 3.2 minutes measured on 8,774 records would be paid again every night,
    and a collection of 100,000 would pay 37.

    Returns how many vectors were computed, which is the number worth logging:
    it is the only one that cost anything.
    """
    if embedder is None or not embedder.configured:
        logger.info("%s: no embedding service configured, indexing without vectors",
                    tenant_id)
        return 0

    inherited = _inherit_embeddings(conn, tenant_id, generation, previous)
    computed = 0

    while True:
        pending = _rows_without_embedding(conn, tenant_id, generation)
        if not pending:
            break

        vectors = embedder.embed([text_of(row) for row in pending])
        written = [
            (_as_vector(vector), tenant_id, generation, row["biblio_id"])
            for row, vector in zip(pending, vectors)
            if vector is not None
        ]

        if not written:
            # The service is not answering. Every row it refused stays NULL and
            # is still found by the lexical arm -- asking again for the rest
            # would only be slower about the same answer.
            logger.warning(
                "%s: embedding unavailable, %d record(s) indexed without a vector",
                tenant_id,
                len(pending),
            )
            break

        _store_embeddings(conn, tenant_id, written)
        computed += len(written)

        if len(written) < len(pending):
            # Some of the batch failed. Stopping rather than looping means the
            # next pass would ask for exactly the same rows and fail the same
            # way.
            logger.warning(
                "%s: %d of %d record(s) could not be embedded",
                tenant_id,
                len(pending) - len(written),
                len(pending),
            )
            break

    logger.info(
        "%s: %d vector(s) inherited, %d computed", tenant_id, inherited, computed
    )
    return computed


def _inherit_embeddings(
    conn: psycopg.Connection,
    tenant_id: str,
    generation: int,
    previous: int | None,
) -> int:
    """Copy vectors forward for records whose embedded text did not change.

    Matched on the three fields `text_of` actually embeds, so a record whose
    publisher was corrected keeps its vector and one whose title was corrected
    does not. Matching on the whole row would re-embed a catalogue every time
    somebody fixed a shelf mark.
    """
    if previous is None:
        return 0

    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "UPDATE discovery_biblio AS fresh "
            "   SET embedding = stale.embedding "
            "  FROM discovery_biblio AS stale "
            " WHERE fresh.tenant_id = %s AND fresh.generation = %s "
            "   AND stale.tenant_id = %s AND stale.generation = %s "
            "   AND stale.biblio_id = fresh.biblio_id "
            "   AND stale.embedding IS NOT NULL "
            "   AND coalesce(stale.title,  '') = coalesce(fresh.title,  '') "
            "   AND coalesce(stale.series, '') = coalesce(fresh.series, '') "
            "   AND coalesce(stale.author, '') = coalesce(fresh.author, '')",
            (tenant_id, generation, tenant_id, previous),
        )
        return cur.rowcount


def _rows_without_embedding(
    conn: psycopg.Connection, tenant_id: str, generation: int, limit: int = 500
) -> list[dict]:
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT biblio_id, title, series, author "
            "  FROM discovery_biblio "
            " WHERE tenant_id = %s AND generation = %s AND embedding IS NULL "
            " ORDER BY biblio_id LIMIT %s",
            (tenant_id, generation, limit),
        )
        return [
            dict(zip(("biblio_id", "title", "series", "author"), row))
            for row in cur.fetchall()
        ]


def _store_embeddings(
    conn: psycopg.Connection, tenant_id: str, rows: list[tuple]
) -> None:
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.executemany(
            "UPDATE discovery_biblio SET embedding = %s::vector "
            " WHERE tenant_id = %s AND generation = %s AND biblio_id = %s",
            rows,
        )
    conn.commit()


def _as_vector(values: list[float]) -> str:
    """A pgvector literal.

    A string with an explicit `::vector` cast rather than the `pgvector`
    package's psycopg adapter: one dependency fewer, and the only thing the
    adapter would buy is this line.
    """
    return "[" + ",".join(f"{value:.7g}" for value in values) + "]"


def _row(record) -> tuple:
    return (
        record.biblio_id,
        record.title,
        record.author,
        record.isbn,
        record.publisher,
        record.copyright_date,
        record.item_type,
        record.series,
        record.abstract,
        record.notes,
        record.ils_timestamp,
    )


def _write(conn: psycopg.Connection, tenant_id: str, batch: list[tuple]) -> int:
    """Write one batch, under the tenant's scope.

    **Not COPY.** PostgreSQL refuses outright — `COPY FROM not supported with
    row-level security`, and it names INSERT in the hint. That is worth writing
    down, because COPY is the obvious reflex for bulk loading and the schema's
    isolation model rules it out permanently, not just here.

    So: `executemany`, which psycopg runs in pipeline mode — one round trip for
    the batch rather than one per row. The cost of isolation is real and it is
    small; the cost of discovering the refusal in production would not have
    been.
    """
    if not batch:
        return 0

    columns = ", ".join(_COLUMNS)
    placeholders = ", ".join(["%s"] * len(_COLUMNS))

    # ON CONFLICT DO NOTHING, and not for tidiness. Koha paginates by offset,
    # so a catalogue edited while the walk is in progress shifts rows between
    # pages and can hand the same record over twice. Without this, hour three
    # of an import dies on a primary key violation caused by a cataloguer doing
    # their job. Seeing a record twice is not new information, so the second
    # sighting is dropped rather than merged.
    statement = (
        f"INSERT INTO discovery_biblio ({columns}) VALUES ({placeholders}) "
        "ON CONFLICT (tenant_id, generation, biblio_id) DO NOTHING"
    )

    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.executemany(statement, batch)
        # Rows actually written, so a duplicate seen twice is counted once and
        # the reported total matches what search will find.
        written = cur.rowcount if cur.rowcount is not None and cur.rowcount >= 0 else len(batch)
    return written
