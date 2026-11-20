"""HTTP transport for one Evergreen server: login, throttling, errors.

Evergreen offers no API key. Access is a **staff login and a password**, which
buy a session token, and the driver speaks to two doors with that one token:

* the **REST API** (`/openapi3/v0`), for the login itself and for a record's
  copies, which it returns as plain JSON with status, shelving location and
  call number already resolved;
* the **OpenSRF gateway** (`/osrf-gateway-v1`), for walking the catalogue. The
  REST API has no way to list records, and the gateway's `pcrud` service pages
  through `biblio.record_entry` by id with the MARC of each record in the same
  response -- a hundred records per request instead of one.

Behaviour verified against a running Evergreen 3.17.3 (the MOBIUS image,
November 2026):

* **The REST API is rate-limited per session**, and the stock installation
  allows about one request per second. The second request inside that second
  answers `401` with `"Rate limit exceeded"` -- a 401 that says nothing about
  the credential. It is told apart from a real one by its message, paced
  against, and retried; mapping it to `Unauthorized` would send a librarian to
  re-type a password that is right.
* The gateway is not rate-limited, always answers HTTP 200, and carries the
  real status inside the body (`{"payload": [...], "status": 401}`). Its error
  text **echoes the session token back**, so nothing from it is logged raw.
* The gateway accepts POST, which keeps the token out of the URL and so out of
  the server's access log.
* A session ends when it is deleted (`DELETE /self/auth`), and Evergreen keeps
  sessions server-side until then. Closing the client logs out: a driver built
  per invocation would otherwise leave one live session behind per question.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable
from typing import Any

import httpx2
from libvox_driver_koha.errors import (
    NotFound,
    PermissionDenied,
    TokenRefused,
    Unauthorized,
    Unavailable,
    UpstreamError,
)

logger = logging.getLogger(__name__)

REST_PREFIX = "/openapi3/v0"
GATEWAY_PATH = "/osrf-gateway-v1"

DEFAULT_TIMEOUT_S = 30.0

LOGIN_TYPE = "staff"
"""Evergreen's login type, appended to the password as `password:staff`.

The REST API reads the basic-auth password as `password:type`. `staff` is what
lets an ordinary staff account in; it is also why a password containing a colon
cannot be used here -- Evergreen would split it."""

REST_INTERVAL_S = 1.05
"""The floor between two REST requests on one session.

