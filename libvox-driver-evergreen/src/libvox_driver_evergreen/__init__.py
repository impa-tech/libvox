"""LibVox Evergreen driver.

The only component that knows what Evergreen looks like: its login, its REST
API and its OpenSRF gateway, its MARCXML and its copy statuses. Everything
downstream deals in the same `BiblioRecord` and `Availability` the Koha driver
produces.

    from libvox_driver_evergreen import EvergreenDriver

    with EvergreenDriver(url, username, password) as driver:
        driver.check_availability(1)
"""

from __future__ import annotations

from .driver import (
    DEFAULT_PAGE_SIZE,
    DEFAULT_SEARCH_LIMIT,
    MAX_SEARCH_LIMIT,
    EvergreenDriver,
)
from .handler import handle, redacted
from .links import record_url
from .normalize import availability_from_copies, copy_state, normalize, normalize_page

__version__ = "0.1.0"

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "DEFAULT_SEARCH_LIMIT",
    "MAX_SEARCH_LIMIT",
    "EvergreenDriver",
    "availability_from_copies",
    "copy_state",
    "handle",
    "normalize",
    "normalize_page",
    "record_url",
    "redacted",
]
