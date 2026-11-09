"""HTTP transport for one Koha instance: authentication, throttling, errors.

Separate from the driver so that the mapping of "what the wire said" to "what
went wrong" is testable on its own, and so the driver deals only in domain
objects.

Behaviour verified against a running Koha (Koha Testing Docker, August 2026):

* **`Accept` is mandatory.** `GET /biblios/{id}` with no `Accept` header
  answers `406` with the list of types it will serve. The endpoint negotiates
  `marcxml+xml`, `marc-in-json` and `marc`; only `application/json` yields the
  flat object this driver expects. The header is set once, on the client.
* Errors carry a machine-readable code: `{"error": "...", "error_code":
  "not_found"}` on 404, and `{"required_permissions": {"catalogue": "1"}}` on
  403.
* OAuth2 client-credentials tokens last 3600s.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx2

from .errors import (
    NotFound,
    PermissionDenied,
    TokenRefused,
    Unauthorized,
    Unavailable,
    UpstreamError,
)

logger = logging.getLogger(__name__)

TOKEN_REFRESH_MARGIN_S = 60
DEFAULT_TIMEOUT_S = 30.0

RETRY_PAUSE_S = 15.0
"""The floor on how long to wait after a timeout before trying once more.

Deliberately long. A timeout means the server did not finish in the time it was
given, and retrying briskly is how a struggling server is turned into an
unreachable one."""

DEFAULT_COURTESY = 0.5
"""How much of a request's own cost to wait before making the next one.

