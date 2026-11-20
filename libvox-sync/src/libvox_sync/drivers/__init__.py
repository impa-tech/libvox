"""ILS drivers: the only components that know what a specific ILS looks like.

The contract lives in `/libvox-driver-koha`, not here. Koha knowledge used to
be duplicated in this package — a second `BiblioRecord`, a second normalizer,
a second set of pagination rules — and two definitions of what a title is
drift the moment one of them is edited. The live path and the bulk path have
to agree about the same book, so they read the same code.

Re-exported rather than imported directly at each call site so that the day a
`libvox-common` package holds the contract, this module is the only edit.

`from_credentials` is the one place that maps a library's `ils_type` to a
driver. Sync and Core both build drivers from stored credentials, and before it
existed each had its own `if ils_type != "koha"`; a second ILS would have meant
finding every one of them.
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

ILS_TYPES = ("koha", "evergreen")
"""The ILS types a driver exists for. Kept equal to the portal's `IlsType` enum
and to the CHECK on `tenant_ils.ils_type`: a type offered there with no driver
here is a library that can be configured and never synced."""


class NoDriver(DriverError):
    """A library runs an ILS LibVox has no driver for."""

    code = "no_driver"


def from_credentials(credentials, *, page_size: int | None = None) -> Driver:
    """The driver for one library's stored connection.

    The stored columns mean the same thing for every ILS -- `client_id` is
    Koha's OAuth client and Evergreen's staff username, `client_secret` the
    matching secret or password -- so the store never has to know which ILS it
    holds, and only this function does.

    The Evergreen driver is imported here, not at the top: a process that never
    meets an Evergreen library does not pay for loading it.
    """
    options = {} if page_size is None else {"page_size": page_size}
    if credentials.ils_type == "koha":
        return KohaDriver(
            credentials.base_url,
            client_id=credentials.client_id,
            client_secret=credentials.client_secret,
            **options,
        )
    if credentials.ils_type == "evergreen":
        from libvox_driver_evergreen import EvergreenDriver

        return EvergreenDriver(
            credentials.base_url,
            username=credentials.client_id,
            password=credentials.client_secret,
            **options,
        )
    raise NoDriver(f"LibVox has no driver for {credentials.ils_type}.")


__all__ = [
    "ILS_TYPES",
    "NoDriver",
    "from_credentials",
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