Evergreen's stock rate limit is one request per second per session, measured on
3.17.3. A hair over one second, so a request is never the one that lands a
millisecond early. A library that raises its limit loses nothing but a second
per availability check."""

RATE_LIMIT_RETRIES = 3

RETRY_PAUSE_S = 15.0
"""The floor on how long to wait after a timeout before trying once more --
the same rule, and the same reason, as /libvox-driver-koha: a server that took
a request and could not finish it needs room, not a second request."""

DEFAULT_COURTESY = 0.5
"""How much of a request's own cost to wait before the next one. A server that
took eight seconds to answer gets four of quiet; a fast one is unaffected."""


class EvergreenClient:
    """Authenticated access to one Evergreen server.

    The session token is held **for the lifetime of this object, and no
    longer**, and is given back to the server when the object closes. That is
    the rule /libvox-driver-koha keeps for its OAuth token, for the same reason:
    a token that outlived the client is where library A's session ends up on
    library B's request.
    """

    def __init__(
        self,
        base_url: str,
        username: str,
        password: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        min_interval_s: float = 0.2,
        rest_interval_s: float = REST_INTERVAL_S,
        courtesy: float = DEFAULT_COURTESY,
        verify: bool | str = True,
        transport: Any | None = None,
    ) -> None:
        """`verify` takes a CA bundle path for a server with a private
        authority, and exists for that alone; nothing in LibVox passes False.
        `transport` lets tests script an Evergreen while keeping the status
        mapping, the pacing and the login flow under test."""
        if not base_url:
            raise ValueError("base_url is required")

        self.base_url = base_url.rstrip("/")
        self._username = username
        self._password = password

        self._min_interval_s = min_interval_s
        self._rest_interval_s = rest_interval_s
        self._courtesy = courtesy
        self._last_finished_at = 0.0
        self._last_cost_s = 0.0
        self._last_rest_at = 0.0

        self._http = httpx2.Client(
            base_url=self.base_url,
            timeout=timeout_s,
            headers={"accept": "application/json"},
            verify=verify,
            transport=transport,
        )
        self._token: str | None = None

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        """Log out, then close. Logging out is best effort: a server that is
        down has no session worth ending, and a failure here must not mask the
        error that is usually the reason the client is closing."""
        try:
            if self._token:
                self._send_rest(
                    "/self/auth",
                    lambda: self._http.delete(
                        f"{REST_PREFIX}/self/auth",
                        headers={"authorization": f"Bearer {self._token}"},
                    ),
                )
        except Exception:  # noqa: BLE001 - see docstring
            logger.debug("logout failed; the session will expire on its own")
        finally:
            self._token = None
            self._http.close()

    def __enter__(self) -> EvergreenClient:  # noqa: PYI034 - py3.10, see koha
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        # No password, no token. Clients reach tracebacks by accident.
        return f"EvergreenClient(base_url={self.base_url!r}, username={self._username!r})"

    # -- login --------------------------------------------------------------

    def _session(self) -> str:
        if self._token:
            return self._token
        self._token = self._login()
        return self._token

    def _login(self) -> str:
        response = self._send_rest(
            "/self/auth",
            lambda: self._http.get(
                f"{REST_PREFIX}/self/auth",
                auth=(self._username, f"{self._password}:{LOGIN_TYPE}"),
            ),
            paced=False,
        )

        if response.status_code == 404:
            raise UpstreamError(
                f"{self.base_url} has no REST API at {REST_PREFIX}. Evergreen "
                "ships it disabled: the server needs the openapi_server running "
                "and a /openapi3 route in its web server."
            )
        if response.status_code in (400, 401, 403):
            raise Unauthorized(
                f"Evergreen refused the login for {self._username!r} "
                f"(HTTP {response.status_code}, {self._textcode(response)}). "
                "The username or password may be wrong, or the account may "
                "lack permission to log in as staff."
            )
        if response.status_code >= 500:
            raise Unavailable(f"Evergreen returned HTTP {response.status_code} on login")
        if response.status_code != 200:
            raise UpstreamError(f"unexpected HTTP {response.status_code} on login")

        body = self._json(response)
        token = body.get("token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise UpstreamError("Evergreen accepted the login and returned no token")
        logger.debug("logged in to Evergreen as %s", self._username)
        return token

    # -- the REST API -------------------------------------------------------

    def rest(self, path: str) -> Any:
        """GET one REST resource. A session the server forgot is renewed once;
        a second refusal is the server's, not the credential's."""
        for attempt in (1, 2):
            token = self._session()
            response = self._send_rest(
                path,
                lambda: self._http.get(
                    f"{REST_PREFIX}{path}",
                    headers={"authorization": f"Bearer {token}"},
                ),
            )
            if response.status_code == 401 and attempt == 1:
                logger.info("Evergreen dropped the session; logging in again")
                self._token = None
                continue
            self._raise_for_rest(response, path)
            return self._json(response)
        raise AssertionError("unreachable")  # pragma: no cover

    def _send_rest(
        self, path: str, send: Callable[[], httpx2.Response], *, paced: bool = True
    ) -> httpx2.Response:
        """One REST request, paced to the session's rate limit, and repeated
        when the limit answers anyway -- a clock that disagrees with ours by a
        few milliseconds is not a reason to fail a reader's question.

        The login is not paced: there is no session yet to count it against,
        and measured, the first request after it is answered at once."""
        for attempt in range(1, RATE_LIMIT_RETRIES + 1):
            wait = self._rest_interval_s - (time.monotonic() - self._last_rest_at)
            if paced and wait > 0:
                time.sleep(wait)
            response = self._send(path, send)
            if paced:
                self._last_rest_at = time.monotonic()

            if not self._rate_limited(response):
                return response
            if attempt < RATE_LIMIT_RETRIES:
                logger.info("Evergreen rate limit on %s; waiting before retrying", path)
                time.sleep(self._rest_interval_s * attempt)

        raise Unavailable(
            f"Evergreen kept answering 'rate limit exceeded' on {path}. The "
            "server's REST rate limit is lower than one request per second."
        )

    @staticmethod
    def _rate_limited(response: httpx2.Response) -> bool:
        return response.status_code in (401, 429) and "rate limit" in response.text.lower()

    def _raise_for_rest(self, response: httpx2.Response, path: str) -> None:
        if response.status_code < 400:
            return
        if response.status_code == 401:
            raise TokenRefused(
                f"Evergreen issued a session and then refused it on {path}. "
                "The login is accepted -- this is the server's to look at."
            )
        if response.status_code == 403:
            raise PermissionDenied(
                f"the Evergreen account lacks the permission {path} needs"
            )
        if response.status_code == 404:
            raise NotFound(f"not found: {path}")
        if response.status_code >= 500:
            raise Unavailable(f"Evergreen returned HTTP {response.status_code} on {path}")
        raise UpstreamError(f"unexpected HTTP {response.status_code} on {path}")

    # -- the OpenSRF gateway ------------------------------------------------

    def gateway(self, service: str, method: str, *params: Any, auth: bool = True) -> list:
        """Call one OpenSRF method and return its payload list.

        With `auth`, the session token goes first, which is where every
        authenticated Evergreen method expects it. A session the server forgot
        is renewed once, as on the REST side.
        """
        for attempt in (1, 2):
            token = self._session() if auth else None
            args = ([token] if auth else []) + list(params)
            # A list value repeats the key, which is how the gateway takes
            # positional parameters: param=<token>&param=<query>&param=<options>.
            data = {
                "service": service,
                "method": method,
                "param": [json.dumps(arg, separators=(",", ":")) for arg in args],
            }
            response = self._send(
                method, lambda: self._http.post(GATEWAY_PATH, data=data)
            )
            if response.status_code >= 500:
                raise Unavailable(
                    f"Evergreen returned HTTP {response.status_code} on {method}"
                )
            if response.status_code != 200:
                raise UpstreamError(
                    f"unexpected HTTP {response.status_code} from the gateway on {method}"
                )

            body = self._json(response)
            if not isinstance(body, dict):
                raise UpstreamError(f"the gateway answered {method} with no envelope")
            status = body.get("status")
            payload = body.get("payload")

            if (status == 401 or self._event(payload) == "NO_SESSION") and auth:
                if attempt == 1:
                    logger.info("Evergreen dropped the session; logging in again")
                    self._token = None
                    continue
                raise TokenRefused(
                    f"Evergreen issued a session and then refused it on {method}."
                )

            event = self._event(payload)
            debug = self._scrub(str(body.get("debug") or ""), token)
            if event == "PERM_FAILURE" or status == 403 or "permission" in debug.lower():
                raise PermissionDenied(
                    f"the Evergreen account may not call {method}"
                    + (f": {event}" if event else "")
                )
            if event:
                raise UpstreamError(f"Evergreen answered {method} with event {event}")
            if status != 200 or not isinstance(payload, list):
                logger.warning("gateway %s failed: status %s, %s", method, status, debug)
                raise UpstreamError(f"the gateway answered {method} with status {status}")
            return payload
        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _event(payload: Any) -> str | None:
        """The textcode of an Evergreen event in a payload, if it carries one.

        Evergreen reports refusals as a value, not as a status: `{"ilsevent":
        5000, "textcode": "PERM_FAILURE"}` arrives as the method's answer."""
        first = payload[0] if isinstance(payload, list) and payload else payload
        if isinstance(first, dict) and "ilsevent" in first:
            return str(first.get("textcode") or first.get("ilsevent"))
        return None

    @staticmethod
    def _scrub(text: str, token: str | None) -> str:
        """The gateway's error text quotes the token it was given."""
        return text.replace(token, "<token>") if token else text

    # -- unauthenticated reads ---------------------------------------------

    def text(self, path: str, **params: Any) -> str:
        """GET a public, non-JSON resource: the IDL, an SRU answer."""
        response = self._send(
            path, lambda: self._http.get(path, params=params or None)
        )
        if response.status_code == 404:
            raise NotFound(f"not found: {path}")
        if response.status_code >= 500:
            raise Unavailable(f"Evergreen returned HTTP {response.status_code} on {path}")
        if response.status_code != 200:
            raise UpstreamError(f"unexpected HTTP {response.status_code} on {path}")
        return response.text

    # -- every request ------------------------------------------------------

    def _throttle(self) -> None:
        """Wait in proportion to what the last request cost, measured from when
        it finished -- /libvox-driver-koha's rule, which it learned from a
        server that dropped a page when hit again at once."""
        wanted = max(self._min_interval_s, self._last_cost_s * self._courtesy)
        elapsed = time.monotonic() - self._last_finished_at
        if elapsed >= wanted:
            return
        waiting = wanted - elapsed
        if waiting > 1.0:
            logger.info(
                "waiting %.1fs before the next request; the last took %.1fs",
                waiting,
                self._last_cost_s,
            )
        time.sleep(waiting)

    def _send(self, what: str, send: Callable[[], httpx2.Response]) -> httpx2.Response:
        """One request, retried once when the server could not finish it."""
        for attempt in (1, 2):
            self._throttle()
            started = time.monotonic()
            try:
                response = send()
            except (httpx2.TimeoutException, httpx2.RemoteProtocolError) as exc:
                self._charge(started)
                if attempt == 2:
                    raise Unavailable(
                        f"{self.base_url} could not finish {what} after "
                        f"{self._last_cost_s:.0f}s, twice. The server is under load."
                    ) from exc
                pause = max(RETRY_PAUSE_S, self._last_cost_s)
                logger.warning(
                    "%s could not finish %s; waiting %.0fs before one more attempt",
                    self.base_url,
                    what,
                    pause,
                )
                time.sleep(pause)
                continue
            except httpx2.RequestError as exc:
                raise Unavailable(f"cannot reach {self.base_url}: {exc}") from exc
            self._charge(started)
            return response
        raise AssertionError("unreachable")  # pragma: no cover

    def _charge(self, started: float) -> None:
        self._last_finished_at = time.monotonic()
        self._last_cost_s = self._last_finished_at - started

    @staticmethod
    def _json(response: httpx2.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                f"Evergreen returned non-JSON on {response.request.url.path}"
            ) from exc

    @staticmethod
    def _textcode(response: httpx2.Response) -> str:
        try:
            body = response.json()
        except ValueError:
            return "no detail"
        if isinstance(body, dict):
            return str(body.get("textcode") or body.get("desc") or "no detail")
        return "no detail"
