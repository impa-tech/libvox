"""Shared test wiring.

One rule lives here: **a test that truncates must talk to a `_test` database,
never to a development one.** Both databases this module touches now have one.

For the **Tenant DB** the reason is that the damage is permanent. The
development copy holds a real registry — the libraries an operator created in
the panel, their widget keys, their ILS credentials — and those credentials are
ciphertext with no read path anywhere in the system. A TRUNCATE there means
every library hands over its API key again.

For the **Discovery Index** the reason is subtler, and it was got wrong first:
the index is derived data, so it looked safe to truncate and rebuild. That is
true at 436 sample records and false at half a million, where a rebuild is half
an hour of paginated requests against someone else's library server. A test run
must not be able to spend that. The symptom was worse than the cost, too —
`pytest` quietly emptying an index imported minutes earlier, and searches then
returning nothing with nothing to say why.

Both `docker/initdb/02-test-database.sql` files, here and in /libvox-portal,
create these databases for exactly this reason.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse, urlunparse


def as_test_database(dsn: str, *, suffix: str = "_test") -> str:
    """Point a DSN at its `_test` sibling.

    Derived rather than configured so there is no second environment variable
    to forget. An explicit `LIBVOX_TENANT_DB_TEST_DSN` still wins, for a setup
    where the test database is somewhere else entirely.
    """
    override = os.environ.get("LIBVOX_TENANT_DB_TEST_DSN")
    if override:
        return override

    parsed = urlparse(dsn)
    name = parsed.path.lstrip("/")
    if name.endswith(suffix):
        return dsn
    return urlunparse(parsed._replace(path=f"/{name}{suffix}"))
