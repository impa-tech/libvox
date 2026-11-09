"""Milestone 6: the read query behind `search_catalog`.

This is what a patron's search actually runs, and the reason the Discovery
Index exists: none of it touches the library's own server.

Real PostgreSQL. The whole subject is a weighted Portuguese `tsvector`, a
generated column and row-level security, none of which exist under SQLite.
"""

from __future__ import annotations

import os

import psycopg
import pytest
from conftest import as_test_database

from libvox_sync.config import Settings
from libvox_sync.db import apply_migrations, tenant_scope
from libvox_sync.search import (
    DEFAULT_LIMIT,
    MAX_LIMIT,
    EmptySearch,
    index_status,
    search,
)

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


@pytest.fixture(scope="session")
def migrated() -> None:
    with psycopg.connect(ADMIN_DSN) as conn:
        apply_migrations(conn)


@pytest.fixture
def app_conn(migrated: None):
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


def index(conn, tenant: str, rows: list[tuple], *, status: str = "ready") -> None:
    """Put records in the index directly, without running an import."""
    with tenant_scope(conn, tenant) as scoped, scoped.cursor() as cur:
        cur.execute(
            "INSERT INTO discovery_tenant (tenant_id, active_generation, index_status) "
            "VALUES (%s, 1, %s) ON CONFLICT (tenant_id) DO UPDATE "
            "SET active_generation = 1, index_status = EXCLUDED.index_status",
            (tenant, status),
        )
        cur.executemany(
            "INSERT INTO discovery_biblio "
            "  (tenant_id, generation, biblio_id, title, author, publisher) "
            "VALUES (%s, 1, %s, %s, %s, %s)",
            [(tenant, *row) for row in rows],
        )


ACERVO = [
    (1, "Memórias póstumas de Brás Cubas", "Machado de Assis", "Garnier"),
    (2, "Dom Casmurro", "Machado de Assis", "Garnier"),
    (3, "Grande sertão: veredas", "João Guimarães Rosa", "José Olympio"),
    (4, "O cortiço", "Aluísio Azevedo", "Ática"),
]


def index_full(conn, tenant: str, rows: list[dict], *, status: str = "ready") -> None:
    """Like `index`, but able to set the fields migration 004 added, and the
    year, which is a column nothing else in this file writes."""
    columns = (
        "biblio_id",
        "title",
        "author",
        "publisher",
        "series",
        "abstract",
        "notes",
        "copyright_date",
    )
    with tenant_scope(conn, tenant) as scoped, scoped.cursor() as cur:
        cur.execute(
            "INSERT INTO discovery_tenant (tenant_id, active_generation, index_status) "
            "VALUES (%s, 1, %s) ON CONFLICT (tenant_id) DO UPDATE "
            "SET active_generation = 1, index_status = EXCLUDED.index_status",
            (tenant, status),
        )
        cur.executemany(
            "INSERT INTO discovery_biblio "
            "  (tenant_id, generation, biblio_id, title, author, publisher, "
            "   series, abstract, notes, copyright_date) "
            "VALUES (%s, 1, %s, %s, %s, %s, %s, %s, %s, %s)",
            [(tenant, *(row.get(c) for c in columns)) for row in rows],
        )


# --- relevance --------------------------------------------------------------


def test_a_search_finds_records_by_any_indexed_word(app_conn) -> None:
    index(app_conn, TENANT_A, ACERVO)
    result = search(app_conn, TENANT_A, "casmurro")
    assert [r["biblio_id"] for r in result.records] == [2]


def test_a_plural_query_finds_a_singular_title(app_conn) -> None:
    """Portuguese stemming, which is the whole reason for the tsvector."""
    index(app_conn, TENANT_A, [(1, "Memória de elefante", "Lobo Antunes", None)])
    assert search(app_conn, TENANT_A, "memórias").records


def test_a_title_match_outranks_an_author_match(app_conn) -> None:
    """The A/B/C weights, which are flat relevance without them."""
    index(
        app_conn,
        TENANT_A,
        [
            (1, "Rosa dos ventos", "Anônimo", None),
            (2, "Sagarana", "João Guimarães Rosa", None),
        ],
    )
    assert [r["biblio_id"] for r in search(app_conn, TENANT_A, "rosa").records] == [1, 2]


