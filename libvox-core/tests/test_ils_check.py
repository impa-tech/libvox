"""Testing a library's catalogue connection, from the panel.

The value of this endpoint is that it tells the three failures apart --
unreachable, rejected, unpermitted -- because "it did not work" sends a
librarian to check all three. So that is what these assert.
"""

from __future__ import annotations

import pytest
from conftest import TENANT_A, databases_up, register_library, store_credentials

# `usefixtures("migrated")` alone errored rather than skipped when no server was
# running, which reports a missing dependency as a broken test. Every test here
# calls `register_library`, and five of six also `store_credentials`, so the
# whole module genuinely needs a database -- it should say so and stand down.
pytestmark = [databases_up, pytest.mark.usefixtures("migrated")]


def ask(client, tenant_id: str = TENANT_A):
    return client.post("/internal/v1/ils/check", json={"tenant_id": tenant_id})


class FakePage:
    def __init__(self, count: int) -> None:
        self.records = [object()] * count
        self.next_cursor = None


def a_driver(monkeypatch, *, raises=None, records: int = 1):
    """Substitute the driver, keeping the credential path real."""
    from libvox_core import ils

    class FakeDriver:
        def __init__(self, *args, **kwargs) -> None:
            self.closed = False

        def fetch_page(self, cursor=None):
            if raises is not None:
                raise raises
            return FakePage(records)

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(
        "libvox_driver_koha.KohaDriver", FakeDriver, raising=False
    )
    return ils


def test_a_working_connection_says_so(client, monkeypatch) -> None:
    register_library()
    store_credentials()
    a_driver(monkeypatch, records=1)

    body = ask(client).json()

    assert body["ok"] is True
    assert body["problem"] is None
    assert body["records_seen"] == 1


def test_a_catalogue_that_authenticates_and_is_empty_is_not_a_pass(
    client, monkeypatch
) -> None:
    """It is how a library ends up telling readers it holds no books, and it
    looks exactly like success from the outside."""
    register_library()
    store_credentials()
    a_driver(monkeypatch, records=0)

    body = ask(client).json()

    assert body["records_seen"] == 0


@pytest.mark.parametrize(
    ("error", "code"),
    [
        ("Unauthorized", "unauthorized"),
        ("PermissionDenied", "permission_denied"),
        ("Unavailable", "upstream_unavailable"),
    ],
)
def test_the_three_failures_are_told_apart(client, monkeypatch, error, code) -> None:
    import libvox_driver_koha as koha

    register_library()
    store_credentials()
    a_driver(monkeypatch, raises=getattr(koha, error)("what Koha said"))

    body = ask(client).json()

    assert body["ok"] is False
    assert body["problem"] == code, "the panel says which of the three it was"
    assert body["detail"] == "what Koha said"


def test_the_answer_is_always_200(client, monkeypatch) -> None:
    """A failed handshake is an answer. A status code would collapse the three
    failures into one sentence on the way through a client."""
    import libvox_driver_koha as koha

    register_library()
    store_credentials()
    a_driver(monkeypatch, raises=koha.Unavailable("down"))

    assert ask(client).status_code == 200


def test_a_library_with_no_catalogue_connection_is_told_that(client) -> None:
    register_library()

    body = ask(client).json()

    assert body["ok"] is False
    assert body["problem"] == "not_configured"


def test_the_check_needs_the_service_token(client) -> None:
    register_library()
    store_credentials()

    client.headers.pop("x-libvox-service-token", None)

    assert ask(client).status_code == 401
