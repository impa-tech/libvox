"""Where a reader opens one record in an Evergreen OPAC.

Evergreen's public catalogue addresses a record as `/eg/opac/record/<id>`, with
the same id the driver indexes. Builds a string and makes no request.

A librarian may paste the OPAC's home page (`.../eg/opac/home`) as easily as
the server's address; both are accepted, and the record path is built from the
server root either way.
"""

from __future__ import annotations

import re

_OPAC_SUFFIX = re.compile(r"/eg/opac(?:/home)?/?$", re.IGNORECASE)


def record_url(opac_url: str, biblio_id: int) -> str:
    """The OPAC page for one bibliographic record.

    `biblio_id` is checked as an `int`: it comes out of a tool result, and
    anything else in a path is not a record number."""
    if not isinstance(biblio_id, int) or isinstance(biblio_id, bool):
        raise TypeError(f"biblio_id must be an int, got {type(biblio_id).__name__}")
    root = _OPAC_SUFFIX.sub("", opac_url.strip().rstrip("/"))
    return f"{root}/eg/opac/record/{biblio_id}"
