"""The driver against a scripted Koha: both execution paths, and every way the
ILS can refuse."""

from __future__ import annotations

import httpx2
import pytest

from libvox_driver_koha import (
    KohaDriver,
    NotFound,
    PermissionDenied,
    Suppressed,
    TokenRefused,
    Unauthorized,
    Unavailable,
    UpstreamError,
    iter_records,
)

from .conftest import BIBLIO_PAGE, BIBLIO_SINGLE, CREDENTIALS, TOKEN_PATH, FakeKoha


def _driver_on(transport: httpx2.MockTransport) -> KohaDriver:
    """A driver on a transport the test built, for the two cases that need to
    interfere with the token -- which the fake answers from its own branch
    rather than its routing table."""
    return KohaDriver(
        CREDENTIALS["base_url"],
        client_id=CREDENTIALS["client_id"],
        client_secret=CREDENTIALS["client_secret"],
        min_interval_s=0.0,
        transport=transport,
    )


def driver_for(koha: FakeKoha, **kwargs) -> KohaDriver:
    return KohaDriver(
        CREDENTIALS["base_url"],
        client_id=CREDENTIALS["client_id"],
        client_secret=CREDENTIALS["client_secret"],
        min_interval_s=0.0,  # no throttle in tests; the live driver keeps it
        transport=koha.transport,
        **kwargs,
    )


# --- the live path ----------------------------------------------------------


def test_get_biblio_returns_a_normalized_record(koha: FakeKoha) -> None:
    with driver_for(koha.biblio(1)) as driver:
        record = driver.get_biblio(1)

    assert record.biblio_id == 1
    assert record.title == "E Street shuffle"  # ISBD colon stripped
    assert record.author == "Heylin, Clinton."
    assert record.isbn == "9780670026623"  # qualifier dropped


def test_the_accept_header_is_always_sent(koha: FakeKoha) -> None:
    """A live Koha answers 406 without it, listing the types it will serve.

    Easy to lose in a refactor, and the failure looks like a server problem
    rather than a client one.
    """
    with driver_for(koha.biblio(1)) as driver:
        driver.get_biblio(1)

    for request in koha.resource_requests():
        assert request.headers["accept"] == "application/json"


def test_one_token_serves_a_whole_operation(koha: FakeKoha) -> None:
    """Caching is bounded by the client's life, which is one operation."""
    with driver_for(koha.biblio(1).biblio(2)) as driver:
        driver.get_biblio(1)
        driver.get_biblio(2)

    assert koha.token_calls == 1


def test_a_missing_record_is_not_found(koha: FakeKoha) -> None:
    with driver_for(koha) as driver, pytest.raises(NotFound):
        driver.get_biblio(999999)


def test_a_hidden_record_is_refused_rather_than_returned(koha: FakeKoha) -> None:
    """Suppressed is not NotFound: the library needs to tell them apart even
    though the patron must not."""
    hidden = dict(BIBLIO_PAGE[0], opac_suppressed=True)
    with driver_for(koha.biblio(1, hidden)) as driver, pytest.raises(Suppressed):
        driver.get_biblio(1)


def test_a_non_integer_id_never_reaches_the_ils(koha: FakeKoha) -> None:
    with driver_for(koha) as driver, pytest.raises(UpstreamError):
        driver.get_biblio("1; DROP TABLE biblio")  # type: ignore[arg-type]
    assert koha.resource_requests() == []


# --- how the ILS refuses ----------------------------------------------------


def test_a_rejected_token_is_unauthorized(koha: FakeKoha) -> None:
    koha.status("/api/v1/biblios/1", 401, {"error": "Authentication failure."})
    with driver_for(koha) as driver, pytest.raises(Unauthorized):
        driver.get_biblio(1)


def test_a_missing_permission_names_itself(koha: FakeKoha) -> None:
    """Verbatim from a live Koha. Passing the detail through is what turns a
    dead end into an instruction for the library's administrator."""
    koha.status(
        "/api/v1/biblios/1",
        403,
        {
            "error": "Authorization failure. Missing required permission(s).",
            "required_permissions": {"catalogue": "1"},
        },
    )
    with driver_for(koha) as driver, pytest.raises(PermissionDenied) as caught:
        driver.get_biblio(1)

    assert caught.value.details["required_permissions"] == {"catalogue": "1"}
    assert caught.value.code == "permission_denied"


