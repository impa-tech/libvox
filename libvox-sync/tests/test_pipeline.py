"""Milestone 3: the load stage, generations, and index-state transitions.

Real PostgreSQL, never SQLite: what is under test here is row-level security,
a generated tsvector column, CHECK constraints and COPY. None of those exist
under SQLite, so every assertion would pass while proving nothing.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from conftest import as_test_database
from libvox_driver_koha import BiblioRecord, Page

from libvox_sync.config import Settings
from libvox_sync.db import apply_migrations, tenant_scope
from libvox_sync.pipeline import (
    EmptyExtraction,
    SyncAlreadyRunning,
    forget_tenant,
    full_import,
)

# The test database, never the development one: everything below truncates, and
# rebuilding a real library's index costs half an hour against its own server.
_DEFAULTS = Settings()
ADMIN_DSN = as_test_database(os.environ.get("LIBVOX_DB_ADMIN_DSN", _DEFAULTS.admin_dsn))
APP_DSN = as_test_database(os.environ.get("LIBVOX_DB_DSN", _DEFAULTS.dsn))

TENANT_A = "lib-furg"
TENANT_B = "lib-ibict"


def _reachable(dsn: str) -> bool:
    try:
        with psycopg.connect(dsn, connect_timeout=3):
            return True
    except psycopg.Error:
        return False


pytestmark = pytest.mark.skipif(
    not _reachable(ADMIN_DSN),
    reason="no PostgreSQL at LIBVOX_DB_ADMIN_DSN; run `docker compose up -d postgres`",
)


# --- fixtures --------------------------------------------------------------


@pytest.fixture(scope="session")
def migrated() -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        apply_migrations(conn)


@pytest.fixture
def app_conn(migrated: None):
    """The unprivileged role, with no transaction left open.

    The commit matters: psycopg opens one on the first statement, and a
    connection handed over mid-transaction turns every `tenant_scope` into a
    savepoint. Nothing would be durable, and the pipeline's whole batching
    story rests on each batch actually committing.
    """
    with psycopg.connect(APP_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute("SET statement_timeout = 15000")
        conn.commit()
        yield conn


@pytest.fixture(autouse=True)
def clean(migrated: None):
    with psycopg.connect(ADMIN_DSN) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "TRUNCATE discovery_biblio, discovery_tenant, discovery_vocabulary"
            )
        conn.commit()


# --- a driver we can steer -------------------------------------------------


class ListDriver:
    """Serves a fixed list of records, paginated like a real driver.

    Same end-of-data contract as Koha: a short page ends the walk, and an
    exactly-full final page costs one more request that comes back empty.
    """

    name = "list"

    def __init__(self, records, *, page_size: int = 100, skipped: int = 0, fail_on: int | None = None):
        self._records = list(records)
        self._page_size = page_size
        self._skipped = skipped
        self._fail_on = fail_on
        self.pages_served = 0

    def fetch_page(self, cursor: str | None = None) -> Page:
        offset = int(cursor) if cursor else 0
        self.pages_served += 1
        if self._fail_on is not None and self.pages_served > self._fail_on:
            raise RuntimeError("the library's server stopped answering")

        rows = self._records[offset : offset + self._page_size]
        nxt = str(offset + self._page_size) if len(rows) == self._page_size else None
        return Page(records=rows, next_cursor=nxt, skipped=self._skipped if offset == 0 else 0)


def book(biblio_id: int, title: str, author: str | None = None) -> BiblioRecord:
    return BiblioRecord(biblio_id=biblio_id, title=title, author=author)


def state(conn, tenant_id: str) -> tuple:
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT active_generation, index_status, last_error "
            "FROM discovery_tenant WHERE tenant_id = %s",
            (tenant_id,),
        )
        return cur.fetchone()


def import_started(conn, tenant_id: str):
    """What discovery_tenant says about an import being under way, or None."""
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT import_started_at FROM discovery_tenant WHERE tenant_id = %s",
            (tenant_id,),
        )
        row = cur.fetchone()
        return row[0] if row else None


def visible(conn, tenant_id: str) -> list[str]:
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute("SELECT title FROM discovery_active ORDER BY biblio_id")
        return [row[0] for row in cur.fetchall()]


# --- the happy path --------------------------------------------------------


def test_a_full_import_makes_records_searchable(app_conn) -> None:
    """The point of the whole module: extraction and the index finally meet."""
    driver = ListDriver([book(1, "O jogo da amarelinha"), book(2, "Grande sertão")])
    result = full_import(app_conn, TENANT_A, driver)

    assert result.written == 2
    assert result.generation == 1
    assert visible(app_conn, TENANT_A) == ["O jogo da amarelinha", "Grande sertão"]

    # The generated tsvector is populated by the write, so a plural query
    # reaches a singular title without the pipeline doing anything about it.
    with tenant_scope(app_conn, TENANT_A) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT title FROM discovery_active "
            " WHERE search @@ plainto_tsquery('portuguese', %s)",
            ("amarelinhas",),
        )
        assert cur.fetchall() == [("O jogo da amarelinha",)]


def test_a_first_import_leaves_the_tenant_ready(app_conn) -> None:
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Um livro")]))
    generation, status, error = state(app_conn, TENANT_A)
    assert (generation, status, error) == (1, "ready", None)


def test_skipped_rows_are_reported_not_swallowed(app_conn) -> None:
    """A silent shortfall in the index is found months later, by a patron."""
    driver = ListDriver([book(1, "Um livro")], skipped=7)
    assert full_import(app_conn, TENANT_A, driver).skipped == 7


def test_a_batch_smaller_than_the_catalog_still_writes_everything(app_conn) -> None:
    driver = ListDriver([book(i, f"Livro {i}") for i in range(1, 26)], page_size=10)
    result = full_import(app_conn, TENANT_A, driver, batch_size=4)
    assert result.written == 25
    assert len(visible(app_conn, TENANT_A)) == 25


# --- generations -----------------------------------------------------------


def test_a_second_import_supersedes_the_first(app_conn) -> None:
    """A record the library deleted is gone, without asking the ILS what it deleted."""
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Fica"), book(2, "Sai")]))
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Fica")]))

    assert visible(app_conn, TENANT_A) == ["Fica"]
    generation, status, _ = state(app_conn, TENANT_A)
    assert (generation, status) == (2, "ready")


def test_the_old_generation_is_dropped_after_the_swap(app_conn) -> None:
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Um")]))
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Um")]))

    with tenant_scope(app_conn, TENANT_A) as scoped, scoped.cursor() as cur:
        cur.execute("SELECT DISTINCT generation FROM discovery_biblio")
        assert cur.fetchall() == [(2,)]


def test_an_orphan_generation_is_cleaned_up_by_the_next_run(app_conn) -> None:
    """A crashed import leaves rows nobody will ever publish."""
    with tenant_scope(app_conn, TENANT_A) as scoped, scoped.cursor() as cur:
        cur.execute("INSERT INTO discovery_tenant (tenant_id) VALUES (%s)", (TENANT_A,))
        cur.execute(
            "INSERT INTO discovery_biblio (tenant_id, generation, biblio_id, title) "
            "VALUES (%s, 9, 1, 'Metade de uma importação')",
            (TENANT_A,),
        )

    full_import(app_conn, TENANT_A, ListDriver([book(1, "Completa")]))

    with tenant_scope(app_conn, TENANT_A) as scoped, scoped.cursor() as cur:
        cur.execute("SELECT DISTINCT generation FROM discovery_biblio")
        assert cur.fetchall() == [(1,)]


# --- failure ---------------------------------------------------------------


def test_a_failed_first_import_leaves_the_tenant_building(app_conn) -> None:
    """Nothing to serve, so `building` is the truth and `stale` would not be."""
    driver = ListDriver([book(i, f"L{i}") for i in range(1, 40)], page_size=10, fail_on=1)
    with pytest.raises(RuntimeError):
        full_import(app_conn, TENANT_A, driver, batch_size=2)

    generation, status, error = state(app_conn, TENANT_A)
    assert generation is None
    assert status == "building"
    assert "stopped answering" in error


def test_a_failed_re_import_leaves_the_tenant_stale_not_building(app_conn) -> None:
    """The live generation still answers, so calling it `building` would be a lie.

    `libvox_mcp.models.IndexStatus` is how the model decides whether an empty
    result means "no matches" or "not indexed yet". Marking a working index
    `building` would have MARC.IA apologize for an index that works.
    """
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Serve bem")]))

    driver = ListDriver([book(i, f"L{i}") for i in range(1, 40)], page_size=10, fail_on=1)
    with pytest.raises(RuntimeError):
        full_import(app_conn, TENANT_A, driver, batch_size=2)

    generation, status, error = state(app_conn, TENANT_A)
    assert (generation, status) == (1, "stale")
    assert "stopped answering" in error
    assert visible(app_conn, TENANT_A) == ["Serve bem"]


def test_an_empty_extraction_does_not_become_a_ready_empty_index(app_conn) -> None:
    """Zero records is a revoked permission far more often than an empty library."""
    with pytest.raises(EmptyExtraction):
        full_import(app_conn, TENANT_A, ListDriver([]))

    generation, status, error = state(app_conn, TENANT_A)
    assert generation is None
    assert status == "building"
    assert "catalogue" in error


def test_an_empty_catalog_can_be_published_on_purpose(app_conn) -> None:
    result = full_import(app_conn, TENANT_A, ListDriver([]), allow_empty=True)
    assert result.written == 0
    assert state(app_conn, TENANT_A)[1] == "ready"


def test_a_failed_import_does_not_publish_a_partial_generation(app_conn) -> None:
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Serve bem")]))

    driver = ListDriver([book(i, f"L{i}") for i in range(1, 40)], page_size=10, fail_on=1)
    with pytest.raises(RuntimeError):
        full_import(app_conn, TENANT_A, driver, batch_size=2)

    assert visible(app_conn, TENANT_A) == ["Serve bem"]


def test_a_record_seen_twice_does_not_kill_the_import(app_conn) -> None:
    """Koha paginates by offset, so a catalogue edited mid-walk repeats rows.

    Hour three of an import dying on a primary key violation, because a
    cataloguer was doing their job, is not an acceptable failure mode. The
    second sighting carries no new information, so it is dropped — and the
    count reports what search will actually find, not how many rows were
    offered.
    """
    twice = [book(1, "Um"), book(2, "Dois"), book(2, "Dois"), book(3, "Três")]
    result = full_import(app_conn, TENANT_A, ListDriver(twice), batch_size=2)

    assert result.written == 3
    assert visible(app_conn, TENANT_A) == ["Um", "Dois", "Três"]


def test_there_is_no_way_to_import_only_part_of_a_catalog(app_conn) -> None:
    """A truncated generation published as `ready` is a confident wrong answer.

    It tells the model the index is current while it holds the first few
    hundred records, and the patron hears "we do not have that". `extract
    --limit` is for looking at data; nothing here imports a fraction.
    """
    import inspect

    assert "limit" not in inspect.signature(full_import).parameters


# --- isolation -------------------------------------------------------------


def test_a_full_import_cannot_write_into_another_tenants_generation(app_conn) -> None:
    """The WITH CHECK clause refuses a row belonging to anyone else.

    Asserted rather than assumed. This test is also why the pipeline does not
    use COPY: writing it first turned up `COPY FROM not supported with
    row-level security`, which PostgreSQL refuses outright rather than silently
    bypassing the policy. Good refusal, and one worth having found here.
    """
    from libvox_sync.pipeline import _write

    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        _write(
            app_conn,
            TENANT_A,
            [(TENANT_B, 1, 1, "Contrabando") + (None,) * 9],
        )


def test_one_librarys_import_is_invisible_to_another(app_conn) -> None:
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Da FURG")]))
    full_import(app_conn, TENANT_B, ListDriver([book(1, "Do IBICT")]))

    assert visible(app_conn, TENANT_A) == ["Da FURG"]
    assert visible(app_conn, TENANT_B) == ["Do IBICT"]


# --- concurrency -----------------------------------------------------------


def test_a_second_import_of_the_same_library_refuses_while_one_runs(app_conn) -> None:
    """Two workers writing the same generation number is a primary-key crash.

    The lock is session-level, so it spans the many transactions one import
    makes rather than being released after the first batch.
    """
    with psycopg.connect(APP_DSN) as other:
        with other.cursor() as cur:
            cur.execute("SET statement_timeout = 15000")
        other.commit()

        full_import(app_conn, TENANT_A, ListDriver([book(1, "Primeira")]))

        from libvox_sync.pipeline import _lock, _unlock

        assert _lock(other, TENANT_A)
        try:
            with pytest.raises(SyncAlreadyRunning):
                full_import(app_conn, TENANT_A, ListDriver([book(2, "Segunda")]))
        finally:
            _unlock(other, TENANT_A)


def test_the_lock_is_released_after_a_failed_import(app_conn) -> None:
    """A failure that kept the lock would need an operator to clear it by hand."""
    driver = ListDriver([book(i, f"L{i}") for i in range(1, 40)], page_size=10, fail_on=1)
    with pytest.raises(RuntimeError):
        full_import(app_conn, TENANT_A, driver, batch_size=2)

    result = full_import(app_conn, TENANT_A, ListDriver([book(1, "Depois")]))
    assert result.written == 1


# --- an import nobody can see yet -------------------------------------------


def test_an_import_says_it_is_running_before_it_writes_anything(app_conn) -> None:
    """The gap the import screen fell into.

    The request is stamped consumed the moment the worker claims it, and the
    first record is written a page later. In between there was nothing at all
    to see, and the panel -- which reads this over a second connection, from
    another database -- concluded the previous import was simply finished.

    Asked from inside the first fetch, which is exactly where the gap is.
    """
    seen: list[object] = []

    class Watching(ListDriver):
        def fetch_page(self, cursor: str | None = None):
            with psycopg.connect(APP_DSN) as other:
                seen.append(import_started(other, TENANT_A))
            return super().fetch_page(cursor)

    full_import(app_conn, TENANT_A, Watching([book(1, "Primeira")]))

    assert seen and seen[0] is not None


def test_a_published_import_stops_saying_it_is_running(app_conn) -> None:
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Primeira")]))

    assert import_started(app_conn, TENANT_A) is None


def test_a_failed_import_stops_saying_it_is_running(app_conn) -> None:
    """Otherwise the screen watches an import that died, forever."""
    driver = ListDriver([book(i, f"L{i}") for i in range(1, 40)], page_size=10, fail_on=1)
    with pytest.raises(RuntimeError):
        full_import(app_conn, TENANT_A, driver, batch_size=2)

    assert import_started(app_conn, TENANT_A) is None


def test_a_refused_import_does_not_clear_the_running_one(app_conn) -> None:
    """The stamp belongs to whoever holds the lock.

    A second press arriving mid-import must not report that nothing is running:
    something is, and it is the import the first press started.
    """
    with psycopg.connect(APP_DSN) as other:
        with other.cursor() as cur:
            cur.execute("SET statement_timeout = 15000")
        other.commit()

        full_import(app_conn, TENANT_A, ListDriver([book(1, "Primeira")]))

        from libvox_sync.pipeline import _lock, _mark_importing, _unlock

        assert _lock(other, TENANT_A)
        try:
            _mark_importing(other, TENANT_A)
            with pytest.raises(SyncAlreadyRunning):
                full_import(app_conn, TENANT_A, ListDriver([book(2, "Segunda")]))

            assert import_started(app_conn, TENANT_A) is not None
        finally:
            _unlock(other, TENANT_A)


# --- forgetting a library ---------------------------------------------------


def test_a_library_that_was_deleted_loses_its_index(app_conn) -> None:
    """The gap that left 1,745 records behind: two libraries were deleted in
    the portal and the Discovery Index never heard."""
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Dom Casmurro")]))
    assert visible(app_conn, TENANT_A) == ["Dom Casmurro"]

    removed = forget_tenant(app_conn, TENANT_A)

    assert removed == 1
    assert visible(app_conn, TENANT_A) == []
    assert state(app_conn, TENANT_A) is None, "and no state row claiming an index"


def test_forgetting_one_library_does_not_touch_another(app_conn) -> None:
    """The tenant name arrives from an event, so what stops a wrong one from
    reaching another library's catalogue matters. It is the same policy that
    stops a reader: the DELETE runs inside the tenant's own scope."""
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Dom Casmurro")]))
    full_import(app_conn, TENANT_B, ListDriver([book(2, "Grande sertão")]))

    forget_tenant(app_conn, TENANT_A)

    assert visible(app_conn, TENANT_B) == ["Grande sertão"]


def test_forgetting_a_library_that_was_never_indexed_is_quiet(app_conn) -> None:
    """A consumer replaying an old event needs this to be nothing, not an
    error."""
    assert forget_tenant(app_conn, "lib-nunca-indexada") == 0


def test_a_forgotten_library_is_rebuilt_from_scratch(app_conn) -> None:
    """Deleted and recreated is a new library that happens to share a name.

    If the old generation survived, the next import would number itself above
    it and the swap would leave the previous catalogue's records live under a
    library that never had them.
    """
    full_import(app_conn, TENANT_A, ListDriver([book(1, "Antigo")]))
    forget_tenant(app_conn, TENANT_A)

    full_import(app_conn, TENANT_A, ListDriver([book(2, "Novo")]))

    assert visible(app_conn, TENANT_A) == ["Novo"]
    assert state(app_conn, TENANT_A)[0] == 1, "generation numbering starts over"
