"""The driver and its client against a scripted Evergreen."""

from __future__ import annotations

import logging

import httpx2
import pytest
from libvox_driver_koha.contract import Driver, iter_records
from libvox_driver_koha.errors import (
    NotFound,
    Suppressed,
    TokenRefused,
    Unauthorized,
    Unavailable,
    UpstreamError,
)

from libvox_driver_evergreen import EvergreenDriver, record_url

from .conftest import CONNECTION, PASSWORD, USERNAME


def test_it_is_a_driver(driver) -> None:
    assert isinstance(driver, Driver)
    assert driver.name == "evergreen"


# --- the bulk path ---------------------------------------------------------------


def test_the_walk_ends_on_an_empty_page(driver) -> None:
    records = list(iter_records(driver))
    assert [r.biblio_id for r in records] == [8, 9]


def test_the_cursor_is_the_last_id_seen(driver, evergreen) -> None:
    page = driver.fetch_page(None)
    assert page.next_cursor == "10"
    assert page.skipped == 1

    driver.fetch_page(page.next_cursor)
    import json

    last = evergreen.gateway_calls[-1]
    assert json.loads(last["param"][1]) == {"id": {">": 10}}


def test_a_page_asks_only_for_the_fields_it_reads(driver, evergreen) -> None:
    driver.fetch_page(None)
    import json

    options = json.loads(evergreen.gateway_calls[-1]["param"][2])
    assert options["select"] == {"bre": ["id", "marc", "edit_date", "deleted", "active"]}
    assert options["order_by"] == {"bre": "id"}


def test_a_page_that_does_not_advance_is_an_error(driver, evergreen, monkeypatch) -> None:
    from .conftest import PCRUD_PAGE

    monkeypatch.setattr(
        evergreen, "_gateway",
        lambda request: httpx2.Response(200, json=PCRUD_PAGE),
    )
    with pytest.raises(UpstreamError):
        driver.fetch_page("50")


def test_the_token_never_travels_in_a_url(driver, evergreen) -> None:
    driver.fetch_page(None)
    gateway = [r for r in evergreen.requests if r.url.path == "/osrf-gateway-v1"]
    assert gateway and all(r.method == "POST" for r in gateway)
    assert all("token" not in str(r.url) for r in evergreen.requests)


# --- the live path ---------------------------------------------------------------


def test_get_biblio(driver) -> None:
    assert driver.get_biblio(9).title == "Critical entertainments: music old and new"


def test_get_biblio_of_a_deleted_record(driver) -> None:
    with pytest.raises(Suppressed):
        driver.get_biblio(10)


def test_get_biblio_of_nothing(driver) -> None:
    with pytest.raises(NotFound):
        driver.get_biblio(4242)


def test_availability_names_the_branch(driver) -> None:
    result = driver.check_availability(1)
    assert result.library == "West Emnet Public Library"
    assert result.copies_available == 23


def test_branch_names_are_asked_for_once(driver, evergreen) -> None:
    driver.check_availability(1)
    driver.check_availability(8)
    asked = [r for r in evergreen.requests if r.url.path.endswith("/org_units")]
    assert len(asked) == 1


def test_a_record_with_no_copies_asks_for_nothing_else(driver, evergreen) -> None:
    assert driver.check_availability(4242).copies_total == 0
    assert not [r for r in evergreen.requests if r.url.path.endswith("/org_units")]


def test_search_by_title_reads_the_record_id_from_901(driver) -> None:
    result = driver.search_by_title("piano concerto", limit=3)
    assert [r.biblio_id for r in result.records] == [88, 45, 47]
    assert result.truncated is True


# --- login and session -----------------------------------------------------------


def test_the_login_sends_the_staff_login_type(driver, evergreen) -> None:
    driver.check_availability(1)
    assert evergreen.tokens_issued == 1


def test_a_wrong_password_is_unauthorized(evergreen) -> None:
    evergreen.reject_login = True
    with EvergreenDriver(
        CONNECTION["base_url"], USERNAME, "pw-9f3a1c", transport=httpx2.MockTransport(evergreen)
    ) as d:
        with pytest.raises(Unauthorized) as caught:
            d.check_availability(1)
    assert not isinstance(caught.value, TokenRefused)
    assert "pw-9f3a1c" not in caught.value.message


def test_the_rate_limit_is_waited_out_not_reported_as_a_bad_password(
    driver, evergreen, no_sleep
) -> None:
    """Evergreen answers its rate limit with a 401. Read as a credential
    failure, it would send a librarian to re-type a password that is right."""
    evergreen.rate_limited = 2
    assert driver.check_availability(1).copies_total == 31
    assert evergreen.tokens_issued == 1
    assert any(p >= 1.0 for p in no_sleep)


def test_a_rate_limit_that_never_lifts_is_unavailable(driver, evergreen) -> None:
    evergreen.rate_limited = 100
    with pytest.raises(Unavailable):
        driver.check_availability(1)


def test_rest_requests_are_paced_a_second_apart(driver, no_sleep) -> None:
    driver.check_availability(1)
    driver.check_availability(8)
    assert sum(1 for p in no_sleep if p > 0.9) >= 2


def test_a_forgotten_session_is_renewed_once(driver, evergreen) -> None:
    driver.check_availability(1)
    evergreen.forget_next_session = True
    driver.check_availability(8)
    assert evergreen.tokens_issued == 2


def test_the_gateway_renews_a_forgotten_session_too(driver, evergreen) -> None:
    driver.fetch_page(None)
    evergreen.forget_next_session = True
    driver.fetch_page("10")
    assert evergreen.tokens_issued == 2


def test_closing_logs_out(evergreen) -> None:
    with EvergreenDriver(
        CONNECTION["base_url"], USERNAME, PASSWORD, transport=httpx2.MockTransport(evergreen)
    ) as d:
        d.check_availability(1)
    assert evergreen.logouts == 1
    assert not evergreen.valid


def test_the_token_echoed_by_the_gateway_is_never_logged(evergreen, caplog) -> None:
    caplog.set_level(logging.DEBUG)
    evergreen.valid.clear()
    with EvergreenDriver(
        CONNECTION["base_url"], USERNAME, PASSWORD, transport=httpx2.MockTransport(evergreen)
    ) as d:
        d._client._token = "leaky-token-0123456789"
        with pytest.raises(TokenRefused):
            # Both attempts refused: the renewed token is dropped as well.
            evergreen.forget_next_session = False
            evergreen._login = lambda request: httpx2.Response(
                200, json={"token": "leaky-token-0123456789"}
            )
            d.fetch_page(None)
    assert "leaky-token" not in caplog.text


def test_no_secret_in_repr(driver) -> None:
    text = repr(driver._client)
    assert PASSWORD not in text


# --- links -----------------------------------------------------------------------


@pytest.mark.parametrize(
    "opac",
    [
        "https://catalogo.example",
        "https://catalogo.example/",
        "https://catalogo.example/eg/opac/home",
        "https://catalogo.example/eg/opac/",
    ],
)
def test_record_url(opac) -> None:
    assert record_url(opac, 42) == "https://catalogo.example/eg/opac/record/42"


def test_record_url_takes_only_an_int() -> None:
    with pytest.raises(TypeError):
        record_url("https://x", "42; drop")  # type: ignore[arg-type]
