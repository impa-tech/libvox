"""Text to vector, over an OpenAI-compatible `/v1/embeddings` endpoint.

Used from two places that never run together: the pipeline, which embeds a
whole catalogue once per import, and `search`, which embeds one query per
question. Both reach the same service and neither may take the system down
with it -- so every failure here returns nothing rather than raising, and the
caller carries on with a lexical index that answers perfectly well on its own.

**Why the vectors are truncated.** The model returns 2560 dimensions and
pgvector indexes at most 2000. Qwen3's embeddings are Matryoshka -- a prefix is
a valid embedding of the same text at a smaller size -- so the first 1024 are
taken and renormalized. Measured against the collection this was written for,
the ordering at 1024 is identical to the full vector; at 512 two irrelevant
entries swap places. Storage halves either way, and 1024 is what the column
declares.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass

import httpx2

logger = logging.getLogger(__name__)

DIMENSIONS = 1024
"""What `discovery_biblio.embedding` declares, and the prefix length taken."""

BATCH = 100
"""Texts per request. Measured at 45 records/s against the local gateway --
3.2 minutes for a collection of 8,774. Larger batches did not go faster and
make one timeout cost more work."""

QUERY_INSTRUCTION = (
    "Instruct: Given a library patron's question, retrieve catalogue records "
    "that answer it\nQuery: "
)
"""What a *query* is wrapped in before it is embedded. A record is not.

Qwen3-Embedding is asymmetric by design and this is the shape its own card
specifies. Skipping it does not fail loudly -- it quietly turns the similarity
into a measure of length. Measured against this collection without it, a
two-word Irish title scored 0.616 against `math` while `The hundred penny box
Mathis, Sharon Bell.` scored 0.306, and every query returned the shortest
titles in the catalogue:

    query "xadrez"              raw    instructed
    Pawn structure chess        0.577  ->  0.489      the right answer
    An tuil                     0.592  ->  0.302
    Vittrad                     0.579  ->  0.299

Raw, the noise outranked the answer. Instructed, the answer is first and the
noise falls away from it. Same model, same vectors on the document side --
only the query changed.
"""

TIMEOUT_S = 120.0
"""Generous, because a batch of 100 is a real amount of work for the server
and an import is not waiting on a person."""


@dataclass(frozen=True, slots=True)
class EmbeddingSettings:
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None

    @property
    def configured(self) -> bool:
        """Whether there is anywhere to ask.

        Unset is a supported configuration, not a broken one: the index is
        built without vectors and every search answers from the lexical arm,
        which is what the whole system did before this module existed.
        """
        return bool(self.base_url and self.model)

    @classmethod
    def from_env(cls) -> EmbeddingSettings:
        return cls(
            base_url=os.environ.get("LIBVOX_EMBEDDING_BASE_URL") or None,
            model=os.environ.get("LIBVOX_EMBEDDING_MODEL") or None,
            api_key=os.environ.get("LIBVOX_EMBEDDING_API_KEY") or None,
        )


def truncate(vector: list[float], dimensions: int = DIMENSIONS) -> list[float]:
    """The first `dimensions` values, renormalized to unit length.

    Renormalizing matters: a Matryoshka prefix is a direction, not a unit
    vector, and cosine distance in PostgreSQL is computed from the stored
    values. Feeding it unnormalized prefixes would make the similarity floor
    mean something slightly different for every row.

    A zero vector cannot be normalized and is returned as it came -- it is
    already meaningless, and dividing by zero would only make it noisy as well.
    """
    prefix = vector[:dimensions]
    norm = math.sqrt(sum(value * value for value in prefix))

    return [value / norm for value in prefix] if norm else prefix


class Embedder:
    """One client, reused across batches so the connection is not rebuilt.

    Never raises for a service problem. `embed` answers `None` per text it
    could not embed, and the caller decides what a missing vector means -- for
    the pipeline it is a row indexed without one, for search it is a query
    answered lexically.
    """

    def __init__(self, settings: EmbeddingSettings) -> None:
        self._settings = settings
        self._client: httpx2.Client | None = None

    @property
    def configured(self) -> bool:
        return self._settings.configured

    def __enter__(self) -> Embedder:  # noqa: PYI034 -- see below
        # ruff wants `Self`, which is `typing.Self` from 3.11. This package
        # declares 3.10, so honouring it would mean a `typing_extensions`
        # dependency for an annotation nobody reads. The Koha driver made the
        # same call for the same reason.
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def embed_query(self, text: str) -> list[float] | None:
        """One reader's question, wrapped in the instruction, or None.

        Separate from `embed` and not a flag on it, because the asymmetry is
        the easiest thing here to get wrong and the hardest to notice: both
        halves return 1024 numbers either way, nothing raises, and the index
        simply starts answering with its shortest titles.
        """
        vectors = self.embed([QUERY_INSTRUCTION + text])

        return vectors[0] if vectors else None

    def embed(self, texts: list[str]) -> list[list[float] | None]:
        """Every text, in order, each either a vector or None.

        The length of the answer always matches the length of the request, so a
        caller can zip it against its own rows without checking. A batch that
        failed is a run of `None`, not a short list.
        """
        if not texts:
            return []

        if not self.configured:
            return [None] * len(texts)

        out: list[list[float] | None] = []
        for start in range(0, len(texts), BATCH):
            batch = texts[start : start + BATCH]
            out.extend(self._batch(batch))

        return out

    def _batch(self, texts: list[str]) -> list[list[float] | None]:
        # One retry, and no more. A gateway that refused twice in a row is
        # down, and an import that keeps asking turns a five-minute job into a
        # long one that fails anyway.
        for attempt in (1, 2):
            try:
                return self._request(texts)
            except (httpx2.HTTPError, ValueError, KeyError) as exc:
                if attempt == 2:
                    logger.warning(
                        "embedding %d text(s) failed, indexing them without a "
                        "vector: %s",
                        len(texts),
                        exc,
                    )
                    return [None] * len(texts)

                logger.debug("embedding attempt %d failed, retrying: %s", attempt, exc)

        return [None] * len(texts)  # pragma: no cover - the loop always returns

    def _request(self, texts: list[str]) -> list[list[float] | None]:
        if self._client is None:
            headers = {"Content-Type": "application/json"}
            if self._settings.api_key:
                headers["Authorization"] = f"Bearer {self._settings.api_key}"

            self._client = httpx2.Client(
                base_url=self._settings.base_url or "",
                headers=headers,
                timeout=TIMEOUT_S,
            )

        response = self._client.post(
            "/embeddings",
            json={"model": self._settings.model, "input": texts},
        )
        response.raise_for_status()
        payload = response.json()

        # Ordered by `index` rather than by arrival: the contract says the
        # field is there, and trusting the array order instead would pair a
        # record with another record's vector if a server ever answered out of
        # order. That is a bug nothing would report.
        rows = sorted(payload["data"], key=lambda row: row["index"])

        if len(rows) != len(texts):
            raise ValueError(
                f"asked for {len(texts)} embeddings and got {len(rows)}"
            )

        return [truncate(row["embedding"]) for row in rows]


def text_of(record: dict) -> str:
    """What gets embedded for one record.

    Title, series and author, and deliberately not the abstract or the notes.
    Two reasons: most records have neither -- 218 abstracts across 8,774
    records in the collection this was measured on -- so including them would
    make a handful of records embed differently from the rest for no reason a
    reader would recognise; and cataloguer prose is long, which would push the
    part that identifies the book into a small corner of the vector.
    """
    parts = [record.get("title"), record.get("series"), record.get("author")]

    return " ".join(part for part in parts if part)