def test_records_come_back_in_a_stable_order(app_conn) -> None:
    """Equal ranks must not shuffle between calls, or paging lies."""
    index(app_conn, TENANT_A, ACERVO)
    first = [r["biblio_id"] for r in search(app_conn, TENANT_A, "Machado").records]
    second = [r["biblio_id"] for r in search(app_conn, TENANT_A, "Machado").records]
    assert first == second


# --- narrowing --------------------------------------------------------------


def test_an_author_filter_narrows_a_query(app_conn) -> None:
    index(app_conn, TENANT_A, ACERVO)
    result = search(app_conn, TENANT_A, "casmurro", author="Machado")
    assert [r["biblio_id"] for r in result.records] == [2]


def test_an_author_alone_is_a_valid_search(app_conn) -> None:
    """"What else does this author have?" needs no free-text term."""
    index(app_conn, TENANT_A, ACERVO)
    result = search(app_conn, TENANT_A, author="Machado de Assis")
    assert sorted(r["biblio_id"] for r in result.records) == [1, 2]


def test_a_title_alone_is_a_valid_search(app_conn) -> None:
    index(app_conn, TENANT_A, ACERVO)
    assert len(search(app_conn, TENANT_A, title="cortiço").records) == 1


def test_a_wildcard_typed_by_a_patron_is_not_a_wildcard(app_conn) -> None:
    """A bare % matched an entire live catalog once. It matches nothing here."""
    index(app_conn, TENANT_A, ACERVO)
    assert search(app_conn, TENANT_A, author="%").records == []


def test_a_search_with_no_terms_is_refused(app_conn) -> None:
    """"Everything you have" answered with an arbitrary twenty is presented to
    the patron as the collection."""
    index(app_conn, TENANT_A, ACERVO)
    with pytest.raises(EmptySearch):
        search(app_conn, TENANT_A, "   ")


# --- what an empty list means ----------------------------------------------


def test_a_library_that_was_never_synced_reports_building(app_conn) -> None:
    """Not `ready`, which would make "nothing yet" look like "we do not have it"."""
    assert index_status(app_conn, TENANT_A) == "building"
    assert search(app_conn, TENANT_A, "qualquer").index_status == "building"


def test_a_stale_index_still_answers_and_says_so(app_conn) -> None:
    index(app_conn, TENANT_A, ACERVO, status="stale")
    result = search(app_conn, TENANT_A, "casmurro")
    assert result.records and result.index_status == "stale"


def test_a_genuine_miss_is_ready_and_empty(app_conn) -> None:
    index(app_conn, TENANT_A, ACERVO)
    result = search(app_conn, TENANT_A, "astrofísica")
    assert result.records == [] and result.index_status == "ready"


# --- truncation -------------------------------------------------------------


def test_more_matches_than_the_limit_are_reported_as_truncated(app_conn) -> None:
    """"Three results" and "the first three of many" are different answers."""
    index(app_conn, TENANT_A, [(i, f"Romance {i}", "Vários", None) for i in range(1, 8)])
    result = search(app_conn, TENANT_A, "romance", limit=3)
    assert len(result.records) == 3
    assert result.truncated is True


def test_an_exact_fit_is_not_truncated(app_conn) -> None:
    index(app_conn, TENANT_A, [(i, f"Romance {i}", "Vários", None) for i in range(1, 4)])
    result = search(app_conn, TENANT_A, "romance", limit=3)
    assert len(result.records) == 3
    assert result.truncated is False


@pytest.mark.parametrize("limit", [0, -1, MAX_LIMIT + 1])
def test_an_out_of_range_limit_is_refused_not_clamped(app_conn, limit: int) -> None:
    """Silently returning 20 of 100 reads as "that is all of them"."""
    index(app_conn, TENANT_A, ACERVO)
    with pytest.raises(ValueError):
        search(app_conn, TENANT_A, "casmurro", limit=limit)


# --- isolation --------------------------------------------------------------


def test_a_search_never_reaches_another_librarys_records(app_conn) -> None:
    index(app_conn, TENANT_A, ACERVO)
    index(app_conn, TENANT_B, [(1, "Dom Casmurro", "Machado de Assis", None)])

    result = search(app_conn, TENANT_A, "casmurro")
    assert [r["biblio_id"] for r in result.records] == [2]


# --- the contract with /libvox-mcp -----------------------------------------


