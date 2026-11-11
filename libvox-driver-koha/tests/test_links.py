"""The OPAC record page: Koha's shape, built without a request."""

from __future__ import annotations

import pytest

from libvox_driver_koha import record_url


def test_a_record_page_is_koha_opac_detail() -> None:
    assert (
        record_url("https://koha-opac-demo.ibict.br", 64)
        == "https://koha-opac-demo.ibict.br/cgi-bin/koha/opac-detail.pl?biblionumber=64"
    )


def test_a_trailing_slash_on_the_opac_address_is_not_doubled() -> None:
    assert record_url("http://localhost:8080/", 7) == (
        "http://localhost:8080/cgi-bin/koha/opac-detail.pl?biblionumber=7"
    )


@pytest.mark.parametrize("not_a_number", ["64", "64&x=1", 6.4, True, None])
def test_only_a_record_number_reaches_the_query_string(not_a_number) -> None:
    with pytest.raises(TypeError):
        record_url("http://localhost:8080", not_a_number)  # type: ignore[arg-type]
