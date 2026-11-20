"""Which driver a library's stored connection gets.

`from_credentials` is the single map from `ils_type` to a driver, shared by
Sync and Core. A type with no driver must fail as `no_driver`, which the
portal's connection check already knows how to explain to a librarian.
"""

from __future__ import annotations

import pytest

from libvox_sync.credentials import ILSCredentials
from libvox_sync.drivers import ILS_TYPES, NoDriver, from_credentials


def credentials(ils_type: str) -> ILSCredentials:
    return ILSCredentials(
        tenant_id="lib-a",
        ils_type=ils_type,
        base_url="https://ils.example",
        client_id="libvox",
        client_secret="not-a-real-secret",
    )


def test_a_koha_library_gets_the_koha_driver() -> None:
    driver = from_credentials(credentials("koha"), page_size=7)
    try:
        assert driver.name == "koha"
    finally:
        driver.close()


def test_an_evergreen_library_gets_the_evergreen_driver() -> None:
    """The stored client id is the staff username, the secret its password."""
    driver = from_credentials(credentials("evergreen"), page_size=7)
    try:
        assert driver.name == "evergreen"
        assert driver._page_size == 7
        assert "libvox" in repr(driver._client)
        assert "not-a-real-secret" not in repr(driver._client)
    finally:
        driver.close()


def test_an_ils_with_no_driver_says_so() -> None:
    with pytest.raises(NoDriver) as caught:
        from_credentials(credentials("folio"))
    assert caught.value.code == "no_driver"


def test_the_types_are_the_ones_with_drivers() -> None:
    assert ILS_TYPES == ("koha", "evergreen")