def test_the_result_matches_what_the_mcp_model_publishes() -> None:
    """Drift here reaches a patron as a missing field, not as an error."""
    models = pytest.importorskip("libvox_mcp.models")

    from dataclasses import fields as dataclass_fields

    from libvox_sync.search import SearchResult

    assert {f.name for f in dataclass_fields(SearchResult)} == set(
        models.SearchCatalogResult.model_fields
    )


def test_every_record_field_is_one_the_mcp_model_carries(app_conn) -> None:
    models = pytest.importorskip("libvox_mcp.models")

    index(app_conn, TENANT_A, ACERVO)
    record = search(app_conn, TENANT_A, "casmurro").records[0]
    assert set(record) == set(models.CatalogRecord.model_fields)


def test_the_default_limit_matches_the_tool_surface() -> None:
    """A larger page is not more helpful to a model with a context window."""
    server = pytest.importorskip("libvox_mcp.server")
    assert DEFAULT_LIMIT == MAX_LIMIT == server.MAX_RESULTS


# --- what the catalog says about a work, not just what it is called ---------


def test_a_record_is_found_by_its_series(app_conn) -> None:
    """The bug that motivated migration 004.

    A catalog of creature names, a series everybody knows it by, and a patron
    asking for "pokemon" was told the library had nothing -- with the index
    reporting `ready`, which is the confident kind of wrong.
    """
    index_full(app_conn, TENANT_A, [{
        "biblio_id": 16,
        "title": "Metapod",
        "series": "Pokemon - Generation I",
    }])
    assert [r["biblio_id"] for r in search(app_conn, TENANT_A, "pokemon").records] == [16]


def test_a_record_is_found_by_what_it_is_about(app_conn) -> None:
    """Descriptive text the ILS already had, and that was being thrown away."""
    index_full(app_conn, TENANT_A, [{
        "biblio_id": 16,
        "title": "Metapod",
        "abstract": "Metapod resembles a chrysalis protected by a hard shell.",
    }])
    assert search(app_conn, TENANT_A, "chrysalis").records


def test_the_series_comes_back_but_the_prose_does_not(app_conn) -> None:
    """Found by abstract; not made to quote it.

    "Metapod, from the Pokemon series" is a sentence MARC.IA should be able to
    say. Twenty abstracts is more text than the conversation around them.
    """
    index_full(app_conn, TENANT_A, [{
        "biblio_id": 16,
        "title": "Metapod",
        "series": "Pokemon - Generation I",
        "abstract": "Metapod resembles a chrysalis.",
    }])
    record = search(app_conn, TENANT_A, "metapod").records[0]
    assert record["series"] == "Pokemon - Generation I"
    assert "abstract" not in record
    assert "notes" not in record


def test_a_title_match_outranks_a_word_buried_in_an_abstract(app_conn) -> None:
    """Weight D is what keeps recall from becoming noise.

    The driver documents the failure in the other direction -- a search for
    "chess" reaching "The Duchess of Malfi". Reaching it is fine; reaching it
    *first*, ahead of a book actually called that, is not.
    """
    index_full(app_conn, TENANT_A, [
        {"biblio_id": 1, "title": "Xadrez para iniciantes"},
        {"biblio_id": 2, "title": "A duquesa", "abstract": "Ela jogava xadrez nas tardes."},
    ])
    assert [r["biblio_id"] for r in search(app_conn, TENANT_A, "xadrez").records] == [1, 2]


def test_a_query_without_accents_finds_a_record_with_them(app_conn) -> None:
    """How people actually type into a search box.

    Migration 004 put "Pokémon - Generation I" in the vector and the search
    still found nothing, because `portuguese` stems plurals and leaves accents
    alone. Everything below missed a record the library holds, and said `ready`
    while doing it.
    """
    index_full(app_conn, TENANT_A, [
        {"biblio_id": 16, "title": "Metapod", "series": "Pokémon - Generation I"},
        {"biblio_id": 3, "title": "Grande sertão: veredas", "author": "João Guimarães Rosa"},
        {"biblio_id": 1, "title": "Memórias póstumas de Brás Cubas"},
    ])
    for query, expected in [
        ("pokemon", 16),
        ("sertao", 3),
        ("guimaraes", 3),
        ("memorias postumas", 1),
    ]:
        found = [r["biblio_id"] for r in search(app_conn, TENANT_A, query).records]
        assert expected in found, f"{query!r} found {found}"


