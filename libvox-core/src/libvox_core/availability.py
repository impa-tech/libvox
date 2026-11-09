"""Live circulation status, for ids the library actually holds.

Two things happen here in a fixed order, and the order is the security
property.

**First, propagation rule 5.** `check_availability` rejects `biblio_ids` absent
from the calling tenant's index. The tool description tells the model to only
pass ids a search returned, but a description is advice and this is
enforcement: an id the model hallucinated, or one a patron injected through the
conversation, is dropped here rather than becoming a request against a
library's server. The index is the allowlist.

**Then the credential, per call.** Decrypted for this invocation, handed to a
driver, and the driver closed afterwards. Nothing is held between calls, which
is what makes it impossible for one library's token to end up on another
library's request.
"""

from __future__ import annotations

import logging

import psycopg

from .config import Settings

logger = logging.getLogger(__name__)


class AvailabilityUnknown(RuntimeError):
    """The ILS could not be asked, so nothing can be said about these copies.

    Deliberately distinct from an empty result. "The library holds none of
    these" and "we could not find out" are different answers, and the contract
    -- a list of records, with the ones a library does not hold omitted -- has
    no way to say the second. An empty list reaching the model as an answer is
    read as the first, and stated to a patron with confidence.

    That is not hypothetical: with a base_url the container could not reach,
    every lookup failed, the list came back empty, and MARC.IA told someone the
    library had no copies of two books it holds.
    """


def known_biblio_ids(
    conn: psycopg.Connection, tenant_id: str, biblio_ids: list[int]
) -> set[int]:
    """Which of these ids exist in this library's live generation.

    Inside a tenant scope, so row-level security is doing the work rather than
    a WHERE clause somebody has to remember. `discovery_active` already hides
    generations, so an id in a half-written import is correctly not yet known.
    """
    from libvox_sync.db import tenant_scope

    if not biblio_ids:
        return set()

    with tenant_scope(conn, tenant_id) as scoped, scoped.cursor() as cur:
        cur.execute(
            "SELECT biblio_id FROM discovery_active WHERE biblio_id = ANY(%s)",
            (list(biblio_ids),),
        )
        return {row[0] for row in cur.fetchall()}


def availability_for(
    settings: Settings, tenant_id: str, biblio_ids: list[int]
) -> list[dict]:
    """Ask the library's ILS about the ids it actually has.

    Returns one entry per record the library holds. Records it does not hold
    are **omitted**, never padded with an `unavailable` placeholder: "we do not
    have that" and "we have it and it is out" are different answers, and a
    placeholder would state the second when the first is true.
    """
    from libvox_driver_koha import DriverError, KohaDriver
    from libvox_sync.credentials import fetch
    from libvox_sync.db import tenant_scope

    # Duplicates are possible -- the MCP schema bounds the list at 20 and does
    # not enforce uniqueness -- and asking a library's server the same question
    # twice in one call is rude for no gain.
    requested = list(dict.fromkeys(biblio_ids))

    with psycopg.connect(settings.discovery_dsn) as conn:
        known = known_biblio_ids(conn, tenant_id, requested)

    dropped = [b for b in requested if b not in known]
    if dropped:
        # Logged, not returned. The caller learns nothing about which ids exist
        # in a library it did not already have ids for.
        logger.warning(
            "%s: dropped %d id(s) absent from the index before reaching the ILS",
            tenant_id,
            len(dropped),
        )

    wanted = [b for b in requested if b in known]
    if not wanted:
        return []

    crypto_key = settings.require_crypto_key()
    with psycopg.connect(settings.tenant_dsn) as conn, tenant_scope(
        conn, tenant_id
    ) as scoped:
        credentials = fetch(scoped, tenant_id, crypto_key=crypto_key)

    # Both of these used to return an empty list, and both were the same
    # mistake as swallowing a driver failure: a library that has not connected
    # its catalog, or runs an ILS with no driver, cannot answer -- which is not
    # the same as answering that it holds nothing.
    if credentials is None:
        logger.warning("%s has no ILS configured; availability cannot be asked", tenant_id)
        raise AvailabilityUnknown(f"{tenant_id} has no catalog connected")
    if credentials.ils_type != "koha":
        logger.warning(
            "%s runs %s, which has no driver", tenant_id, credentials.ils_type
        )
        raise AvailabilityUnknown(f"no driver for {credentials.ils_type}")

    records: list[dict] = []
    failed: list[int] = []
    driver = KohaDriver(
        credentials.base_url,
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
    )
    try:
        for biblio_id in wanted:
            try:
                result = driver.check_availability(biblio_id)
            except DriverError as exc:
                # The reason never crosses into the answer -- a 403 or an ILS
                # URL belongs in the operator's log. But the *fact* has to,
                # because silently dropping it turns "we could not ask" into
                # "the library has none", which is the confidently wrong answer
                # this whole architecture exists to prevent.
                logger.warning(
                    "%s: availability failed for %d (%s)", tenant_id, biblio_id, exc.code
                )
                failed.append(biblio_id)
                continue

            records.append(
                {
                    "biblio_id": result.biblio_id,
                    "status": result.status,
                    "copies_total": result.copies_total,
                    "copies_available": result.copies_available,
                    "copies_reference_only": result.copies_reference_only,
                    "library": result.library,
                    "shelf": result.shelf,
                    "call_number": result.call_number,
                    "due_date": result.due_date,
                }
            )
    finally:
        # Stateless by contract: the driver holds an OAuth token for its own
        # lifetime and no longer, so closing it here is what keeps one
        # library's bearer from reaching another library's request.
        driver.close()

    if failed:
        # Any failure fails the call, not just a total one. A partial list
        # presented as complete is the same error one record at a time: the
        # model would say "we have two of the three" about a third it was never
        # told went missing. The result type carries records and nothing else,
        # so there is no honest way to report a hole in it.
        raise AvailabilityUnknown(
            f"could not reach the catalog for {len(failed)} of "
            f"{len(wanted)} record(s)"
        )

    return records
