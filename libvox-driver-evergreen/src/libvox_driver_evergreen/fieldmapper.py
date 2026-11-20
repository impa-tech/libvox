"""Evergreen's fieldmapper objects, read by field name.

The OpenSRF gateway sends objects as **positional arrays**:
`{"__c": "bre", "__p": [null, null, "t", "2024-12-24T00:36:05-0600", ...]}`.
Which position holds which field is defined by the server's IDL, and nothing in
the payload says so.

**The positions are read from the server, never written down here.** The IDL is
generated from the installed version's schema, and a field added in a later
release shifts every position after it -- a hard-coded `__p[11]` for the MARC
would silently read the wrong column on the next upgrade. The IDL is public
(`/reports/fm_IDL.xml`), costs one request per driver, and is the server's own
statement of its shape.
"""

from __future__ import annotations

import re
from typing import Any

from libvox_driver_koha.errors import UpstreamError

IDL_PATH = "/reports/fm_IDL.xml"

# Attribute quoting varies between Evergreen's generator and hand-edited IDLs,
# so both quote styles are accepted.
_CLASS = r"<class\s[^>]*\bid=['\"]{cls}['\"][^>]*>(.*?)</class>"
_FIELD = re.compile(r"<field\s[^>]*\bname=['\"]([^'\"]+)['\"]")


def fields_from_idl(idl: str, cls: str) -> list[str]:
    """The field names of one IDL class, in positional order.

    Regular expressions rather than an XML parser, deliberately: this reads one
    flat list of attributes from a document the driver did not write, and a
    parser is the part that resolves entities. The fields are the ones before
    `<links>`; the names inside it repeat them.
    """
    match = re.search(_CLASS.format(cls=re.escape(cls)), idl, re.S)
    if not match:
        raise UpstreamError(f"the Evergreen IDL has no class {cls!r}")
    fields = _FIELD.findall(match.group(1).split("<links>")[0])
    if not fields:
        raise UpstreamError(f"the Evergreen IDL lists no fields for {cls!r}")
    return fields


def to_dict(obj: Any, fields: list[str], cls: str) -> dict[str, Any]:
    """One positional object as a dictionary keyed by field name."""
    if not isinstance(obj, dict) or obj.get("__c") != cls:
        raise UpstreamError(
            f"expected an Evergreen {cls!r} object, got {type(obj).__name__}"
        )
    values = obj.get("__p")
    if not isinstance(values, list):
        raise UpstreamError(f"an Evergreen {cls!r} object carried no fields")
    return dict(zip(fields, values))