def test_the_accented_spelling_still_works(app_conn) -> None:
    """Folding is symmetric, not a replacement. Both spellings, both ways."""
    index_full(app_conn, TENANT_A, [{"biblio_id": 3, "title": "Grande sertão: veredas"}])
    assert search(app_conn, TENANT_A, "sertão").records
    assert search(app_conn, TENANT_A, "sertao").records


# --- the year, which is not a word ------------------------------------------
#
# A reader asked for books from 1992 and was told the library had none. It had
# fourteen. `copyright_date` comes back in every result, so the model could see
# the year of every record it was handed -- it just had no way to ask for one,
# and "1992" as a query is matched against a text vector the year is not in.

DATADO = [
    {"biblio_id": 1, "title": "Xadrez para iniciantes", "copyright_date": 1992},
    {"biblio_id": 2, "title": "Xadrez avançado", "copyright_date": 1999},
    {"biblio_id": 3, "title": "Xadrez moderno", "copyright_date": 2010},
    {"biblio_id": 4, "title": "Xadrez sem data"},
    {"biblio_id": 5, "title": "Xadrez de ano zero", "copyright_date": 0},
    {"biblio_id": 6, "title": "Culinária de 1992", "copyright_date": 1992},
    {"biblio_id": 7, "title": "Culinária sem data"},
]


def _ids(result) -> set[int]:
    return {record["biblio_id"] for record in result.records}


def test_a_year_range_narrows_a_search(app_conn) -> None:
    index_full(app_conn, TENANT_A, DATADO)
    assert _ids(search(app_conn, TENANT_A, "xadrez", year_from=1990, year_to=1999)) == {
        1,
        2,
    }


def test_a_single_year_is_a_range_of_one(app_conn) -> None:
    index_full(app_conn, TENANT_A, DATADO)
    assert _ids(search(app_conn, TENANT_A, "xadrez", year_from=1992, year_to=1992)) == {
        1
    }


def test_an_open_range_reaches_everything_after_it(app_conn) -> None:
    """"From 1999 on", which is half the questions readers actually ask."""
    index_full(app_conn, TENANT_A, DATADO)
    assert _ids(search(app_conn, TENANT_A, "xadrez", year_from=1999)) == {2, 3}


def test_a_year_alone_is_a_valid_search(app_conn) -> None:
    """The question that started this: "are there any books from 1992?"

    It carries no searchable word at all, and before this it was refused before
    it reached the index.
    """
    index_full(app_conn, TENANT_A, DATADO)
    assert _ids(search(app_conn, TENANT_A, year_from=1992, year_to=1992)) == {1, 6}


def test_a_reversed_range_is_the_range_the_reader_meant(app_conn) -> None:
    """Refusing would cost a round of the agent loop and buy nothing."""
    index_full(app_conn, TENANT_A, DATADO)
    assert _ids(search(app_conn, TENANT_A, "xadrez", year_from=1999, year_to=1990)) == {
        1,
        2,
    }


def test_a_year_of_zero_is_not_a_year(app_conn) -> None:
    """A null wearing a number, seen in a real catalogue in August 2026.

    The fixture carries its own, deliberately: the demo Koha it was found in is
    reset daily, so the row is long gone and this must not depend on it.

    Without the guard, "published before 1900" returns it as though the library
    held something from year zero.
    """
    index_full(app_conn, TENANT_A, DATADO)
    assert search(app_conn, TENANT_A, "xadrez", year_to=1900).records == []


def test_undated_records_counts_within_the_search(app_conn) -> None:
    """Not the catalog's total, which would be a number about a different
    question: for "chess since 1990" what matters is how many chess records
    have no year."""
    index_full(app_conn, TENANT_A, DATADO)

    # Records 4 and 5 are chess with no usable year. Record 7 is not chess.
    assert search(app_conn, TENANT_A, "xadrez", year_from=1990).undated_records == 2
    assert search(app_conn, TENANT_A, year_from=1990).undated_records == 3


def test_undated_records_is_absent_without_a_year_filter(app_conn) -> None:
    """Every search that mentions no year is the shape it always was."""
    index_full(app_conn, TENANT_A, DATADO)
    assert search(app_conn, TENANT_A, "xadrez").undated_records is None


def test_a_year_filter_never_reaches_another_librarys_records(app_conn) -> None:
    """Including through the count, which is a second query and a second chance
    to leak one library's holdings into another's answer."""
    index_full(app_conn, TENANT_A, DATADO)
    result = search(app_conn, TENANT_B, year_from=1900, year_to=2100)
    assert result.records == [] and result.undated_records == 0


