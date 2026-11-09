"""Run the imports somebody asked for.

The outbox `/libvox-portal` writes has had no consumer since it was built --
158 events recorded and none read, on the development database. This is the
consumer, for one event: `SyncRequested`, which a librarian raises by pressing
a button.

**Not `TenantUpdated`.** That fires whenever any configuration changes, the
widget's colour included, and re-importing a catalogue on each one would send
hundreds of requests to a library's own server because somebody picked a new
shade of blue. Reacting to a credential change is a worthwhile thing to build
and a different decision; this consumer ignores those events and leaves them
unconsumed for whoever makes it.

**LISTEN for latency, the table for the guarantee.** `pg_notify` is delivered
on commit and dropped if nobody is connected, so a worker that was restarting
when the button was pressed would never learn of it. The poll below is what
makes that a delay rather than a loss, and it is why the interval can be
generous.
"""

from __future__ import annotations

import logging
import signal
from dataclasses import dataclass
from pathlib import Path

import psycopg
from libvox_driver_koha import DEFAULT_PAGE_SIZE

from .config import Settings
from .db import connect_discovery, connect_tenant

logger = logging.getLogger(__name__)

CHANNEL = "libvox_tenant_events"
REQUEST = "SyncRequested"
CHANGED = "TenantUpdated"

PAGE_SIZE = DEFAULT_PAGE_SIZE
"""The driver's own default, named rather than passed as None -- which is what
it would have been handed otherwise, and which it does not accept."""

DEFAULT_POLL_S = 5.0
"""Only a backstop. A press arrives through NOTIFY in milliseconds; this is
what catches the press that happened while the worker was restarting.

Kept below the window the panel waits before telling a librarian that nobody is
answering: if this were the longer of the two, that screen would say a live
worker is absent seconds before it picks the request up. One query against a
partial index, so the rate costs nothing worth measuring."""


@dataclass(frozen=True, slots=True)
class Claim:
    event_id: int
    tenant_id: str


def claim(conn: psycopg.Connection, event: str = REQUEST) -> Claim | None:
    """Take one pending event of this kind, or None.

    `FOR UPDATE SKIP LOCKED` so two workers never take the same one, and
    `consumed_at` is stamped **now** rather than after the import finishes.

    That ordering is deliberate and it is a trade. Stamping at the end would
    keep the request pending for the whole import, which reads better -- until
    a worker is killed mid-import, and then the row stays pending forever, the
    unique index refuses every new request, and the button never enables again.
    Stamping first costs one lost request when a worker dies, and the fix for
    that is pressing the button again.

    Nothing here is tenant-scoped, and `tenant_events` deliberately carries no
    policy: a consumer polls it *before* it knows which library an event is
    about, which is the thing the event tells it.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            UPDATE tenant_events
               SET consumed_at = now()
             WHERE id = (
                   SELECT id
                     FROM tenant_events
                    WHERE event = %s
                      AND consumed_at IS NULL
                    ORDER BY id
                      FOR UPDATE SKIP LOCKED
                    LIMIT 1
             )
            RETURNING id, tenant_id
            """,
            (event,),
        )
        row = cur.fetchone()

    # No commit(): `serve` runs this connection in autocommit, so the UPDATE
    # above is already durable and psycopg refuses an explicit commit outside a
    # transaction. Claiming has to be its own transaction anyway -- a claim held
    # open for the length of an import is a lock held for the length of an
    # import.
    return Claim(row[0], row[1]) if row else None


def run_claimed(settings: Settings, claimed: Claim) -> bool:
    """Import one library's catalogue. True when it worked.

    Failures are logged and swallowed on purpose: this loop serves every
    library, and one library's server being down for an hour must not stop the
    next press from being answered. What went wrong is already written to
    `discovery_tenant.last_error` by the pipeline, which is where the librarian
    reads it.
    """
    from .__main__ import _driver_from_tenant
    from .embedding import Embedder, EmbeddingSettings
    from .pipeline import full_import

    logger.info("%s: importing, asked for in event %d", claimed.tenant_id, claimed.event_id)

    driver = None
    try:
        # Inside the try, and SystemExit caught below with it: this raises
        # SystemExit when a library has no credentials or an ILS type with no
        # driver. That is right for a command and fatal for a loop -- one
        # library whose configuration was removed would stop every other
        # library's imports being answered.
        driver = _driver_from_tenant(settings, claimed.tenant_id, PAGE_SIZE)

        with (
            connect_discovery(settings) as discovery,
            Embedder(EmbeddingSettings.from_env()) as embedder,
        ):
            result = full_import(
                discovery, claimed.tenant_id, driver, embedder=embedder
            )
    except (Exception, SystemExit) as exc:
        # One line at warning, the traceback at debug -- which is what the
        # comment here always claimed and the code did not do. Two failed
        # imports were producing 180 lines of Python between them, burying the
        # only sentence anyone needs: which library, and what its server said.
        #
        # The traceback is still there for whoever wants it:
        #   LIBVOX_LOG_LEVEL=DEBUG
        logger.warning("%s: import failed — %s", claimed.tenant_id, exc)
        logger.debug("%s: the traceback", claimed.tenant_id, exc_info=True)
        return False
    finally:
        close = getattr(driver, "close", None) if driver is not None else None
        if close:
            close()

    logger.info(
        "%s: generation %d live — %d record(s), %d skipped",
        claimed.tenant_id,
        result.generation,
        result.written,
        result.skipped,
    )
    return True


