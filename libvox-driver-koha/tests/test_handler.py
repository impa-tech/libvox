"""The invocation contract — the shape the serverless version will inherit."""

from __future__ import annotations

import functools
import json

import pytest

from libvox_driver_koha import handler as handler_module
from libvox_driver_koha import redacted

from .conftest import BIBLIO_PAGE, CREDENTIALS, FakeKoha


@pytest.fixture
def invoke(koha: FakeKoha, monkeypatch):
    """Call `handle()` against a scripted Koha.

    The transport is bound to the driver class rather than passed in the
    request: a test hook has no business being part of a contract that crosses
    a network boundary.
    """
    real = handler_module.KohaDriver
    monkeypatch.setattr(
        handler_module,
        "KohaDriver",
        functools.partial(real, transport=koha.transport, min_interval_s=0.0),
    )

    def call(request: dict) -> dict:
        return handler_module.handle(request)

    call.koha = koha  # type: ignore[attr-defined]
    return call


def request_for(biblio_id, **overrides) -> dict:
    return {
        "action": "get_biblio",
        "params": {"biblio_id": biblio_id},
        "connection": dict(CREDENTIALS),
        **overrides,
    }


# --- the happy path ---------------------------------------------------------


def test_a_successful_invocation(invoke) -> None:
    invoke.koha.biblio(1)
    response = invoke(request_for(1))

    assert response["ok"] is True
    assert response["action"] == "get_biblio"
    assert response["data"]["biblio_id"] == 1
    assert response["data"]["title"] == "E Street shuffle"


def test_the_response_survives_serialization(invoke) -> None:
    """It crosses a process boundary today and a network one tomorrow."""
    invoke.koha.biblio(1)
    response = invoke(request_for(1))
    assert json.loads(json.dumps(response)) == response


def test_a_numeric_string_id_is_accepted(invoke) -> None:
    """JSON from a shell or a form arrives as a string more often than not."""
    invoke.koha.biblio(1)
    assert invoke(request_for("1"))["ok"] is True


# --- failure is data, not an exception --------------------------------------


def test_a_missing_record_is_a_response_not_a_crash(invoke) -> None:
    response = invoke(request_for(999999))
    assert response["ok"] is False
    assert response["error"]["code"] == "not_found"


def test_an_unknown_action_is_refused(invoke) -> None:
    response = invoke(request_for(1, action="delete_everything"))
    assert response["error"]["code"] == "bad_request"


def test_a_missing_connection_is_refused(invoke) -> None:
    response = invoke({"action": "get_biblio", "params": {"biblio_id": 1}})
    assert response["error"]["code"] == "bad_request"


def test_partial_credentials_are_refused_before_dialling(invoke) -> None:
    """An empty secret would reach Koha and come back as an auth failure, and
    the operator would go looking at the library's key instead of the caller."""
    broken = dict(CREDENTIALS, client_secret="")
    response = invoke(request_for(1, connection=broken))

    assert response["error"]["code"] == "bad_request"
    assert "client_secret" in response["error"]["message"]
    assert invoke.koha.requests == [], "nothing should have been sent"


def test_an_unexpected_failure_still_crosses_as_data(invoke, monkeypatch) -> None:
    """A raised exception would surface as a 500 with a stack trace, losing the
    distinction between 'no such book' and 'the library is down'."""

    def explode(*_args, **_kwargs):
        raise ZeroDivisionError("the secret is hunter2")

    monkeypatch.setattr(handler_module, "KohaDriver", explode)
    response = handler_module.handle(request_for(1))

    assert response["ok"] is False
    assert response["error"]["code"] == "driver_error"
    assert "hunter2" not in json.dumps(response), "internal text must not escape"


# --- what the driver must not know or keep ----------------------------------


def test_the_contract_has_no_place_for_a_tenant(invoke) -> None:
    """The driver receives a connection, not an identity.

    Tenant identity stays in /libvox-core. If a tenant key were ever accepted
    here, the driver would become able to correlate invocations — and the
    isolation argument would be one component wider than it can be verified.
    """
    for key in ("tenant", "tenant_id", "library", "library_id"):
        response = invoke(request_for(1, connection=dict(CREDENTIALS, **{key: "x"})))
        assert response["ok"] is False, f"connection accepted a {key!r}"
        assert response["error"]["code"] == "bad_request"


def test_nothing_is_held_between_invocations(invoke) -> None:
    """Each call authenticates on its own.

    A token cache surviving an invocation is where one library's bearer ends up
    on another library's request.
    """
    invoke.koha.biblio(1)
    invoke(request_for(1))
    invoke(request_for(1))

    assert invoke.koha.token_calls == 2


# --- logging ----------------------------------------------------------------


def test_redacted_hides_the_secret_and_nothing_else() -> None:
    """Anything that logs an invocation goes through this; one function to
    reach for is a rule people can actually keep."""
    safe = redacted(request_for(1))

    assert safe["connection"]["client_secret"] == "<redacted>"
    assert CREDENTIALS["client_secret"] not in json.dumps(safe)
    assert safe["connection"]["base_url"] == CREDENTIALS["base_url"]
    assert safe["connection"]["client_id"] == CREDENTIALS["client_id"]
    assert safe["params"] == {"biblio_id": 1}


def test_redacted_does_not_mutate_the_original() -> None:
    original = request_for(1)
    redacted(original)
    assert original["connection"]["client_secret"] == CREDENTIALS["client_secret"]


def test_redacted_survives_a_malformed_request() -> None:
    """It runs in exception paths, where the request is malformed by
    definition. Raising here would replace a real error with this one."""
    assert redacted({"action": "x"}) == {"action": "x"}
    assert redacted({"connection": "not-an-object"})["connection"] == "not-an-object"


# --- fixtures are the real thing --------------------------------------------


def test_the_fixture_is_a_real_capture() -> None:
    """Guards against someone 'tidying' the fixture into an idealized shape."""
    assert len(BIBLIO_PAGE) == 20
    assert BIBLIO_PAGE[0]["title"].endswith(" :"), "ISBD punctuation was removed"