# --- repairing a typo -------------------------------------------------------


def vocabulary(conn, tenant: str) -> int:
    """Build the word list the repair draws its candidates from.

    Through the real builder, not a fixture of its own: these tests are about
    words a library catalogued, and a hand-written vocabulary would be a list
    of words *the test* catalogued.
    """
    from libvox_sync.pipeline import _build_vocabulary

    return _build_vocabulary(conn, tenant, 1)


REPARAVEL = [
    {"biblio_id": 1, "title": "Manual de xadrez para iniciantes"},
    {"biblio_id": 2, "title": "Memórias póstumas de Brás Cubas"},
    # Two names one edit apart, so a typo landing between them has no answer.
    # Invented, and invented twice: `vexara` and `vexira` both stem to `vex`,
    # so the typo between them was found by the ordinary search and never
    # reached the repair at all. These two survive the stemmer intact.
    {"biblio_id": 3, "title": "Zolmak", "series": "Zolmak"},
    {"biblio_id": 4, "title": "Zolnak", "series": "Zolnak"},
    # Four letters, and one edit from a word nobody here has: `casa`.
    {"biblio_id": 5, "title": "Uma cara conhecida"},
]


def test_an_explicit_typo_is_repaired_against_the_catalogue(app_conn) -> None:
    """The failure this whole path exists for.

    A reader asked for `xadres` and was told the library had nothing. It is one
    letter from `xadrez`, and the tsvector cannot bridge it: unaccent and the
    Portuguese stemmer reduce `xadres` to `xadr` while `xadrez` stays `xadrez`.
    """
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "xadres")

    assert _ids(result) == {1}
    assert result.searched_for == "xadrez"


def test_a_repair_is_reported_in_the_librarys_own_spelling(app_conn) -> None:
    """`display` earns its column here.

    The vocabulary is matched without accents, because migration 005 decided
    accents do not separate words. Reporting the folded form back would have
    MARC.IA tell a reader she searched for "postumas".
    """
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "postumaz")

    assert _ids(result) == {2}
    assert result.searched_for == "póstumas"


def test_a_word_the_library_has_is_left_alone(app_conn) -> None:
    """The `Opus posthumous` rule, in the index instead of the prompt.

    One word of the query is a typo and the other is a word this library
    catalogued. Only the first is touched — a repair that "corrects" a real
    word is the failure that made POLICY rule 6 forbid the model from doing
    this at all.
    """
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "xadres manual")

    assert _ids(result) == {1}
    assert result.searched_for == "xadrez manual"


def test_a_word_shorter_than_five_letters_is_never_repaired(app_conn) -> None:
    """`casa` is one edit from `cara`, and it is not a typo for it.

    Both are words, and both are somebody's search. The length floor is the
    cheapest way to say that a short word is meant.
    """
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "casa")

    assert result.records == []
    assert result.searched_for is None


def test_an_ambiguous_typo_is_refused_rather_than_guessed(app_conn) -> None:
    """Where the measured error rate of zero comes from.

    `zolvak` is one edit from `zolmak` and one edit from `zolnak`, and the
    catalogue holds both. There is no right answer, so there is no answer.
    """
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "zolvak")

    assert result.records == []
    assert result.searched_for is None


def test_a_word_with_no_near_neighbour_is_not_repaired(app_conn) -> None:
    """An empty result is still an answer, and stays one."""
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "astronomia")

    assert result.records == []
    assert result.searched_for is None


def test_a_search_that_finds_records_is_never_repaired(app_conn) -> None:
    """While there are records to show, the reader's words answered.

    This is also the cost guarantee: the repair runs on the empty path only, so
    a search that hits costs exactly the queries it used to.
    """
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "xadrez")

    assert _ids(result) == {1}
    assert result.searched_for is None


def test_a_repair_that_still_finds_nothing_leaves_no_trace(app_conn) -> None:
    """"I looked for another word and it is not here either" answers nothing.

    The typo is repairable and the repaired search is still empty, because the
    author filter excludes the only record that carries the word.
    """
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "xadres", author="Machado")

    assert result.records == []
    assert result.searched_for is None


