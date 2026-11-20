"""A scripted Evergreen, driven through the real HTTP stack.

`httpx2.MockTransport` rather than a fake client, so what is under test
includes the parts most likely to be wrong: the login and its `password:staff`
form, the REST rate limit told apart from a refused credential, the gateway's
status-in-the-body, and the logout on close.

The payloads are captured from a running Evergreen 3.17.3 (MOBIUS image,
November 2026), not written by hand.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx2
import pytest

HERE = Path(__file__).parent

IDL_BRE = (HERE / "idl_bre.xml").read_text(encoding="utf-8")
# Records 8, 9 and 10 as `pcrud` pages them; 10 is deleted.
PCRUD_PAGE: dict[str, Any] = json.loads((HERE / "pcrud_page.json").read_text("utf-8"))
PCRUD_EMPTY: dict[str, Any] = json.loads((HERE / "pcrud_empty.json").read_text("utf-8"))
PCRUD_DELETED: dict[str, Any] = json.loads((HERE / "pcrud_deleted.json").read_text("utf-8"))
# Record 1: 31 copies across five branches, four of them in a reference room.
HOLDINGS_AVAILABLE: list = json.loads((HERE / "holdings_available.json").read_text("utf-8"))
# Record 8: 21 copies, three checked out.
HOLDINGS_CHECKED_OUT: list = json.loads((HERE / "holdings_checked_out.json").read_text("utf-8"))
ORG_UNITS: list = json.loads((HERE / "org_units.json").read_text("utf-8"))
SRU_PIANO = (HERE / "sru_piano_concerto.xml").read_text(encoding="utf-8")

USERNAME = "libvox"
PASSWORD = "s3cret-pass"
CONNECTION = {
    "base_url": "https://evergreen.example",
    "client_id": USERNAME,
    "client_secret": PASSWORD,
}


class FakeEvergreen:
    """Answers the handful of requests the driver makes, and records them."""

    def __init__(self) -> None:
        self.tokens_issued = 0
        self.logouts = 0
        self.valid: set[str] = set()
        self.rate_limited = 0  # how many REST calls to refuse before answering
        self.holdings: dict[int, list] = {1: HOLDINGS_AVAILABLE, 8: HOLDINGS_CHECKED_OUT}
        self.requests: list[httpx2.Request] = []
        self.gateway_calls: list[dict[str, list[str]]] = []
        self.reject_login = False
        self.forget_next_session = False

    def __call__(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(request)
        path = request.url.path

        if path == "/openapi3/v0/self/auth" and request.method == "GET":
            return self._login(request)
        if path == "/openapi3/v0/self/auth" and request.method == "DELETE":
            self.logouts += 1
            self.valid.discard(self._bearer(request))
            return httpx2.Response(200, json={})
        if path.startswith("/openapi3/v0/"):
            return self._rest(request, path[len("/openapi3/v0"):])
        if path == "/osrf-gateway-v1":
            return self._gateway(request)
        if path == "/reports/fm_IDL.xml":
            return httpx2.Response(200, text=IDL_BRE)
        if path == "/opac/extras/sru":
            return httpx2.Response(200, text=SRU_PIANO)
        return httpx2.Response(404, text="not found")

    # -- doors --------------------------------------------------------------

    def _login(self, request: httpx2.Request) -> httpx2.Response:
        user, _, password = base64.b64decode(
            request.headers["authorization"].split()[1]
        ).decode().partition(":")
        if self.reject_login or (user, password) != (USERNAME, f"{PASSWORD}:staff"):
            return httpx2.Response(
                401, json={"textcode": "LOGIN_FAILED", "desc": "User login failed"}
            )
        self.tokens_issued += 1
        token = f"token{self.tokens_issued:028d}"
        self.valid.add(token)
        return httpx2.Response(200, json={"token": token})

    def _rest(self, request: httpx2.Request, path: str) -> httpx2.Response:
        if self.rate_limited:
            self.rate_limited -= 1
            return httpx2.Response(
                401, json={"errors": [{"message": "Rate limit exceeded"}], "status": 401}
            )
        if self.forget_next_session:
            self.forget_next_session = False
            self.valid.clear()
        if self._bearer(request) not in self.valid:
            return httpx2.Response(
                401, json={"errors": [{"message": "Invalid session"}], "status": 401}
            )
        if path == "/org_units":
            return httpx2.Response(200, json=ORG_UNITS)
        match = re.fullmatch(r"/bib/(\d+)/holdings", path)
        if match:
            return httpx2.Response(200, json=self.holdings.get(int(match.group(1)), []))
        return httpx2.Response(404, json={"errors": [{"message": "not found"}]})

    def _gateway(self, request: httpx2.Request) -> httpx2.Response:
        form = parse_qs(request.content.decode())
        self.gateway_calls.append(form)
        params = [json.loads(p) for p in form.get("param", [])]
        token = params[0] if params else None
        if self.forget_next_session:
            self.forget_next_session = False
            self.valid.clear()
        if token not in self.valid:
            return httpx2.Response(200, json={
                "payload": [],
                "debug": f"permacrud received a bad auth token: {token}",
                "status": 401,
            })
        method = form["method"][0]
        if method == "open-ils.pcrud.search.bre.atomic":
            after = params[1]["id"][">"]
            return httpx2.Response(200, json=PCRUD_PAGE if after < 10 else PCRUD_EMPTY)
        if method == "open-ils.pcrud.retrieve.bre":
            record = params[1]
            if record == 10:
                return httpx2.Response(200, json=PCRUD_DELETED)
            objects = [o for o in PCRUD_PAGE["payload"][0] if o["__p"][9] == record]
            return httpx2.Response(200, json={"payload": objects, "status": 200})
        return httpx2.Response(200, json={"payload": [], "status": 404})

    @staticmethod
    def _bearer(request: httpx2.Request) -> str | None:
        value = request.headers.get("authorization", "")
        return value.split(" ", 1)[1] if value.startswith("Bearer ") else None


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    """The pacing is real code under test; the waiting is not. Every pause the
    client asks for is recorded instead of slept."""
    pauses: list[float] = []
    monkeypatch.setattr(
        "libvox_driver_evergreen.client.time.sleep", lambda s: pauses.append(s)
    )
    return pauses


@pytest.fixture
def evergreen() -> FakeEvergreen:
    return FakeEvergreen()


@pytest.fixture
def driver(evergreen):
    from libvox_driver_evergreen import EvergreenDriver

    d = EvergreenDriver(
        CONNECTION["base_url"],
        username=USERNAME,
        password=PASSWORD,
        transport=httpx2.MockTransport(evergreen),
    )
    yield d
    d.close()


@pytest.fixture
def invoke(monkeypatch, evergreen):
    """`handle()` with every driver it builds wired to the fake server."""
    from libvox_driver_evergreen import driver as driver_module
    from libvox_driver_evergreen import handler as handler_module

    real = driver_module.EvergreenDriver

    def wired(*args, **kwargs):
        return real(*args, transport=httpx2.MockTransport(evergreen), **kwargs)

    monkeypatch.setattr(handler_module, "EvergreenDriver", wired)
    return handler_module.handle
