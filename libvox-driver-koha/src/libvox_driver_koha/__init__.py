"""LibVox Koha driver.

The only component that knows what Koha looks like. Everything downstream —
`/libvox-sync`, and eventually `/libvox-core` — deals in `BiblioRecord` and
never in MARC.

Two ways in, for two kinds of caller:

* Python, for a process that holds the driver across many calls::

      from libvox_driver_koha import KohaDriver, iter_records

      with KohaDriver(url, client_id, secret) as driver:
          record = driver.get_biblio(1)
          for record in iter_records(driver):
              ...

* The invocation contract, for a caller that sends JSON and expects JSON::

      from libvox_driver_koha import handle

      handle({"action": "get_biblio",
              "params": {"biblio_id": 1},
              "connection": {...}})
"""

from __future__ import annotations

from .contract import (
    Availability,
    AvailabilityStatus,
    BiblioRecord,
    Driver,
    Page,
    SearchResult,
    iter_records,
)
from .driver import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    KohaDriver,
    escape_like,
)
from .errors import (
    BadRequest,
    DriverError,
    NotFound,
    PermissionDenied,
    Suppressed,
    TokenRefused,
    Unauthorized,
    Unavailable,
    UnusableRecord,
    UpstreamError,
)
from .handler import handle, redacted
from .normalize import availability_from_items, item_state, normalize, normalize_page

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "Availability",
    "AvailabilityStatus",
    "BadRequest",
    "BiblioRecord",
    "Driver",
    "DriverError",
    "KohaDriver",
    "NotFound",
    "Page",
    "PermissionDenied",
    "SearchResult",
    "Suppressed",
    "TokenRefused",
    "Unauthorized",
    "Unavailable",
    "UnusableRecord",
    "UpstreamError",
    "availability_from_items",
    "escape_like",
    "handle",
    "item_state",
    "iter_records",
    "normalize",
    "normalize_page",
    "redacted",
]
