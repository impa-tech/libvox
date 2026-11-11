"""Where a reader opens one record in a Koha OPAC.

The shape of this address is Koha's and nobody else's -- `opac-detail.pl`,
keyed by `biblionumber` -- which is why it lives in the driver. Core asks for
"the record page of biblio 64 at this OPAC" without knowing what that looks
like, and the next driver answers the same question with its own path.

No request is made. This builds a string from two values the library and the
index already hold, so it needs no credential and cannot fail on a network.
The OPAC address is the librarian's, from the portal, and is not the API
address the driver talks to: Koha serves the two on different hosts.
"""

from __future__ import annotations

from urllib.parse import urlencode

_DETAIL_PATH = "/cgi-bin/koha/opac-detail.pl"


def record_url(opac_url: str, biblio_id: int) -> str:
    """The OPAC page for one bibliographic record.

    `biblio_id` is an `int` and is checked as one: it comes out of a tool
    result, and anything else in a query string is not a record number.
    """
    if not isinstance(biblio_id, int) or isinstance(biblio_id, bool):
        raise TypeError(f"biblio_id must be an int, got {type(biblio_id).__name__}")
    query = urlencode({"biblionumber": biblio_id})
    return f"{opac_url.rstrip('/')}{_DETAIL_PATH}?{query}"
