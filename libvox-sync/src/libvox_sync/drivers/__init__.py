"""ILS drivers: the only components that know what a specific ILS looks like.

The contract lives in `/libvox-driver-koha`, not here. Koha knowledge used to
be duplicated in this package — a second `BiblioRecord`, a second normalizer,
a second set of pagination rules — and two definitions of what a title is
drift the moment one of them is edited. The live path and the bulk path have
to agree about the same book, so they read the same code.

Re-exported rather than imported directly at each call site so that the day a
`libvox-common` package holds the contract, this module is the only edit.
"""

from __future__ import annotations

from libvox_driver_koha import (
    BiblioRecord,
    Driver,
    DriverError,
    KohaDriver,
    Page,
    PermissionDenied,
    Suppressed,
    Unauthorized,
    Unavailable,
    UnusableRecord,
    UpstreamError,
    iter_records,
    normalize_page,
)

__all__ = [
    "BiblioRecord",
    "Driver",
    "DriverError",
    "KohaDriver",
    "Page",
    "PermissionDenied",
    "Suppressed",
    "Unauthorized",
    "Unavailable",
    "UnusableRecord",
    "UpstreamError",
    "iter_records",
    "normalize_page",
]
