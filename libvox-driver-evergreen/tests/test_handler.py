"""The invocation contract: what crosses, and what must not."""

from __future__ import annotations

import json

from libvox_driver_evergreen import redacted

from .conftest import CONNECTION, PASSWORD


def request_for(biblio_id, action="check_availability", connection=None):
    return {
        "action": action,
        "params": {"biblio_id": biblio_id},
        "connection": connection or dict(CONNECTION),
    }


def test_availability_crosses_as_data(invoke) -> None:
    response = invoke(request_for(1))
    assert response["ok"] is True
    assert response["data"]["copies_available"] == 23
    assert response["data"]["library"] == "West Emnet Public Library"


def test_failure_is_a_response(invoke) -> None:
    response = invoke(request_for(10, action="get_biblio"))
    assert response["ok"] is False
    assert response["error"]["code"] == "suppressed"


def test_unknown_action(invoke) -> None:
    response = invoke(request_for(1, action="renew_loan"))
    assert response["error"]["code"] == "bad_request"


def test_partial_credentials_are_refused(invoke) -> None:
    connection = dict(CONNECTION, client_secret="")
    response = invoke(request_for(1, connection=connection))
    assert response["error"]["code"] == "bad_request"


def test_the_contract_has_no_place_for_a_tenant(invoke) -> None:
    """The same guarantee the Koha driver pins: a connection, not an identity."""
    for key in ("tenant", "tenant_id", "library", "library_id"):
        response = invoke(request_for(1, connection=dict(CONNECTION, **{key: "x"})))
        assert response["ok"] is False, f"connection accepted a {key!r}"
        assert response["error"]["code"] == "bad_request"


def test_nothing_is_held_between_invocations(invoke, evergreen) -> None:
    """Each call logs in on its own and logs out on its way out."""
    invoke(request_for(1))
    invoke(request_for(1))
    assert evergreen.tokens_issued == 2
    assert evergreen.logouts == 2
    assert not evergreen.valid


def test_the_password_never_crosses_back(invoke, evergreen) -> None:
    evergreen.reject_login = True
    response = invoke(request_for(1))
    assert response["error"]["code"] == "unauthorized"
    assert PASSWORD not in json.dumps(response)


def test_redacted_hides_the_password_and_nothing_else() -> None:
    safe = redacted(request_for(1))
    assert safe["connection"]["client_secret"] == "<redacted>"
    assert safe["connection"]["client_id"] == CONNECTION["client_id"]
