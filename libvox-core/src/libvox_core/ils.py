"""Does this library's catalogue connection actually work?

A librarian types a URL, a client id and a secret into `/libvox-portal` and has
no way to find out whether any of it is right until MARC.IA answers a reader
badly, or an import fails hours later. This answers it in a few seconds.

**Why it lives here and not in the portal.** The portal cannot decrypt: its
credential store has `put`, `updateConnection`, `describe` and `forget` and no
read path, and two tests hold that line -- one of them tokenizes every PHP file
looking for `pgp_sym_decrypt`. Making the check the portal's job would mean
undoing the property that keeps a decrypted ILS secret out of the web
application. So the portal asks the component the architecture already names as
the one that may decrypt.

**The answer is for the librarian, not for a patron.** It names her own server,
the status it returned, and which Koha permission is missing, because those are
the only sentences that fix anything and she is the person who can act on them.
The rule that collapses this detail protects a member of the public reading a
chat window; applying it to the owner of the server would be the same mistake
in the other direction.
"""

from __future__ import annotations

import logging

import psycopg

from .config import Settings
from .contracts import IlsCheckResult

logger = logging.getLogger(__name__)

# One record. Enough to prove the credential authenticates *and* that it can
# read the catalogue -- the two failures a librarian actually has -- without
# pulling a page of a hundred to find out.
PROBE_SIZE = 1


def check(settings: Settings, tenant_id: str) -> IlsCheckResult:
    """Try the handshake, and say what happened in a sentence."""
    from libvox_driver_koha import DriverError, KohaDriver
    from libvox_sync.credentials import fetch
    from libvox_sync.db import tenant_scope

    crypto_key = settings.require_crypto_key()

    with psycopg.connect(settings.tenant_dsn) as conn, tenant_scope(
        conn, tenant_id
    ) as scoped:
        credentials = fetch(scoped, tenant_id, crypto_key=crypto_key)

    if credentials is None:
        return IlsCheckResult(
            ok=False,
            problem="not_configured",
            detail="No catalogue connection has been saved for this library yet.",
        )

    if credentials.ils_type != "koha":
        return IlsCheckResult(
            ok=False,
            problem="no_driver",
            detail=f"LibVox has no driver for {credentials.ils_type}.",
        )

    driver = KohaDriver(
        credentials.base_url,
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
        page_size=PROBE_SIZE,
    )
    try:
        page = driver.fetch_page(None)
    except DriverError as exc:
        # The driver's own taxonomy, which distinguishes the three things that
        # actually go wrong: the address is unreachable, the credential is
        # rejected, or the credential is fine and lacks a permission. Telling
        # them apart is the whole value of the button -- "it did not work" sends
        # a librarian to check all three.
        logger.info("%s: connection check failed (%s)", tenant_id, exc.code)
        return IlsCheckResult(ok=False, problem=exc.code, detail=exc.message)
    except Exception as exc:
        # Blind on purpose: this is a diagnostic, and every unexpected failure
        # has the same right answer -- say so, rather than return a 500 that
        # the panel renders as "could not reach LibVox" and sends her looking
        # in the wrong place entirely.
        logger.warning("%s: connection check raised", tenant_id, exc_info=True)
        return IlsCheckResult(ok=False, problem="unexpected", detail=str(exc))
    finally:
        driver.close()

    logger.info("%s: connection check passed", tenant_id)
    return IlsCheckResult(
        ok=True,
        problem=None,
        detail=None,
        # Read rather than counted: a catalogue that authenticates and returns
        # nothing is the failure this system is most careful about -- it is how
        # a library ends up telling readers it holds no books.
        records_seen=len(page.records),
    )