def test_a_repair_never_reaches_another_librarys_vocabulary(app_conn) -> None:
    """The isolation that governs records governs the words too.

    Otherwise one library's catalogue would be inferable from another's search
    box: a repair that only succeeds for words the neighbour holds is a
    disclosure, however small.
    """
    index_full(app_conn, TENANT_B, REPARAVEL)
    vocabulary(app_conn, TENANT_B)
    index_full(app_conn, TENANT_A, [{"biblio_id": 9, "title": "Astronomia"}])
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "xadres")

    assert result.records == []
    assert result.searched_for is None


# --- a catalogued name is inverted ------------------------------------------

NOMES = [
    {"biblio_id": 1, "title": "Is this English?", "author": "Fecho, Bob."},
    {"biblio_id": 2, "title": "Uma outra coisa", "author": "Smith, Bob."},
    {"biblio_id": 3, "title": "Mais uma", "author": "Fecho, Ana."},
]


def test_an_author_asked_for_in_natural_order_finds_the_catalogued_one(app_conn) -> None:
    """The failure: `Fecho` found the book, `Fecho, Bob` found the book, and
    `bob fecho` found nothing, because a substring match on the whole phrase
    cannot cross the comma. Inverted is how names are catalogued."""
    index_full(app_conn, TENANT_A, NOMES)

    assert _ids(search(app_conn, TENANT_A, author="bob fecho")) == {1}


def test_the_catalogued_order_still_works(app_conn) -> None:
    index_full(app_conn, TENANT_A, NOMES)

    assert _ids(search(app_conn, TENANT_A, author="Fecho, Bob")) == {1}
    assert _ids(search(app_conn, TENANT_A, author="Fecho")) == {1, 3}


def test_every_word_has_to_be_in_the_same_author(app_conn) -> None:
    """The correctness this rests on. `bob` is in one author and `fecho` in
    another, and a filter that merely found both words somewhere would return
    two records for a person who wrote neither book."""
    index_full(app_conn, TENANT_A, NOMES)

    assert _ids(search(app_conn, TENANT_A, author="bob fecho")) == {1}
    assert search(app_conn, TENANT_A, author="ana smith").records == []


def test_a_wildcard_inside_an_author_is_still_not_a_wildcard(app_conn) -> None:
    """Splitting on spaces must not lose the escaping: `%` reaching the
    database as a wildcard matched an entire live catalogue once."""
    index_full(app_conn, TENANT_A, NOMES)

    assert search(app_conn, TENANT_A, author="% %").records == []


# --- a swap is one slip, and a name is a term like any other -----------------


def test_two_letters_in_the_wrong_order_are_one_slip_not_two(app_conn) -> None:
    """`Calkisn` for `Calkins`, which the catalogue holds and the reader missed.

    Plain Levenshtein charges an adjacent swap two -- it has no notion of one --
    and two is over the limit for a word under nine letters, so the commonest
    typing error there is was the one class the repair could not reach.
    """
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "xadrze")

    assert _ids(result) == {1}
    assert result.searched_for == "xadrez"


def test_two_edits_that_are_not_a_swap_are_still_refused(app_conn) -> None:
    """The rule widened for swaps and for nothing else. Two ordinary edits in a
    six-letter word is where a wrong repair would come from."""
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, "xadrza")

    assert result.records == []
    assert result.searched_for is None


def test_a_typo_in_an_author_is_repaired_like_any_other(app_conn) -> None:
    """The question this arrived as.

    *"tem algum livro do Calkisn?"* reaches the index as `{"author":
    "Calkisn"}`, not as a query -- so for as long as the repair ran on `query`
    alone, the one field the model actually uses for that question was the one
    field it could not rescue.
    """
    index_full(app_conn, TENANT_A, NOMES)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, author="fehco")

    assert _ids(result) == {1, 3}
    assert result.searched_for == "fecho"


def test_a_typo_in_a_title_filter_is_repaired_too(app_conn) -> None:
    index_full(app_conn, TENANT_A, REPARAVEL)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, title="Manual de xadres")

    assert _ids(result) == {1}
    assert result.searched_for == "Manual de xadrez"


def test_an_author_the_library_has_is_never_repaired(app_conn) -> None:
    """The `Opus posthumous` rule, on the field a name arrives in: a term that
    finds records is the reader's own and is not second-guessed."""
    index_full(app_conn, TENANT_A, NOMES)
    vocabulary(app_conn, TENANT_A)

    result = search(app_conn, TENANT_A, author="Fecho")

    assert _ids(result) == {1, 3}
    assert result.searched_for is None
