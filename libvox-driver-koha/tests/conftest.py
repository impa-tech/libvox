"""A scripted Koha, driven through the real HTTP stack.

Tests use `httpx2.MockTransport` rather than replacing `KohaClient`, so what is
under test includes the parts most likely to be wrong: the `Accept` header the
live server insists on, the mapping from status code to error, and the token
flow.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest

HERE = Path(__file__).parent

BIBLIO_PAGE: list[dict[str, Any]] = json.loads(
    (HERE / "biblio_page.json").read_text(encoding="utf-8")
)
BIBLIO_SINGLE: dict[str, Any] = json.loads(
    (HERE / "biblio_single.json").read_text(encoding="utf-8")
)

# What a live Koha actually returned for q={"title":{"like":"%chess%"}} over a
# 436-record catalog. Captured rather than hand-written, because the point it
# illustrates — LIKE matching inside words — is easy to argue away and hard to
# argue with.
SEARCH_CHESS: list[dict[str, Any]] = json.loads(
    (HERE / "biblio_search_chess.json").read_text(encoding="utf-8")
)

# One copy, everything clear: the shape of a book on the shelf.
ITEMS_ON_SHELF: list[dict[str, Any]] = json.loads(
    (HERE / "items_example.json").read_text(encoding="utf-8")
)

# The same endpoint on a live Swiss library, for a copy that is out. It is here
# because it settles a question no argument could: Koha's item record carries
# `checked_out_date` and no due date at all.
ITEMS_CHECKED_OUT: list[dict[str, Any]] = json.loads(
    (HERE / "items_checked_out.json").read_text(encoding="utf-8")
)

# Twelve branches from a live instance. 'CPL' is 'Centerville'.
LIBRARIES: list[dict[str, Any]] = json.loads(
    (HERE / "libraries_example.json").read_text(encoding="utf-8")
)

# Koha's authorised values in category LOC -- the shelving locations. Taken
# from a live instance, where `opac_description` is null on every one of the
# ten: the staff description is the ordinary path and the public one is the
# exception a library configures. One entry here has it set, so the preference
# is exercised rather than assumed.
SHELVES: list[dict[str, Any]] = json.loads(
    (HERE / "shelving_locations_example.json").read_text(encoding="utf-8")
)

TOKEN_PATH = "/api/v1/oauth/token"

CREDENTIALS = {
    "base_url": "http://koha.test",
    "client_id": "test-client",
    "client_secret": "test-secret",
}


class FakeKoha:
    """Records every request, answers from a routing table.

    Bodies copied verbatim from a live Koha (Koha Testing Docker, August 2026).
    """

    def __init__(self) -> None:
        self.requests: list[httpx2.Request] = []
        self.token_calls = 0
        self.routes: dict[str, Callable[[httpx2.Request], httpx2.Response]] = {}

    # -- canned answers -----------------------------------------------------

    def biblio(self, biblio_id: int, body: dict[str, Any] | None = None) -> FakeKoha:
        payload = body if body is not None else BIBLIO_SINGLE
        self.routes[f"/api/v1/biblios/{biblio_id}"] = lambda _r: httpx2.Response(
            200, json=payload
        )
        return self

    def items(self, biblio_id: int, rows: list[dict[str, Any]] | None = None) -> FakeKoha:
        payload = ITEMS_ON_SHELF if rows is None else rows
        self.routes[f"/api/v1/biblios/{biblio_id}/items"] = lambda _r: httpx2.Response(
            200, json=payload
        )
        return self

    def libraries(self, rows: list[dict[str, Any]] | None = None) -> FakeKoha:
        payload = LIBRARIES if rows is None else rows
        self.routes["/api/v1/libraries"] = lambda _r: httpx2.Response(200, json=payload)
        return self

    def shelves(self, rows: list[dict[str, Any]] | None = None) -> FakeKoha:
        payload = SHELVES if rows is None else rows
        self.routes[
            "/api/v1/authorised_value_categories/LOC/authorised_values"
        ] = lambda _r: httpx2.Response(200, json=payload)
        return self

    def status(self, path: str, code: int, body: Any = None) -> FakeKoha:
        self.routes[path] = lambda _r: httpx2.Response(code, json=body)
        return self

    def page(self, rows: list[dict[str, Any]] | None = None) -> FakeKoha:
        """Paginate `rows` the way Koha does: 1-indexed, no total, no headers."""
        data = BIBLIO_PAGE if rows is None else rows

        def respond(request: httpx2.Request) -> httpx2.Response:
            number = int(request.url.params.get("_page", 1))
            size = int(request.url.params.get("_per_page", 100))
            start = (number - 1) * size
            return httpx2.Response(200, json=data[start : start + size])

        self.routes["/api/v1/biblios"] = respond
        return self

    # -- transport ----------------------------------------------------------

    def _dispatch(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path

        if path == TOKEN_PATH:
            self.token_calls += 1
            return httpx2.Response(
                200, json={"access_token": "tok-abc", "expires_in": 3600}
            )

        route = self.routes.get(path)
        if route is None:
            return httpx2.Response(
                404,
                json={"error": "Bibliographic record not found",
                      "error_code": "not_found"},
            )
        return route(request)

    @property
    def transport(self) -> httpx2.MockTransport:
        return httpx2.MockTransport(self._dispatch)

    # -- assertions helpers -------------------------------------------------

    def resource_requests(self) -> list[httpx2.Request]:
        return [r for r in self.requests if r.url.path != TOKEN_PATH]


@pytest.fixture
def koha() -> FakeKoha:
    return FakeKoha()
