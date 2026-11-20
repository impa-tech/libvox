"""The invocation contract: JSON in, JSON out -- the same one as Koha's.

Request::

    {"action": "check_availability",
     "params": {"biblio_id": 1},
     "connection": {"base_url": "...", "client_id": "...", "client_secret": "..."}}

**The connection keys are the Koha driver's, on purpose.** Evergreen has no API
key: `client_id` carries the staff username and `client_secret` its password.
Keeping the names means the portal stores, and Core decrypts, every library's
connection the same way, whatever ILS is behind it; a third column for one ILS
would make the credential store know which ILS it holds.

The two properties /libvox-driver-koha states hold here unchanged: the driver
never learns which tenant it serves -- the connection allowlist turns a stray
`tenant_id` into a `bad_request` -- and nothing is held between invocations.
The session lives and dies inside `handle()`, and is logged out on the way.

Failure is a response, not an exception.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from libvox_driver_koha.errors import BadRequest, DriverError

from .driver import DEFAULT_SEARCH_LIMIT, EvergreenDriver

logger = logging.getLogger(__name__)

REDACTED = "<redacted>"

ALLOWED_CONNECTION_KEYS = {"base_url", "client_id", "client_secret", "timeout_s", "page_size"}


def handle(request: dict[str, Any]) -> dict[str, Any]:
    """Execute one driver invocation. Never raises."""
    action = request.get("action")

    try:
        handler = _ACTIONS.get(action) if isinstance(action, str) else None
        if handler is None:
            raise BadRequest(
                f"unknown action {action!r}; known actions: " + ", ".join(sorted(_ACTIONS))
            )

        connection = _connection(request)
        params = request.get("params") or {}
        if not isinstance(params, dict):
            raise BadRequest("params must be an object")

        with EvergreenDriver(
            connection["base_url"],
            username=connection["client_id"],
            password=connection["client_secret"],
            **{k: v for k, v in connection.items() if k in ("timeout_s", "page_size")},
        ) as driver:
            data = handler(driver, params)

        return {"ok": True, "action": action, "data": data}

    except DriverError as exc:
        logger.info("invocation failed: %s (%s)", exc.message, exc.code)
        return {"ok": False, "action": action, "error": exc.as_dict()}
    except Exception as exc:
        # Kept generic: an internal exception string can carry a URL or a
        # fragment of a credential, and this value travels back to the caller.
        logger.exception("unhandled error in driver invocation")
        return {
            "ok": False,
            "action": action,
            "error": {
                "code": "driver_error",
                "message": f"unhandled {type(exc).__name__} in the Evergreen driver",
            },
        }


def _record_id(params: dict[str, Any], action: str) -> int:
    biblio_id = params.get("biblio_id")
    if isinstance(biblio_id, str) and biblio_id.strip().isdigit():
        biblio_id = int(biblio_id)
    if not isinstance(biblio_id, int) or isinstance(biblio_id, bool):
        raise BadRequest(f"{action} needs an integer biblio_id, got {biblio_id!r}")
    return biblio_id


def _get_biblio(driver: EvergreenDriver, params: dict[str, Any]) -> dict[str, Any]:
    return driver.get_biblio(_record_id(params, "get_biblio")).as_dict()


def _check_availability(driver: EvergreenDriver, params: dict[str, Any]) -> dict[str, Any]:
    return driver.check_availability(_record_id(params, "check_availability")).as_dict()


def _search_by_title(driver: EvergreenDriver, params: dict[str, Any]) -> dict[str, Any]:
    title = params.get("title")
    if not isinstance(title, str):
        raise BadRequest(f"search_by_title needs a string title, got {title!r}")
    limit = params.get("limit", DEFAULT_SEARCH_LIMIT)
    if isinstance(limit, str) and limit.strip().isdigit():
        limit = int(limit)
    return driver.search_by_title(title, limit=limit).as_dict()


_ACTIONS: dict[str, Callable[[EvergreenDriver, dict[str, Any]], Any]] = {
    "get_biblio": _get_biblio,
    "check_availability": _check_availability,
    "search_by_title": _search_by_title,
}


def _connection(request: dict[str, Any]) -> dict[str, Any]:
    """Validate the connection block, refusing partial credentials and any key
    the allowlist does not name -- which is what rejects a tenant id."""
    connection = request.get("connection")
    if not isinstance(connection, dict):
        raise BadRequest("request needs a 'connection' object")

    missing = [k for k in ("base_url", "client_id", "client_secret") if not connection.get(k)]
    if missing:
        raise BadRequest(f"connection is missing: {', '.join(missing)}")

    unknown = set(connection) - ALLOWED_CONNECTION_KEYS
    if unknown:
        raise BadRequest(f"unknown connection keys: {', '.join(sorted(unknown))}")

    return dict(connection)


def redacted(request: dict[str, Any]) -> dict[str, Any]:
    """A copy of a request that is safe to log."""
    safe = dict(request)
    connection = safe.get("connection")
    if isinstance(connection, dict):
        safe["connection"] = {
            key: (REDACTED if key == "client_secret" else value)
            for key, value in connection.items()
        }
    return safe
