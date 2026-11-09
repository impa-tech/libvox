"""Failure modes a caller has to tell apart.

A conversational layer cannot collapse these. "I couldn't find that record",
"the library's system is down" and "this library's key has been revoked" send
the patron, the librarian and the operator to three different places. A single
`DriverError` would make the assistant guess, and it would guess wrong.

Every error carries a stable `code`, which is what crosses the invocation
boundary — English prose is for logs, not for the caller to match on.
"""

from __future__ import annotations


class DriverError(Exception):
    """Base class. `code` is the contract; the message is for humans."""

    code = "driver_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def as_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {"code": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


# --- the record ------------------------------------------------------------


class NotFound(DriverError):
    """No record with that id in this catalog."""

    code = "not_found"


class Suppressed(DriverError):
    """The record exists but the library hides it from its own OPAC.

    Deliberately distinct from `NotFound` so the library can diagnose a missing
    result. Callers should still show the patron the same thing they show for
    `not_found`: revealing that a hidden record exists leaks precisely what the
    suppression flag was set to hide.
    """

    code = "suppressed"


class UnusableRecord(DriverError):
    """Present, but too malformed to normalize — no id, or no title."""

    code = "unusable_record"


# --- the credential --------------------------------------------------------


class Unauthorized(DriverError):
    """The ILS rejected the credential: wrong, expired or revoked.

    The operator's signal to re-issue. Distinct from `PermissionDenied`, which
    means the credential is fine and the grant is not.
    """

    code = "unauthorized"


class TokenRefused(Unauthorized):
    """The credentials were accepted, and the token they bought was not.

    A different failure from `Unauthorized`, with a different fix, which is why
    it has its own code. Koha mints a token from `api_keys` and resolves the
    patron on the *next* request, so a key can be valid while every call made
    with it comes back 401 — seen on a live demo server where bogus credentials
    answered 403 and real ones answered 200, and then every request with the
    token answered "Authentication failure."

    Telling someone to re-check an id and a secret that are demonstrably right
    sends them looking at the one thing that is not wrong. This one is the
    server's to fix, and the cause found in the field was **Koha running under
    CGI**: the token lives server-side, and under CGI every request is a fresh
    process, so whichever one minted it dies with its response and the next
    looks somewhere empty. Koha's own staff interface warns about the mode, and
    `koha-plack --enable` is the remedy. An expired patron behind the key, or
    one with no `userid`, produce the same 401.

    Subclasses `Unauthorized` on purpose: anything already handling that keeps
    working, and only callers that care about the difference need to look.
    """

    code = "token_refused"


class PermissionDenied(DriverError):
    """Authenticated, but the API user lacks the required permission.

    Koha reports which one in the body. Passing that through turns a dead end
    into an instruction: the library grants `catalogue` and it works. Observed
    on a live instance, where an API key whose patron held the `catalogue` flag
    still failed because that patron had no `userid` for Koha to resolve.
    """

    code = "permission_denied"


# --- the connection --------------------------------------------------------


class Unavailable(DriverError):
    """The ILS could not be reached, or answered 5xx. Retryable."""

    code = "upstream_unavailable"


class UpstreamError(DriverError):
    """The ILS answered, but with something this driver cannot interpret."""

    code = "upstream_error"


# --- the request itself ----------------------------------------------------


class BadRequest(DriverError):
    """The invocation was malformed — unknown action, missing parameter."""

    code = "bad_request"
