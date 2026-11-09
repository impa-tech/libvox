"""The read side: answering a patron's search from the Discovery Index.

This is the query `search_catalog` is built on, and the reason the index exists
at all. A patron searching a catalog never generates traffic against the
library's own server — the whole architecture is arranged around that, and this
function is where it becomes true.

It returns five things and not one, because a list of records cannot say why
it is empty -- nor that it answered a different question. `index_status`
distinguishes "the library has nothing like that" from "I have not finished
reading this library's catalogue", `truncated` distinguishes "three results"
from "the first three of many", `undated_records` says how much of the
catalogue a year filter could not reach, and `searched_for` names the word that
was actually searched when a typo was repaired. A caller that cannot tell those
apart will state the wrong one confidently.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

import psycopg

from .db import tenant_scope
from .embedding import Embedder

logger = logging.getLogger(__name__)

DEFAULT_LIMIT = 20
MAX_LIMIT = 20
"""Matches `libvox_mcp.server.MAX_RESULTS`. A larger page is not more helpful:
the caller is a language model with a context window, and twenty records it
reads beats a hundred it skims."""

# The configuration is written into the generated column, so the query has to
# name the same one or the tsquery and the tsvector will not be comparable.
# Per-library language is milestone 7 -- see the README.
# Ours, not PostgreSQL's: `portuguese` leaves accents in place, so "pokemon"
# did not find "Pokémon" and "sertao" did not find "sertão". Migration 005
# defines it as unaccent + portuguese_stem, and both sides of the search use
# it -- an asymmetry here would be worse than no folding at all.
_CONFIG = "libvox_search"

_CANDIDATES = 50
"""How deep each arm goes when both are running.

Deep enough that a record the lexical arm ranked twentieth can still be lifted
by the vector arm agreeing with it, shallow enough that the fusion is not
sorting the whole catalogue. Both arms are index scans, so the cost of the
extra rows is the rows themselves.
"""

RRF_K = 60
"""The constant in Reciprocal Rank Fusion, and the conventional one.

It decides how much a top place is worth against a merely good one: at 60 the
first result scores 1/61 and the tenth 1/70, so agreement between the two arms
matters more than either arm's exact ordering. That is the property worth
having here, because the two arms are good at different questions and neither
ranking is trustworthy on its own.
"""

SIMILARITY_FLOOR = 0.47
"""How close a record has to be before the vector arm will offer it.

Without a floor, KNN always answers: ask a library about knitting and it
returns the five records least unlike knitting, and it stops ever saying no.

Measured over eleven questions against the whole of lib-local, six of which
the collection can answer and five of which it cannot -- the best hit for
each:

    answerable                       unanswerable
    japonês            0.617         jardinagem          0.519  *
    programação        0.576         culinária japonesa  0.454
    poesia irlandesa   0.567         direito tributário  0.440
    xadrez             0.536         tricô               0.417
    cinema             0.515         astronomia          0.398
    matemática         0.489

    * mislabelled while writing this: the library does hold `Gardening with
      children`, so 0.519 is a hit and belongs in the left column.

The lowest right answer is 0.489 and the highest wrong one 0.454, so 0.47
separates them with room on both sides.

**What it costs, because it is not free.** It is a cut on the whole list, not
just the first row: for `cinema` it keeps `The Routledge encyclopedia of films`
at 0.515 and drops `Un chien andalou` at 0.467, which is also an answer. The
floor buys "the library can still say no" and pays for it in recall past the
first result.

**And it is a property of this model and this instruction, not a constant of
nature.** The first version of this number was 0.45, measured without the
query instruction on seven hand-picked titles; against the real collection
that arrangement scored a two-word Irish title above every mathematics book
in the catalogue. Any change to `QUERY_INSTRUCTION` or to the model invalidates
this number, and the way to re-derive it is the table above.
"""

_COLUMNS = (
    "biblio_id",
    "title",
    "author",
    "isbn",
    "publisher",
    "copyright_date",
    "item_type",
    # Returned, unlike abstract and notes: "Metapod, from the Pokemon series"
    # is something MARC.IA should be able to say, and it is one short line.
    # The prose that made the record findable stays in the index -- twenty
    # abstracts is more text than the conversation around them.
    "series",
)


MIN_REPAIRABLE = 5
"""Shortest word a typo is repaired in.