def test_a_server_error_is_retryable_not_a_missing_book(koha: FakeKoha) -> None:
    koha.status("/api/v1/biblios/1", 503, {"error": "down for maintenance"})
    with driver_for(koha) as driver, pytest.raises(Unavailable):
        driver.get_biblio(1)


def test_an_unreachable_server_is_unavailable() -> None:
    """The distinction the patron feels: 'no such book' versus 'try later'."""

    def refuse(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ConnectError("connection refused", request=request)

    driver = KohaDriver(
        "http://koha.test",
        client_id="x",
        client_secret="y",
        min_interval_s=0.0,
        transport=httpx2.MockTransport(refuse),
    )
    with driver, pytest.raises(Unavailable):
        driver.get_biblio(1)


def test_a_wrong_secret_is_unauthorized_not_permission_denied() -> None:
    """Koha answers 403 to a bad secret on the token endpoint, which would
    otherwise be mistaken for a missing grant — a different fix entirely."""

    def reject(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(403, json={"error": "unauthorized_client"})

    with KohaDriver(
        "http://koha.test",
        client_id="x",
        client_secret="wrong",
        min_interval_s=0.0,
        transport=httpx2.MockTransport(reject),
    ) as driver, pytest.raises(Unauthorized):
        driver.get_biblio(1)


# --- the bulk path ----------------------------------------------------------


def test_paginates_to_completion(koha: FakeKoha) -> None:
    with driver_for(koha.page(), page_size=7) as driver:
        records = list(iter_records(driver))

    assert [r.biblio_id for r in records] == [r["biblio_id"] for r in BIBLIO_PAGE]


def test_an_exactly_full_final_page_terminates(koha: FakeKoha) -> None:
    """Koha sends no total, so a full last page costs one empty follow-up.

    The driver must survive that instead of looping or raising.
    """
    with driver_for(koha.page(), page_size=len(BIBLIO_PAGE)) as driver:
        first = driver.fetch_page()
        assert len(first.records) == len(BIBLIO_PAGE)
        assert first.next_cursor is not None, "a full page cannot be assumed last"

        second = driver.fetch_page(first.next_cursor)
        assert second.records == []
        assert second.next_cursor is None


def test_a_short_page_does_not_end_pagination(koha: FakeKoha) -> None:
    """It used to, and that trusted the server to be healthy.

    Koha sends no total and no Link header, so a short page was read as the
    end. A server that truncates a response under load -- and this driver has
    watched servers time out and drop connections mid-response -- would end the
    walk early, and the import would publish a partial catalogue as `ready`.
    Only an empty page is trusted now.
    """
    with driver_for(koha.page(), page_size=len(BIBLIO_PAGE) + 1) as driver:
        page = driver.fetch_page()

    assert len(page.records) == len(BIBLIO_PAGE)
    assert page.next_cursor is not None, "a short page is not proof of the end"


def test_a_server_that_never_returns_an_empty_page_fails_loudly(
    koha: FakeKoha, monkeypatch
) -> None:
    """A server clamping out-of-range page numbers to the last page would
    answer forever. Better a loud failure than a loop nobody notices."""
    monkeypatch.setattr("libvox_driver_koha.driver.MAX_PAGES", 3)

    # A server that answers every page with the same rows, which is what
    # clamping an out-of-range page number looks like from here.
    koha.routes["/api/v1/biblios"] = lambda _r: httpx2.Response(200, json=BIBLIO_PAGE)

    with driver_for(koha, page_size=len(BIBLIO_PAGE)) as driver:
        cursor = None
        with pytest.raises(UpstreamError, match="repeating a page"):
            for _ in range(5):
                cursor = driver.fetch_page(cursor).next_cursor


def test_bulk_tolerates_what_a_lookup_cannot(koha: FakeKoha) -> None:
    """One bad row in half a million is a log line; the same row asked for by
    id is the whole answer."""
    rows = [
        BIBLIO_PAGE[0],
        dict(BIBLIO_PAGE[1], opac_suppressed=True),
        dict(BIBLIO_PAGE[2], title=None),
    ]
    with driver_for(koha.page(rows), page_size=10) as driver:
        page = driver.fetch_page()

    assert len(page.records) == 1
    assert page.skipped == 2


def test_a_token_that_is_issued_then_refused_is_its_own_failure(koha: FakeKoha) -> None:
    """Seen on a live demo server: bogus credentials answered 403, real ones
    answered 200 with a token, and every request made with that token answered
    401 "Authentication failure."

    Telling someone to re-check an id and a secret that are demonstrably right
    sends them looking at the one thing that is not wrong -- so this carries its
    own code, while still being an Unauthorized for anything already handling
    that.
    """
    koha.status("/api/v1/biblios/1", 401, {"error": "Authentication failure."})

    with driver_for(koha) as driver, pytest.raises(TokenRefused) as refusal:
        driver.get_biblio(1)

    assert refusal.value.code == "token_refused"
    assert isinstance(refusal.value, Unauthorized), "existing handling keeps working"
    assert "credentials are registered" in refusal.value.message


# --- being gentle with someone else's server --------------------------------


def test_a_slow_server_is_given_room_before_being_asked_again(
    koha: FakeKoha, monkeypatch
) -> None:
    """The defect this replaced, seen in production against a demo Koha.

    `_last_request_at` is stamped *before* a request, so after a page that took
    eight seconds the elapsed time was already eight, the fixed floor of 0.2s
    was long past, and no wait happened at all. The server that needed room was
    hit again immediately; the one with room to spare was the one being paced.
    Pages 1 and 2 answered in ~8s, page 3's body never finished arriving.
    """
    koha.biblio(1)
    slept: list[float] = []
    monkeypatch.setattr("libvox_driver_koha.client.time.sleep", slept.append)

    # A clock where the first request takes eight seconds and no wall time
    # passes afterwards, so any pause has to come from the throttle.
    ticks = iter([
        0.0,   # throttle: elapsed
        0.0,   # started
        8.0,   # charge: finished  -> cost 8s
        8.0,   # throttle: elapsed -> 0s since it finished
        8.0,   # started
        8.0,   # charge: finished
    ])
    monkeypatch.setattr(
        "libvox_driver_koha.client.time.monotonic", lambda: next(ticks, 8.0)
    )

    with driver_for(koha, courtesy=0.5) as driver:
        driver.get_biblio(1)
        driver.get_biblio(1)

    assert slept, "a request that cost the server eight seconds bought no pause"
    assert max(slept) == pytest.approx(4.0), "half of what the last request cost"


def test_a_fast_server_is_not_slowed_down(koha: FakeKoha, monkeypatch) -> None:
    """Courtesy applies to servers that need it. One answering in milliseconds
    is paced by the fixed floor, exactly as before."""
    koha.biblio(1)
    slept: list[float] = []
    monkeypatch.setattr("libvox_driver_koha.client.time.sleep", slept.append)

    with driver_for(koha, courtesy=0.5) as driver:
        driver.get_biblio(1)
        driver.get_biblio(1)

    assert all(wait <= 0.2 for wait in slept), slept


def test_a_timeout_is_retried_once_after_a_long_pause(
    koha: FakeKoha, monkeypatch
) -> None:
    attempts = {"n": 0}

    def sometimes_times_out(request: httpx2.Request) -> httpx2.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx2.ReadTimeout("timed out", request=request)
        return httpx2.Response(200, json=BIBLIO_SINGLE)

    koha.routes["/api/v1/biblios/1"] = sometimes_times_out

    slept: list[float] = []
    monkeypatch.setattr("libvox_driver_koha.client.time.sleep", slept.append)

    with driver_for(koha) as driver:
        record = driver.get_biblio(1)

    assert record.biblio_id == 1
    assert attempts["n"] == 2, "tried once more"
    assert max(slept) >= 15.0, "and waited long enough to be a pause"


def test_a_server_that_keeps_timing_out_is_left_alone(
    koha: FakeKoha, monkeypatch
) -> None:
    """Two timeouts is the server saying it cannot serve this. Retrying past
    that turns a struggling server into an unreachable one."""

    def always_times_out(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    koha.routes["/api/v1/biblios/1"] = always_times_out
    monkeypatch.setattr("libvox_driver_koha.client.time.sleep", lambda _s: None)

    calls = {"n": 0}
    original = koha.routes["/api/v1/biblios/1"]

    def counted(request: httpx2.Request) -> httpx2.Response:
        calls["n"] += 1
        return original(request)

    koha.routes["/api/v1/biblios/1"] = counted

    with driver_for(koha) as driver, pytest.raises(Unavailable) as failure:
        driver.get_biblio(1)

    assert calls["n"] == 2, "twice, and no more"
    assert "would add to it" in failure.value.message


def test_a_dropped_connection_is_retried_like_a_timeout(
    koha: FakeKoha, monkeypatch
) -> None:
    """From a worker log: `RemoteProtocolError: Server disconnected without
    sending a response.`

    A server that takes a request and drops the connection mid-response is
    saying the same thing as one that times out -- it could not finish -- and
    it was reaching the log as a hard failure with no second attempt. A server
    recycling its workers turned a whole import into nothing.
    """
    attempts = {"n": 0}

    def drops_once(request: httpx2.Request) -> httpx2.Response:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise httpx2.RemoteProtocolError(
                "Server disconnected without sending a response.", request=request
            )
        return httpx2.Response(200, json=BIBLIO_SINGLE)

    koha.routes["/api/v1/biblios/1"] = drops_once
    monkeypatch.setattr("libvox_driver_koha.client.time.sleep", lambda _s: None)

    with driver_for(koha) as driver:
        record = driver.get_biblio(1)

    assert record.biblio_id == 1
    assert attempts["n"] == 2


def test_a_server_that_keeps_dropping_says_what_it_did(
    koha: FakeKoha, monkeypatch
) -> None:
    """"Dropped the connection" and "timed out" are different things to have
    happened, and an operator reading the log should be told which."""

    def always_drops(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.RemoteProtocolError("gone", request=request)

    koha.routes["/api/v1/biblios/1"] = always_drops
    monkeypatch.setattr("libvox_driver_koha.client.time.sleep", lambda _s: None)

    with driver_for(koha) as driver, pytest.raises(Unavailable) as failure:
        driver.get_biblio(1)

    assert "dropped the connection" in failure.value.message
    assert "would add to it" in failure.value.message


def test_a_token_request_that_times_out_is_tried_once_more(
    koha: FakeKoha, monkeypatch
) -> None:
    """The gap the pacing round left, and it killed an import the same day:

        lib-mykoha: import failed — cannot reach demo-intra.mykoha.co.nz: timed out

    The retry covered page requests and not the token, so a slow server ended
    the whole import before a single page was asked for.
    """
    koha.biblio(1)
    monkeypatch.setattr("libvox_driver_koha.client.time.sleep", lambda _s: None)

    # The token is answered by the fake's own branch rather than its routing
    # table, so the transport is wrapped instead of a route replaced.
    attempts = {"n": 0}
    inner = koha.transport

    def times_out_once(request: httpx2.Request) -> httpx2.Response:
        if request.url.path == TOKEN_PATH:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise httpx2.ReadTimeout("timed out", request=request)
        return inner.handle_request(request)

    with _driver_on(httpx2.MockTransport(times_out_once)) as driver:
        record = driver.get_biblio(1)

    assert record.biblio_id == 1
    assert attempts["n"] == 2, "the token was asked for twice"


def test_a_token_request_that_keeps_timing_out_says_the_server_is_under_load(
    koha: FakeKoha, monkeypatch
) -> None:
    monkeypatch.setattr("libvox_driver_koha.client.time.sleep", lambda _s: None)

    def token_never_answers(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("timed out", request=request)

    with _driver_on(
        httpx2.MockTransport(token_never_answers)
    ) as driver, pytest.raises(Unavailable) as failure:
        driver.get_biblio(1)

    assert "oauth/token" in failure.value.message
    assert "would add to it" in failure.value.message
