"""The embedding client, and the fusion it feeds.

No database and no network. What is asserted here is the half of the vector
arm that has nothing to do with PostgreSQL: that a truncated vector is still a
unit vector, that a service which is down costs the caller nothing but its
vectors, and that Reciprocal Rank Fusion prefers what both arms agree on.
"""

from __future__ import annotations

import json
import math

import httpx2
import pytest

from libvox_sync.embedding import (
    DIMENSIONS,
    QUERY_INSTRUCTION,
    Embedder,
    EmbeddingSettings,
    text_of,
    truncate,
)
from libvox_sync.search import fuse

CONFIGURED = EmbeddingSettings(
    base_url="http://embeddings.test/v1",
    model="test-embedding",
    api_key="k",
)


def responder(handler):
    """An Embedder wired to a fake transport."""
    embedder = Embedder(CONFIGURED)
    embedder._client = httpx2.Client(
        base_url=CONFIGURED.base_url,
        transport=httpx2.MockTransport(handler),
    )

    return embedder


# --- truncation -------------------------------------------------------------


def test_a_truncated_vector_is_still_a_unit_vector() -> None:
    """Matryoshka prefixes are directions, not unit vectors.

    Cosine distance in PostgreSQL is computed from the stored values, so a
    prefix left unnormalized would make the similarity floor mean something
    slightly different for every row -- and the floor is what decides between
    answering and saying no.
    """
    vector = [float(n) for n in range(2560)]

    short = truncate(vector)

    assert len(short) == DIMENSIONS
    assert math.isclose(math.sqrt(sum(v * v for v in short)), 1.0, rel_tol=1e-9)


def test_the_prefix_is_the_prefix_and_not_a_resampling() -> None:
    """The first 1024 values, in order. A Matryoshka prefix is only valid
    because it is literally the beginning of the vector."""
    vector = [float(n) for n in range(2560)]

    short = truncate(vector, dimensions=4)
    ratios = [v / short[1] for v in short]

    assert ratios == pytest.approx([0.0, 1.0, 2.0, 3.0])


def test_a_zero_vector_comes_back_as_it_went_in() -> None:
    """It is already meaningless; dividing by zero would make it noisy too."""
    assert truncate([0.0] * 8, dimensions=4) == [0.0] * 4


# --- the client -------------------------------------------------------------


def test_nothing_configured_is_a_supported_state_not_an_error() -> None:
    """The index is built without vectors and the lexical arm answers alone,
    which is what the whole system did before this module existed."""
    embedder = Embedder(EmbeddingSettings())

    assert not embedder.configured
    assert embedder.embed(["anything"]) == [None]
    assert embedder.embed_query("anything") is None