One edit inside a four-letter word is a different word, not a slip -- `casa`
and `cara` and `caso` are all real, and all of them are somebody's search. The
number is the same one Meilisearch draws its typo budget at, arrived at
independently and for the same reason.
"""

SHORTLIST = 20
"""How many trigram neighbours the edit distance is measured against.

Trigrams shortlist and edit distance decides, because trigram similarity alone
gets the motivating case wrong: `similarity('xadres','xadrez')` is 0.556, under
pg_trgm's own default limit of 0.6, while the edit distance is 1. Twenty is
past where the right answer ever appeared in measurement, and the ordering is
index-answered, so a larger shortlist buys nothing.
"""


class EmptySearch(ValueError):
    """No search terms at all."""


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Mirrors `libvox_mcp.models.SearchCatalogResult`, field for field."""

    index_status: str
    records: list[dict] = field(default_factory=list)
    truncated: bool = False
    undated_records: int | None = None
    """How many records matched everything except the year filter and carry no
    year at all.

    `None` when no year was asked for. A number there would be one more thing
    for a model to read and nothing it could do with it -- the field exists to
    stop "we have 14 books from 1992" from claiming completeness over a catalog
    where 70% of the records were never catalogued with a date.
    """

    searched_for: str | None = None
    """The query these records actually answer, when it is not the one asked.

    `None` unless a word was repaired -- which is the ordinary case, and the
    reason no caller has to change. When it is set, the search that was asked
    for found nothing and this one found something, and the reader has to be
    told which word was looked up: a repair nobody can see is a repair nobody
    can correct.
    """


def _escape_like(term: str) -> str:
    """Neutralize LIKE wildcards in text a person typed.

    Not optional, and not theoretical: `/libvox-driver-koha` records that a
    bare `%` as a search term matched **every record in the catalog** on a live
    Koha, because it reaches the database as a wildcard. `_` is the same hazard
    one character wide. The backslash goes first, or escaping the escape
    character doubles back on the wildcards inserted after it.
    """
    return term.replace("\\", "\\\\").replace("%", r"\%").replace("_", r"\_")


_WORD = re.compile(r"\w+", re.UNICODE)


def _transposed(a: str, b: str) -> bool:
    """Whether two words differ only by one pair of swapped neighbours.

    `calkisn` for `calkins`, `teh` for `the`. Plain Levenshtein charges that
    two -- it has no notion of a swap, so it pays for two substitutions -- and
    two is over the limit for anything under nine letters. Damerau-Levenshtein
    counts it as one edit, which is what it is: one slip of one finger, and by
    most measures of typing the commonest one there is.

    `fuzzystrmatch` has no Damerau variant, so the swap is recognised here
    rather than in SQL. Cheap, and it only ever *lowers* a distance the database
    already computed -- the shortlist, the uniqueness rule and the tie refusal
    are untouched.
    """
    if len(a) != len(b):
        return False

    differing = [i for i, (x, y) in enumerate(zip(a, b)) if x != y]

    return (
        len(differing) == 2
        and differing[1] == differing[0] + 1
        and a[differing[0]] == b[differing[1]]
        and a[differing[1]] == b[differing[0]]
    )