At 0.5, a page that took the library's server eight seconds buys it four
seconds of quiet. A server answering in 200ms is unaffected -- the fixed floor
is larger and applies instead -- so this only ever slows down the servers that
are already struggling, which are the only ones it should slow down."""


class KohaClient:
    """Authenticated access to one Koha instance.

    The access token is cached **for the lifetime of this object, and no
    longer**. That single rule satisfies both execution paths without a flag:
    a bulk import holds one client for hundreds of requests and pays for one
    token, while the invocation contract builds a client per invocation and so
    holds nothing between them. A token cache that outlived the client is
    exactly where library A's bearer would end up on library B's request.

    Credentials are supplied by the caller and never read from the environment
    here — in standalone mode the CLI reads them, and in production Core
    injects them per call.
    """

    def __init__(
        self,
        base_url: str,
        client_id: str,
        client_secret: str,
        *,
        timeout_s: float = DEFAULT_TIMEOUT_S,
        min_interval_s: float = 0.2,
        courtesy: float = DEFAULT_COURTESY,
        transport: Any | None = None,
    ) -> None:
        """`transport` exists so tests can drive the real HTTP layer against a
        scripted Koha. Substituting the transport keeps status mapping, header
        negotiation and the token flow under test; substituting this class
        would leave all three untested."""
        if not base_url:
            raise ValueError("base_url is required")

        self.base_url = base_url.rstrip("/")
        self._client_id = client_id
        self._client_secret = client_secret

        # Throttle lives here, not in the caller: the component that knows it
        # is talking to someone else's library server is the one that should
        # decide how gently to do it.
        self._min_interval_s = min_interval_s
        self._last_finished_at = 0.0
        self._last_cost_s = 0.0
        self._courtesy = courtesy

        self._http = httpx2.Client(
            base_url=self.base_url,
            timeout=timeout_s,
            # Not a nicety: without this the single-record endpoint answers 406.
            headers={"accept": "application/json"},
            transport=transport,
        )
        self._token: str | None = None
        self._token_expires_at = datetime.min.replace(tzinfo=timezone.utc)

    # -- lifecycle ----------------------------------------------------------

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> KohaClient:  # noqa: PYI034 -- see below
        # ruff wants `Self`, which is right for a subclass and is `typing.Self`
        # from 3.11. This module declares 3.10, so honouring it would mean a
        # `typing_extensions` dependency for an annotation nobody reads.
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def __repr__(self) -> str:
        # No secret, no token. Clients reach tracebacks by accident.
        return f"KohaClient(base_url={self.base_url!r}, client_id={self._client_id!r})"

    # -- auth ---------------------------------------------------------------

    def _bearer(self) -> str:
        now = datetime.now(timezone.utc)
        if self._token and now < self._token_expires_at:
            return self._token

        response = self._send(
            "/api/v1/oauth/token", lambda: self._http.post(
                "/api/v1/oauth/token",
                data={
                    "grant_type": "client_credentials",
                    "client_id": self._client_id,
                    "client_secret": self._client_secret,
                },
                headers={"content-type": "application/x-www-form-urlencoded"},
            )
        )

        if response.status_code in (400, 401, 403):
            # Koha answers 403 to a bad client_secret on this endpoint, which
            # is a credential problem rather than a permission one — hence the
            # explicit mapping instead of reusing _raise_for_status.
            raise Unauthorized(
                "Koha rejected the client credentials "
                f"(HTTP {response.status_code}); the key may be wrong, "
                "inactive or revoked"
            )
        if response.status_code >= 500:
            raise Unavailable(f"Koha returned HTTP {response.status_code} on token")
        if response.status_code != 200:
            raise UpstreamError(f"unexpected HTTP {response.status_code} on token")

        body = self._json(response)
        token = body.get("access_token") if isinstance(body, dict) else None
        if not token:
            raise UpstreamError("Koha returned no access_token")

        expires_in = int(body.get("expires_in", 3600))
        self._token = token
        self._token_expires_at = now + timedelta(
            seconds=max(expires_in - TOKEN_REFRESH_MARGIN_S, 30)
        )
        logger.debug("obtained Koha token, valid until %s", self._token_expires_at)
        return token

    # -- requests -----------------------------------------------------------

    def _throttle(self) -> None:
        """Wait before asking again, in proportion to what the last ask cost.

        **Measured from when the last request finished, not when it started.**
        That is the whole correction. The stamp used to be taken before the
        request, so a page that took the server eight seconds had already
        "elapsed" eight by the time it returned — every pause was spent by the
        request that earned it, and the server that needed room got hit again
        immediately while the one with room to spare was the one being paced.

        Seen against a demo Koha: pages 1 and 2 answered in ~8s each, nothing
        waited in between, and page 3's body never finished arriving.

        A healthy server answers in a fraction of a second, so the fixed floor
        is the larger number and applies exactly as before. This only slows
        down servers that are already struggling.
        """
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

    def _charge(self, started: float) -> None:
        """Record what the request cost, and when it stopped costing it."""
        self._last_finished_at = time.monotonic()
        self._last_cost_s = self._last_finished_at - started

    def get(self, path: str, **params: Any) -> Any:
        """GET a JSON resource, translating transport failures into errors.

        A server that accepts a request and then fails to finish it is retried
        once, after a pause long enough to be a pause -- and then abandoned.
        That is the strongest signal a server can send that it needs room, so
        the answer is to wait rather than to try harder.

        Two failures mean that, not one: a timeout, and a connection dropped
        mid-response. The second was reaching the log as a hard failure with no
        second attempt, which is how a struggling server -- one recycling its
        workers, say -- turned a whole import into nothing.
        """
        bearer = self._bearer()

        response = self._send(
            path, lambda: self._http.get(
                path,
                params=params or None,
                headers={"authorization": f"Bearer {bearer}"},
            )
        )

        self._raise_for_status(response, path)
        return self._json(response)

    def _send(self, path: str, send: Callable[[], httpx2.Response]) -> httpx2.Response:
        """One request, retried once when the server could not finish it.

        Two failures mean that: a timeout, and a connection dropped
        mid-response. Both say the server took the request and ran out of road,
        so the answer is to wait rather than to try harder — and then to stop,
        because a third attempt is pushing something that is already failing.

        **Everything that leaves this client comes through here**, the token
        included. It used to be only the page requests: `_bearer` raised on the
        first timeout, so a slow server killed an import before a single page
        was asked for. Keeping the retry in one place is also what stops the
        two drifting once there are two of them.
        """
        for attempt in (1, 2):
            self._throttle()
            started = time.monotonic()

            try:
                response = send()
            except (httpx2.TimeoutException, httpx2.RemoteProtocolError) as exc:
                # Charged even though it failed: a request the server took and
                # could not finish is the most expensive one it handled, and
                # the next wait should reflect that.
                self._charge(started)
                what = (
                    "timed out"
                    if isinstance(exc, httpx2.TimeoutException)
                    else "dropped the connection"
                )

                if attempt == 2:
                    raise Unavailable(
                        f"{self.base_url} {what} on {path} after "
                        f"{self._last_cost_s:.0f}s, twice. The server is under "
                        "load; importing again now would add to it."
                    ) from exc

                pause = max(RETRY_PAUSE_S, self._last_cost_s)
                logger.warning(
                    "%s %s on %s; waiting %.0fs before one more attempt",
                    self.base_url,
                    what,
                    path,
                    pause,
                )
                time.sleep(pause)
                continue
            except httpx2.RequestError as exc:
                raise Unavailable(f"cannot reach {self.base_url}: {exc}") from exc

            self._charge(started)
            return response

        raise AssertionError("unreachable")  # pragma: no cover

    @staticmethod
    def _json(response: httpx2.Response) -> Any:
        try:
            return response.json()
        except ValueError as exc:
            raise UpstreamError(
                f"Koha returned non-JSON on {response.request.url.path}"
            ) from exc

    def _raise_for_status(self, response: httpx2.Response, path: str) -> None:
        if response.status_code < 400:
            return

        body = self._safe_body(response)

        if response.status_code == 401:
            # TokenRefused, not Unauthorized: the credentials already bought
            # this token, so whatever is wrong is not what the operator typed.
            raise TokenRefused(
                f"Koha issued a token and then refused it on {path}: "
                f"{self._reason(body)}. The credentials are registered — this "
                "is the server's to fix."
            )
        if response.status_code == 403:
            # Koha names the missing grant. Passing it through is what turns a
            # dead end into an instruction for the library's administrator.
            required = body.get("required_permissions") if isinstance(body, dict) else None
            raise PermissionDenied(
                "the Koha API user lacks the permissions this call needs"
                + (f": {required}" if required else ""),
                required_permissions=required,
            )
        if response.status_code == 404:
            # Koha's own code for this is literally "not_found", and every path
            # this driver requests is one it constructed itself — so a 404 is a
            # missing resource, never a typo in a URL.
            raise NotFound(self._reason(body) or f"not found: {path}")
        if response.status_code == 406:
            raise UpstreamError(
                f"Koha refused the requested representation on {path} — "
                "the Accept header is wrong"
            )
        if response.status_code >= 500:
            raise Unavailable(f"Koha returned HTTP {response.status_code} on {path}")

        raise UpstreamError(
            f"unexpected HTTP {response.status_code} on {path}: {self._reason(body)}"
        )

    @staticmethod
    def _safe_body(response: httpx2.Response) -> Any:
        try:
            return response.json()
        except ValueError:
            return None

    @staticmethod
    def _reason(body: Any) -> str:
        if isinstance(body, dict):
            return str(body.get("error") or body.get("errors") or body)
        return "no detail"