def forget_deleted(settings: Settings, conn: psycopg.Connection) -> int:
    """Drop the index of every library that has left the registry.

    The root README says a deletion event is what tells a consumer to drop that
    tenant's index. Nothing consumed it, and two libraries deleted in the portal
    left 1,745 records behind.

    **The event has to say which library, because nothing else can.**
    `discovery_biblio` and `discovery_tenant` both carry a per-tenant policy and
    this process connects as the unprivileged role, so it cannot enumerate who
    is in the index at all. Scanning for orphans would need a connection that
    ignores the policy, held by a process that runs for weeks -- which is
    exactly what `prune` refused. An event names one library, and one library
    is all this needs.

    `TenantUpdated` fires on every configuration change, so most of these are
    nothing to do: the library is still there and this returns without
    touching it. What it must never do is treat one as a reason to re-import --
    somebody picking a new widget colour would become hundreds of requests to
    that library's server.
    """
    from .pipeline import forget_tenant

    forgotten = 0

    while (claimed := claim(conn, CHANGED)) is not None:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM tenants WHERE tenant_id = %s", (claimed.tenant_id,)
            )
            still_here = cur.fetchone() is not None

        if still_here:
            continue

        try:
            with connect_discovery(settings) as discovery:
                if forget_tenant(discovery, claimed.tenant_id):
                    forgotten += 1
        except Exception:
            # Logged and swallowed, like a failed import: one library's leftover
            # rows must not stop the next press being answered. The event is
            # already consumed, so this will not be retried -- the rows stay
            # unreachable until somebody deletes another library, which is a
            # cost worth paying for a loop that keeps serving.
            logger.warning(
                "%s: could not drop the index of a deleted library",
                claimed.tenant_id,
                exc_info=True,
            )

    return forgotten


def drain(settings: Settings, conn: psycopg.Connection) -> int:
    """Answer every request waiting right now. Returns how many were run."""
    done = 0
    while (claimed := claim(conn)) is not None:
        run_claimed(settings, claimed)
        done += 1
    return done


def serve(settings: Settings, *, poll_s: float = DEFAULT_POLL_S) -> int:
    """Wait for requests, and answer them, until asked to stop."""
    # Before anything is claimed. Every job this process runs decrypts a
    # library's ILS credentials, so a worker without the key can do nothing --
    # and a worker that starts anyway would consume each request and fail it,
    # which loses the request and re-disables the button for nothing.
    try:
        settings.require_crypto_key()
    except SystemExit as exc:
        # The shared message says `export LIBVOX_CRYPTO_KEY=...`, which is right
        # for the CLI and wrong inside a container: that export would go to a
        # shell which is not this process's parent, and with
        # `restart: unless-stopped` the result is a loop of the same advice.
        # Compose reads .env, so that is where it belongs.
        if Path("/.dockerenv").exists():
            raise SystemExit(
                f"{exc}\n\n"
                "Running in a container, so exporting it would not reach this "
                "process. Put it in libvox-sync/.env instead:\n"
                "  ./worker.sh    (copies the portal's key, and checks it matches)"
            ) from None
        raise

    stopping = False

    def stop(*_: object) -> None:
        nonlocal stopping
        stopping = True
        logger.info("finishing the current import, then stopping")

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    with connect_tenant(settings) as conn:
        conn.autocommit = True
        conn.execute(f"LISTEN {CHANNEL}")
        logger.info("listening on %s, polling every %.0fs", CHANNEL, poll_s)

        # Before waiting for anything: a press that happened while this was
        # down is sitting in the table, and nobody is going to notify us of it
        # again. The same is true of a library deleted while it was down.
        forget_deleted(settings, conn)
        drain(settings, conn)

        while not stopping:
            woken = False
            for _ in conn.notifies(timeout=poll_s, stop_after=1):
                woken = True

            if stopping:
                break

            # Either way -- notified or timed out -- the table is what is read.
            # A notification only decides how long the wait was.
            forget_deleted(settings, conn)

            if drain(settings, conn) and not woken:
                logger.debug("found work by polling; a notification was missed")

    logger.info("stopped")
    return 0