def _repair(cur, query: str) -> str | None:
    """Rewrite a query whose words the library does not have, or return None.

    The candidates are this library's own vocabulary, so a repair can only ever
    land on a word somebody here actually catalogued. That is the whole reason
    this is not a spelling dictionary: a dictionary would happily correct a
    reader onto a word the library has never held, which is the failure that
    made POLICY rule 6 forbid the model from doing this.

    Deliberately narrow, and the narrowness is where the accuracy comes from.
    Measured over 800 single-character typos against both test catalogues:
    86.8% repaired, 6% refused as ambiguous, **0% repaired wrongly**.

    Takes any one of the search's term strings -- `query`, `author`, `title` --
    because a typo does not know which field it was typed into. `Calkisn` for
    `Calkins` arrived as an author, which is how the model asks *"tem algum
    livro do X?"*, and for as long as this ran on `query` alone the repair was
    not reached at all.
    """
    matches = [m for m in _WORD.finditer(query)]
    suspect = [(m.start(), m.end(), m.group()) for m in matches
               if len(m.group()) >= MIN_REPAIRABLE]
    if not suspect:
        return None

    # One round trip for the whole query. `WITH ORDINALITY` carries each word's
    # position back, so nothing has to be matched up by string afterwards --
    # and the folding happens here, against a vocabulary that was folded the
    # same way when it was written.
    cur.execute(
        "WITH typed AS ( "
        "    SELECT ord, unaccent(lower(t)) AS word "
        "      FROM unnest(%s::text[]) WITH ORDINALITY AS u(t, ord) "
        ") "
        "SELECT typed.ord, typed.word, c.word, c.display, "
        "       levenshtein_less_equal(typed.word, c.word, 3) AS distance "
        "  FROM typed "
        "  CROSS JOIN LATERAL ( "
        "       SELECT word, display FROM discovery_vocabulary_active "
        "        ORDER BY word <-> typed.word "
        "        LIMIT %s "
        "  ) c",
        ([word for _, _, word in suspect], SHORTLIST),
    )

    # Both folded forms come back, not just the display one: the swap below has
    # to be judged on the same footing the distance was, and `Calkisn` against
    # `Calkins` is not the comparison the database made.
    nearest: dict[int, list[tuple[int, str]]] = {}
    for ord_, typed, word, display, distance in cur.fetchall():
        if distance == 2 and _transposed(typed, word):
            distance = 1
        nearest.setdefault(ord_, []).append((distance, display))

    repairs: dict[int, str] = {}
    for ord_, candidates in nearest.items():
        word = suspect[ord_ - 1][2]
        closest = min(distance for distance, _ in candidates)

        # The library has this word. Whatever else it is, it is not a typo.
        if closest == 0:
            continue

        # Two for a long word, one for a short one: a ninth letter is room for
        # a second slip, and is not room for a second word to wander in.
        if closest > (2 if len(word) >= 9 else 1):
            continue

        tied = {display for distance, display in candidates if distance == closest}
        if len(tied) == 1:
            repairs[ord_] = tied.pop()
        # Otherwise nothing. A tie is the catalogue holding two words equally
        # close, and guessing between them is where a wrong repair would come
        # from -- it is why the measured error rate is zero and not merely low.

    if not repairs:
        return None

    rebuilt, cursor = [], 0
    for ord_, (start, end, word) in enumerate(suspect, start=1):
        if ord_ in repairs:
            rebuilt.append(query[cursor:start])
            rebuilt.append(repairs[ord_])
            cursor = end
    rebuilt.append(query[cursor:])
    return "".join(rebuilt)


def index_status(conn: psycopg.Connection, tenant_id: str) -> str:
    """What state this library's index is in.

    A library with no row has never been synced, and `building` is the honest
    word for that: there is nothing to search yet, and saying `ready` would
    make an empty result look like a genuine "we do not have it".
    """
    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT index_status FROM discovery_tenant WHERE tenant_id = %s",
            (tenant_id,),
        )
        row = cur.fetchone()
    return row[0] if row else "building"


