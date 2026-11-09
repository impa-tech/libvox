"""The invocation contract: JSON in, JSON out.

This is the seam between the container version and the serverless one. Today
the CLI calls `handle()`; tomorrow a Lambda or Knative entry point calls the
same function with the same dictionary. Moving to serverless is then an
adapter, not a rewrite.

Two properties are worth stating because they are easy to lose later:

**The driver never learns which tenant it is serving.** A request carries a
*connection* — a URL and a credential — not an identity. There is nothing in
this module to correlate two invocations, and nothing a caller could pass that
would let it. Tenant identity stays in `/libvox-core`, which is what keeps it
out of anything the model can read.

**Nothing is held between invocations.** The client, and therefore the access
token, lives and dies inside `handle()`. A single lookup costs one extra
request to authenticate; a cache that survived the call is where one library's
bearer token ends up on another library's request.

Request::

    {"action": "get_biblio",
     "params": {"biblio_id": 1},
     "connection": {"base_url": "...", "client_id": "...", "client_secret": "..."}}

Response::

    {"ok": true,  "action": "get_biblio", "data": {...}}
    {"ok": false, "action": "get_biblio", "error": {"code": "not_found", ...}}

Failure is a response, not an exception. A driver that raised across the
invocation boundary would surface as a 500 with a stack trace, and the caller
would lose the distinction between "no such book" and "the library is down".
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .contract import BiblioRecord
from .driver import DEFAULT_SEARCH_LIMIT, KohaDriver
from .errors import BadRequest, DriverError

logger = logging.getLogger(__name__)

REDACTED = "<redacted>"


def handle(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one driver invocation. Never raises."""
    action = request.get("action")

    try:
        handler = _ACTIONS.get(action) if isinstance(action, str) else None
        if handler is None:
            raise BadRequest(
                f"unknown action {action!r}; known actions: "
                + ", ".join(sorted(_ACTIONS))
            )

        connection = _connection(request)
        params = request.get("params") or {}
        if not isinstance(params, dict):
            raise BadRequest("params must be an object")

        with KohaDriver(**connection) as driver:
            data = handler(driver, params)

        return {"ok": True, "action": action, "data": data}

    except DriverError as exc:
        logger.info("invocation failed: %s (%s)", exc.message, exc.code)
        return {"ok": False, "action": action, "error": exc.as_dict()}
    except Exception as exc:
        # An unexpected failure still has to cross as data. The message is kept
        # generic on purpose: an internal exception string can carry a URL or a
        # fragment of a credential, and this value travels back to the caller.
        logger.exception("unhandled error in driver invocation")
        return {
            "ok": False,
            "action": action,
            "error": {
                "code": "driver_error",
                "message": f"unhandled {type(exc).__name__} in the Koha driver",
            },
        }


# --- actions ----------------------------------------------------------------


def _get_biblio(driver: KohaDriver, params: dict[str, Any]) -> dict[str, Any]:
    biblio_id = params.get("biblio_id")
    if isinstance(biblio_id, str) and biblio_id.strip().isdigit():
        biblio_id = int(biblio_id)
    if not isinstance(biblio_id, int) or isinstance(biblio_id, bool):
        raise BadRequest(f"get_biblio needs an integer biblio_id, got {biblio_id!r}")

    record: BiblioRecord = driver.get_biblio(biblio_id)
    return record.as_dict()


def _check_availability(driver: KohaDriver, params: dict[str, Any]) -> dict[str, Any]:
    biblio_id = params.get("biblio_id")
    if isinstance(biblio_id, str) and biblio_id.strip().isdigit():
        biblio_id = int(biblio_id)
    if not isinstance(biblio_id, int) or isinstance(biblio_id, bool):
        raise BadRequest(
            f"check_availability needs an integer biblio_id, got {biblio_id!r}"
        )

    return driver.check_availability(biblio_id).as_dict()


def _search_by_title(driver: KohaDriver, params: dict[str, Any]) -> dict[str, Any]:
    title = params.get("title")
    if not isinstance(title, str):
        raise BadRequest(f"search_by_title needs a string title, got {title!r}")

    limit = params.get("limit", DEFAULT_SEARCH_LIMIT)
    if isinstance(limit, str) and limit.strip().isdigit():
        limit = int(limit)

    return driver.search_by_title(title, limit=limit).as_dict()


_ACTIONS: dict[str, Callable[[KohaDriver, dict[str, Any]], Any]] = {
    "get_biblio": _get_biblio,
    "check_availability": _check_availability,
    "search_by_title": _search_by_title,
}


# --- connection --------------------------------------------------------------


def _connection(request: dict[str, Any]) -> dict[str, Any]:
    """Validate the connection block, refusing partial credentials.

    Deliberately strict. A missing secret that reached the ILS as an empty
    string would come back as an authentication failure, and the operator would
    go looking at the library's key instead of at the caller.
    """
    connection = request.get("connection")
    if not isinstance(connection, dict):
        raise BadRequest("request needs a 'connection' object")

    missing = [
        key
        for key in ("base_url", "client_id", "client_secret")
        if not connection.get(key)
    ]
    if missing:
        raise BadRequest(f"connection is missing: {', '.join(missing)}")

    allowed = {"base_url", "client_id", "client_secret", "timeout_s", "page_size"}
    unknown = set(connection) - allowed
    if unknown:
        raise BadRequest(f"unknown connection keys: {', '.join(sorted(unknown))}")

    return dict(connection)


def redacted(request: dict[str, Any]) -> dict[str, Any]:
    """A copy of a request that is safe to log.

    Anything that logs an invocation must go through this. The rule is easier
    to keep when there is exactly one function to reach for.
    """
    safe = dict(request)
    connection = safe.get("connection")
    if isinstance(connection, dict):
        safe["connection"] = {
            key: (REDACTED if key == "client_secret" else value)
            for key, value in connection.items()
        }
    return safe
