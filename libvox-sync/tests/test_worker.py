"""Claiming import requests off the outbox.

The worker's own logic, with no import run: what it takes, what it leaves, and
what it does when a library's configuration cannot produce a driver. Whether an
import works is `test_pipeline.py`'s question.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from conftest import as_test_database

from libvox_sync.config import Settings
from libvox_sync.worker import (
    CHANGED,
    REQUEST,
    claim,
    drain,
    forget_deleted,
    run_claimed,
)

_DEFAULTS = Settings()
ADMIN_DSN = as_test_database(
    os.environ.get("LIBVOX_TENANT_DB_ADMIN_DSN", _DEFAULTS.tenant_admin_dsn)
)

TENANT_A = "lib-furg"
TENANT_B = "lib-ibict"


def _has_outbox(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=3) as conn, conn.cursor() as cur:
            cur.execute("SELECT to_regclass('tenant_events') IS NOT NULL")
            return bool(cur.fetchone()[0])
    except psycopg.Error:
        return False


outbox_up = pytest.mark.skipif(
    not _has_outbox(ADMIN_DSN),
    reason="no Tenant DB with tenant_events; run /libvox-portal's migrations",
)

pytestmark = outbox_up


@pytest.fixture
def conn():
    with psycopg.connect(ADMIN_DSN) as connection:
        connection.execute("DELETE FROM tenant_events")
        connection.commit()
        yield connection
        connection.execute("DELETE FROM tenant_events")
        connection.commit()


def register(conn: psycopg.Connection, tenant_id: str) -> None:
    conn.execute(
        "INSERT INTO tenants (tenant_id, display_name) VALUES (%s, %s) "
        "ON CONFLICT (tenant_id) DO NOTHING",
        (tenant_id, tenant_id),
    )
    conn.commit()


def ask(conn: psycopg.Connection, tenant_id: str, event: str = REQUEST) -> None:
    conn.execute(
        "INSERT INTO tenant_events (tenant_id, event) VALUES (%s, %s)",
        (tenant_id, event),
    )
    conn.commit()


def pending(conn: psycopg.Connection) -> list[str]:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT tenant_id FROM tenant_events "
            " WHERE event = %s AND consumed_at IS NULL ORDER BY id",
            (REQUEST,),
        )
        return [row[0] for row in cur.fetchall()]


def test_a_request_is_claimed_once(conn) -> None:
    ask(conn, TENANT_A)

    first = claim(conn)
    second = claim(conn)

    assert first is not None
    assert first.tenant_id == TENANT_A
    assert second is None, "a claimed request must not be handed out again"


def test_claiming_marks_it_before_the_import_runs(conn) -> None:
    """The trade in `claim`: a worker killed mid-import loses one request,
    rather than leaving it pending forever and disabling the button for good."""
    ask(conn, TENANT_A)
    claim(conn)

    assert pending(conn) == []


def test_a_tenant_updated_event_is_left_alone(conn) -> None:
    """It fires on every configuration change, the widget's colour included.
    Re-importing a catalogue for one would be hundreds of requests to a
    library's server because somebody picked a new shade of blue."""
    ask(conn, TENANT_A, event="TenantUpdated")

    assert claim(conn) is None

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM tenant_events WHERE consumed_at IS NULL")
        assert cur.fetchone()[0] == 1, "it must stay for whoever consumes it"


def test_requests_are_claimed_oldest_first(conn) -> None:
    ask(conn, TENANT_A)
    ask(conn, TENANT_B)

    assert [claim(conn).tenant_id, claim(conn).tenant_id] == [TENANT_A, TENANT_B]


def test_nothing_pending_claims_nothing(conn) -> None:
    assert claim(conn) is None


def test_a_library_with_no_credentials_does_not_stop_the_loop(conn) -> None:
    """`_driver_from_tenant` raises SystemExit for a library with no ILS, which
    is right for a command and fatal for a loop: one library whose
    configuration was removed would stop every other library being served."""
    ask(conn, "lib-sem-credencial")
    ask(conn, TENANT_B)

    done = drain(Settings(crypto_key="test-key-not-for-production"), conn)

    assert done == 2, "both were attempted"
    assert pending(conn) == [], "and neither was left pending"


def test_a_failed_import_still_consumes_its_request(conn) -> None:
    """Otherwise the button never enables again, and the librarian who fixed
    her credentials cannot try."""
    ask(conn, "lib-sem-credencial")
    claimed = claim(conn)

    assert run_claimed(Settings(crypto_key="k"), claimed) is False
    assert pending(conn) == []


# --- forgetting a library that has left the registry ------------------------


def test_a_library_still_in_the_registry_keeps_its_index(conn) -> None:
    """`TenantUpdated` fires on every configuration change, so most of these
    are nothing to do. Treating one as a reason to touch the index would make
    somebody picking a widget colour into an index operation."""
    register(conn, TENANT_A)
    ask(conn, TENANT_A, event=CHANGED)

    forgotten = forget_deleted(Settings(), conn)

    assert forgotten == 0


def test_the_event_is_consumed_either_way(conn) -> None:
    """Otherwise every poll re-reads the same 200 events forever."""
    register(conn, TENANT_A)
    ask(conn, TENANT_A, event=CHANGED)

    forget_deleted(Settings(), conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM tenant_events "
            " WHERE event = %s AND consumed_at IS NULL",
            (CHANGED,),
        )
        assert cur.fetchone()[0] == 0


def test_a_sync_request_is_left_for_the_importer(conn) -> None:
    """The two consumers share a table and must not eat each other's work."""
    ask(conn, TENANT_A, event=REQUEST)

    forget_deleted(Settings(), conn)

    with conn.cursor() as cur:
        cur.execute(
            "SELECT count(*) FROM tenant_events "
            " WHERE event = %s AND consumed_at IS NULL",
            (REQUEST,),
        )
        assert cur.fetchone()[0] == 1, "an import request is not a change event"