def test_a_service_that_is_down_costs_the_vectors_and_nothing_else() -> None:
    """The rule the whole design rests on: no failure here may stop an import.

    A record indexed without a vector is still found by the lexical arm, still
    answered about, still correct. Losing that is a smaller thing than losing
    the record, and much smaller than losing the import.
    """

    def refuse(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(503)

    embedder = responder(refuse)

    assert embedder.embed(["a", "b"]) == [None, None]


def test_a_refusal_is_retried_once_and_not_twice() -> None:
    """A gateway that said no twice is down. An import that keeps asking turns
    a five-minute job into a long one that fails anyway."""
    calls: list = []

    def refuse(request: httpx2.Request) -> httpx2.Response:
        calls.append(request)

        return httpx2.Response(500)

    responder(refuse).embed(["a"])

    assert len(calls) == 2


def test_the_answer_is_always_as_long_as_the_question() -> None:
    """So a caller can zip it against its own rows without checking. A batch
    that failed is a run of None, never a short list."""
    embedder = responder(lambda r: httpx2.Response(200, json={"data": []}))

    assert embedder.embed(["a", "b", "c"]) == [None, None, None]


def test_vectors_are_paired_by_index_not_by_arrival() -> None:
    """A server answering out of order would otherwise give one record another
    record's vector -- a bug that nothing would ever report."""

    def out_of_order(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "data": [
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )

    first, second = responder(out_of_order).embed(["a", "b"])

    assert first == pytest.approx([1.0, 0.0])
    assert second == pytest.approx([0.0, 1.0])


def test_a_query_is_instructed_and_a_record_is_not() -> None:
    """The asymmetry Qwen3-Embedding is built around, and the easiest thing
    here to get wrong.

    Nothing fails when the instruction is missing: both sides still return
    1024 numbers and the index simply starts answering with its shortest
    titles. Measured against the real collection without it, `An tuil` scored
    0.616 against `math` and every query came back with the same handful of
    two-word titles.
    """
    sent: list = []

    def capture(request: httpx2.Request) -> httpx2.Response:
        sent.append(json.loads(request.content)["input"])

        return httpx2.Response(
            200, json={"data": [{"index": 0, "embedding": [1.0, 0.0]}]}
        )

    embedder = responder(capture)

    embedder.embed_query("xadrez")
    embedder.embed(["Pawn structure chess"])

    assert sent[0] == [QUERY_INSTRUCTION + "xadrez"]
    assert sent[1] == ["Pawn structure chess"]


def test_what_gets_embedded_is_what_identifies_the_book() -> None:
    """Title, series and author -- not the abstract or the notes.

    Two reasons. Most records have neither (218 abstracts across 8,774 records
    in the collection this was measured on), so including them would make a
    handful of records embed unlike all the rest for no reason a reader would
    recognise; and cataloguer prose is long enough to push the part that names
    the book into a corner of the vector.
    """
    text = text_of(
        {
            "title": "Metapod",
            "series": "Pokemon - Generation I",
            "author": "Satoshi Tajiri",
            "abstract": "Metapod resembles a chrysalis.",
            "notes": "Based on butterfly chrysalises.",
        }
    )

    assert text == "Metapod Pokemon - Generation I Satoshi Tajiri"

    # And a record with only a title embeds its title, not a string of Nones.
    assert text_of({"title": "Metapod", "series": None, "author": None}) == "Metapod"


# --- the fusion -------------------------------------------------------------


def test_what_both_arms_found_comes_first() -> None:
    """The whole point of fusing rather than choosing: the lexical arm knows
    the reader's words, the vector arm knows what they meant, and the records
    they agree on are the ones worth showing first."""
    lexical = [(1,), (2,), (3,)]
    vector = [(3,), (9,)]

    assert [row[0] for row in fuse(lexical, vector, limit=4)] == [3, 1, 2, 9]


def test_a_record_only_one_arm_found_is_still_offered() -> None:
    """`matemática` against an English collection is invisible to the lexical
    arm, and those records are the entire reason this exists."""
    assert [row[0] for row in fuse([], [(7,), (8,)], limit=5)] == [7, 8]


def test_with_no_vector_arm_the_lexical_order_is_untouched() -> None:
    """A search where nothing was embedded, or where the service was down, has
    to come back exactly as it always did."""
    lexical = [(4,), (5,), (6,)]

    assert fuse(lexical, [], limit=10) == lexical


def test_a_tie_breaks_on_the_lexical_order_and_never_on_chance() -> None:
    """Two arms that disagree completely produce a real tie, and it has to
    resolve the same way twice.

    Here 30 is first lexically and last by vector, 20 is the reverse, and both
    score 1/61 + 1/63. The middle record scores 1/62 twice, which is *less* --
    two second places are worth less than a first and a last, which is RRF
    working as intended and worth seeing written down.

    The tie goes to the lexical arm's order. A search that reshuffled equal
    results between two identical questions would look like a bug to the one
    person who noticed.
    """
    lexical = [(30,), (10,), (20,)]
    vector = [(20,), (10,), (30,)]

    once = fuse(lexical, vector, limit=3)

    assert once == fuse(lexical, vector, limit=3)
    assert [row[0] for row in once] == [30, 20, 10]


def test_the_limit_is_the_limit() -> None:
    assert len(fuse([(n,) for n in range(20)], [(n,) for n in range(30, 50)], limit=5)) == 5