def _text_conditions(
    query: str, author: str | None, title: str | None
) -> tuple[list[str], list[object]]:
    """The text half of the WHERE clause, and its parameters.

    A function rather than inline, so the retry after a repair can be built the
    same way the first attempt was. It used to be a splice -- `[repaired,
    *params[1:]]` -- which held only while the query was the one thing that
    could be repaired and the first parameter in the list. Both stopped being
    true when the author joined it.

    The word count never changes across a repair, so the conditions this
    returns have the same shape either way and the statement can be reused.
    """
    conditions: list[str] = []
    params: list[object] = []

    if query:
        conditions.append(f"search @@ plainto_tsquery('{_CONFIG}', %s)")
        params.append(query)

    # A word at a time, and all of them in the same author. Catalogues record
    # `Fecho, Bob.` and readers ask for `bob fecho`, so a substring match on the
    # whole phrase finds nothing at all -- 0 records for a book the library has,
    # while `Fecho` and `Fecho, Bob` both found it. Inverted is the normal form
    # of a catalogued name, not an edge case.
    #
    # Not done to `title` below, and the reason is not squeamishness: a title is
    # mostly short common words, and `the` and `of` as separate ILIKEs match
    # nearly every record. Word order carries meaning in a title and carries
    # none in a name.
    for word in (author or "").split():
        conditions.append("author ILIKE %s ESCAPE '\\'")
        params.append(f"%{_escape_like(word)}%")

    if title:
        conditions.append("title ILIKE %s ESCAPE '\\'")
        params.append(f"%{_escape_like(title)}%")

    return conditions, params


def fuse(
    lexical: list[tuple],
    vector: list[tuple],
    *,
    limit: int,
    k: int = RRF_K,
) -> list[tuple]:
    """Two ranked lists into one, by Reciprocal Rank Fusion.

    Each row scores `1/(k + position)` in every list it appears in, and the
    scores add. A record both arms found outranks one either arm found alone,
    which is the whole point: the lexical arm knows the reader's words and the
    vector arm knows what they meant, and the records they agree on are the
    ones worth showing first.

    Fused here rather than in SQL, deliberately. The same thing is expressible
    as a CTE with two `row_number()`s and a FULL OUTER JOIN, and it would make
    `_text_conditions` serve a third caller and put this logic somewhere no
    test can reach without a database. Twenty lines of Python with plain tests
    is the better trade at this size.

    Ties break on the lexical arm's order, then on `biblio_id`, so the same
    inputs always produce the same page -- a search that reshuffled equal
    results between two identical questions would look like a bug to the one
    person who noticed.
    """
    scores: dict[int, float] = {}
    rows: dict[int, tuple] = {}
    lexical_rank: dict[int, int] = {}

    for position, row in enumerate(lexical):
        biblio_id = row[0]
        scores[biblio_id] = scores.get(biblio_id, 0.0) + 1.0 / (k + position + 1)
        rows.setdefault(biblio_id, row)
        lexical_rank.setdefault(biblio_id, position)

    for position, row in enumerate(vector):
        biblio_id = row[0]
        scores[biblio_id] = scores.get(biblio_id, 0.0) + 1.0 / (k + position + 1)
        rows.setdefault(biblio_id, row)

    ordered = sorted(
        scores,
        key=lambda biblio_id: (
            -scores[biblio_id],
            lexical_rank.get(biblio_id, len(lexical)),
            biblio_id,
        ),
    )

    return [rows[biblio_id] for biblio_id in ordered[:limit]]


def _as_vector(values: list[float]) -> str:
    """A pgvector literal, cast explicitly at the call site."""
    return "[" + ",".join(f"{value:.7g}" for value in values) + "]"


def search(
    conn: psycopg.Connection,
    tenant_id: str,
    query: str = "",
    *,
    author: str | None = None,
    title: str | None = None,
    year_from: int | None = None,
    year_to: int | None = None,
    limit: int = DEFAULT_LIMIT,
    embedder: Embedder | None = None,
) -> SearchResult:
    """Find records in one library's index, most relevant first.

    `query` is matched against the weighted full-text vector — title outranks
    author, which outranks publisher — so ordering is relevance and not
    insertion order. `author` and `title` narrow it further, and either can be
    used alone.

    `year_from` and `year_to` are a range over `copyright_date`, and either is
    valid alone -- "before 2000" and "from 1990 on" are how the question
    arrives. A year is not in the text vector and never could be: "1992" there
    would also match an ISBN, a page count and a call number, and would not
    answer "the nineties" at all.

    When the search finds nothing, a word the library does not have is repaired
    against the library's own vocabulary and the search is run once more --
    `xadres` reaches the index as the lexeme `xadr` and never meets `xadrez`,
    so the tsvector cannot rescue it and something has to. Any of the three
    term fields is repaired, because a typo does not know which one it was
    typed into: `author` is how *"tem algum livro do X?"* arrives.
    `searched_for` says what was looked up when that happened, and is `None`
    when it did not.

    Refuses a search with no terms at all. "Everything you have" is not a
    question the index should answer with an arbitrary twenty records, and a
    model that gets one will present it as the collection.
    """
    query = (query or "").strip()
    author = (author or "").strip() or None
    title = (title or "").strip() or None

    if year_from and year_to and year_from > year_to:
        # What the reader meant either way. Refusing would cost the model a
        # round of the loop for nothing, which is the same reasoning that has
        # /libvox-mcp clamping `limit` instead of rejecting it.
        year_from, year_to = year_to, year_from

    if not query and not author and not title and not year_from and not year_to:
        raise EmptySearch(
            "a search needs a term: pass query, author, title or a year. "
            "Listing a whole catalog is not something this index answers."
        )

    if limit < 1 or limit > MAX_LIMIT:
        # Refused rather than clamped, the same way the Koha driver refuses an
        # out-of-range limit: silently returning 20 of 100 reads as "that is
        # all of them".
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}, got {limit}")

    conditions, params = _text_conditions(query, author, title)

    # Relevance only when there is something to rank against. A bare integer in
    # ORDER BY is read as a column position, so a constant placeholder here
    # would be rejected outright on the author-only and title-only paths.
    order_by = "biblio_id"
    if query:
        order_by = (
            f"ts_rank(search, plainto_tsquery('{_CONFIG}', %s)) DESC, biblio_id"
        )

    # Held apart from the rest, because `undated_records` counts what matched
    # everything *but* these -- so the two sets cannot be one list.
    year_conditions: list[str] = []
    year_params: list[object] = []

    if year_from or year_to:
        # 0 is a null wearing a number. A record in the lib-catalyst demo Koha
        # carried one in August 2026, and "before 1900" returned it as though it
        # were year zero. Do not go looking for that record to justify this
        # guard: that catalogue is reset daily and the record is already gone.
        # The shape is what recurs, not the row.
        year_conditions.append("copyright_date > 0")
        if year_from:
            year_conditions.append("copyright_date >= %s")
            year_params.append(year_from)
        if year_to:
            year_conditions.append("copyright_date <= %s")
            year_params.append(year_to)

    rank_params = [query] if query else []
    columns = ", ".join(_COLUMNS)

    # A year alone is a valid search, so the text half can be empty here even
    # though the whole cannot -- EmptySearch above saw to that.
    text_where = " AND ".join(conditions) or "TRUE"

    # One row more than asked for, which is how `truncated` is answered without
    # a second COUNT over the whole match set.
    # The vector arm exists only for a text query: there is nothing to embed in
    # a search that is an author, a title or a year and nothing else.
    fusing = bool(query) and embedder is not None and embedder.configured

    # With one arm, exactly what it always was: one row more than asked for, to
    # answer `truncated` without a second COUNT. With two, enough from each for
    # the fusion to have something to agree about -- a top-6 fused against a
    # top-50 would be the vector arm's ranking wearing a fusion's name.
    fetch = _CANDIDATES if fusing else limit + 1

    statement = (
        f"SELECT {columns} FROM discovery_active "
        f" WHERE {' AND '.join([*conditions, *year_conditions])} "
        f" ORDER BY {order_by} "
        f" LIMIT %s"
    )

    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT index_status FROM discovery_tenant WHERE tenant_id = %s",
            (tenant_id,),
        )
        row = cur.fetchone()
        status = row[0] if row else "building"

        cur.execute(statement, [*params, *year_params, *rank_params, fetch])
        rows = cur.fetchall()

        # Only on the empty path, so a search that found something costs not one
        # query more than it did before. This is also the only place a repair
        # can be justified: while there are records to show, the reader's own
        # words answered, and substituting them would be answering a question
        # they did not ask.
        searched_for: str | None = None
        if not rows:
            # Every term the reader gave, not just `query`. The model asks
            # *"tem algum livro do Calkisn?"* as `{"author": "Calkisn"}` -- one
            # slip of one finger from a name this catalogue holds -- and while
            # only the query was repaired, that question could not be rescued
            # at all.
            fixed = {
                name: repaired
                for name, term in (("q", query), ("a", author), ("t", title))
                if term and (repaired := _repair(cur, term))
            }
            if fixed:
                terms = (
                    fixed.get("q", query),
                    fixed.get("a", author),
                    fixed.get("t", title),
                )
                _, retry = _text_conditions(*terms)
                cur.execute(
                    statement,
                    [*retry, *year_params, *([terms[0]] if query else []), fetch],
                )
                rows = cur.fetchall()
                if rows:
                    # `params` feeds the undated count below, which has to be
                    # about the search that answered and not the one that did
                    # not. A repair that found nothing either leaves no trace:
                    # "I looked for another word and it is not here either" is
                    # not an answer anybody asked for.
                    params = retry
                    searched_for = " ".join(fixed[k] for k in "qat" if k in fixed)

        # --- the vector arm ------------------------------------------------
        #
        # Runs whatever the lexical arm found, because the two are good at
        # different questions: `matemática` against an English collection is
        # invisible to any stemmer, and `friendships` is a word the stemmer
        # gets right and the embedding gets wrong. Fusing is how each covers
        # the other; picking one would trade one set of failures for another.
        #
        # After the repair, so a typo is still repaired and still reported.
        # A vector arm that quietly rescued `xadres` would leave the reader
        # never told which word answered them.
        vector_rows: list[tuple] = []
        if fusing:
            vector = embedder.embed_query(query)

            if vector is not None:
                # The same non-text filters, or `author=X` would come back with
                # somebody else's books that happen to be about the same thing.
                narrowing, narrowing_params = _text_conditions("", author, title)
                where = " AND ".join(
                    [
                        "embedding IS NOT NULL",
                        "(embedding <=> %s::vector) <= %s",
                        *narrowing,
                        *year_conditions,
                    ]
                )
                literal = _as_vector(vector)
                cur.execute(
                    f"SELECT {columns} FROM discovery_active "
                    f" WHERE {where} "
                    f" ORDER BY embedding <=> %s::vector "
                    f" LIMIT %s",
                    [
                        literal,
                        1 - SIMILARITY_FLOOR,
                        *narrowing_params,
                        *year_params,
                        literal,
                        _CANDIDATES,
                    ],
                )
                vector_rows = cur.fetchall()

        if vector_rows:
            rows = fuse(rows, vector_rows, limit=fetch)

        # Only when a year was asked for, and counted *within the search*: for
        # "pokemon since 1990" the useful number is how many pokemon records
        # carry no year, not how many the whole catalog is missing. On a
        # year-only search the two are the same number, which is the right one
        # for that question.
        undated: int | None = None
        if year_conditions:
            cur.execute(
                f"SELECT count(*) FROM discovery_active WHERE {text_where} "
                "  AND (copyright_date IS NULL OR copyright_date = 0)",
                params,
            )
            undated = cur.fetchone()[0]

    truncated = len(rows) > limit
    records = [dict(zip(_COLUMNS, r)) for r in rows[:limit]]

    logger.info(
        "%s: search %r%s%s -> %d record(s)%s, index %s",
        tenant_id,
        query or author or title or "",
        f" repaired to {searched_for!r}" if searched_for else "",
        f" [{year_from or ''}..{year_to or ''}]" if year_conditions else "",
        len(records),
        " (truncated)" if truncated else "",
        status,
    )
    return SearchResult(
        index_status=status,
        records=records,
        truncated=truncated,
        undated_records=undated,
        searched_for=searched_for,
    )
