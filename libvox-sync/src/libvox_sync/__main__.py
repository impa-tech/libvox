"""CLI: `python -m libvox_sync`.

Schema management, a read-only extraction probe, and the full import that
populates the Discovery Index.

`incremental` is still absent rather than stubbed, so `--help` never advertises
something that does nothing. It is blocked upstream — see the README.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys

from libvox_driver_koha import DEFAULT_PAGE_SIZE

from .config import Settings
from .db import (
    apply_migrations,
    connect,
    connect_discovery,
    connect_tenant,
    tenant_scope,
)
from .drivers import DriverError, KohaDriver, iter_records
from .embedding import Embedder, EmbeddingSettings
from .pipeline import (
    DEFAULT_BATCH_SIZE,
    ImportResult,
    SyncAlreadyRunning,
    full_import,
)
from .registry import syncable_tenants
from .search import EmptySearch, search

logger = logging.getLogger("libvox_sync")


def cmd_migrate(settings: Settings, _args: argparse.Namespace) -> int:
    # Migrations need the admin role; everything else runs unprivileged so
    # row-level security is in force. This never touches the Tenant DB: that
    # schema is owned and migrated by /libvox-portal.
    with connect(
        settings.admin_dsn, statement_timeout_ms=settings.statement_timeout_ms
    ) as conn:
        applied = apply_migrations(conn)
    if applied:
        print(f"applied migrations: {', '.join(str(v) for v in applied)}")
    else:
        print("schema already up to date")
    return 0


def cmd_status(settings: Settings, args: argparse.Namespace) -> int:
    # Index state, so the Discovery Index.
    with (
        connect_discovery(settings) as conn,
        tenant_scope(conn, args.tenant) as scoped,
        scoped.cursor() as cur,
    ):
        cur.execute(
            "SELECT index_status, active_generation, last_sync_at, last_error "
            "FROM discovery_tenant WHERE tenant_id = %s",
            (args.tenant,),
        )
        row = cur.fetchone()
        if row is None:
            print(f"{args.tenant}: unknown tenant (never synced)")
            return 1

        cur.execute("SELECT count(*) FROM discovery_active")
        (records,) = cur.fetchone()

    status, generation, last_sync, last_error = row
    print(f"tenant:      {args.tenant}")
    print(f"status:      {status}")
    print(f"generation:  {generation if generation is not None else '-'}")
    print(f"records:     {records}")
    print(f"last sync:   {last_sync or '-'}")
    if last_error:
        print(f"last error:  {last_error}")
    return 0


def _build_koha(args: argparse.Namespace):
    return KohaDriver(
        os.environ["KOHA_URL"],
        client_id=os.environ["KOHA_CLIENT_ID"],
        client_secret=os.environ["KOHA_CLIENT_SECRET"],
        page_size=args.page_size,
    )


def _build_fixture(args: argparse.Namespace):
    from .drivers.fixture import FixtureDriver

    if not args.fixture:
        raise SystemExit("--ils fixture requires --fixture PATH")
    return FixtureDriver(args.fixture, page_size=args.page_size)


# Which ILS a tenant runs, and how to reach it, is tenant configuration. In the
# finished system it lives in the Tenant DB and Core resolves it; standalone
# mode substitutes a flag and environment variables.
#
# Only implemented drivers are listed. Adding 'folio' here before a FOLIO
# driver exists would put a choice in --help that cannot work.
DRIVERS: dict[str, tuple[tuple[str, ...], object]] = {
    "koha": (("KOHA_URL", "KOHA_CLIENT_ID", "KOHA_CLIENT_SECRET"), _build_koha),
    "fixture": ((), _build_fixture),
}


def _koha_driver(credentials, page_size: int):
    return KohaDriver(
        credentials.base_url,
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
        page_size=page_size,
    )


# Keyed by the `ils_type` stored in the Tenant DB, which the portal constrains
# to the same set. Adding 'folio' here before a FOLIO driver exists would make
# a library configurable that can never sync — the same reason DRIVERS below
# lists only what is implemented.
BY_ILS_TYPE = {"koha": _koha_driver}


def _driver_from_tenant(settings: Settings, tenant_id: str, page_size: int):
    """Build a driver from credentials stored in the database.

    This is the shape the worker uses: the job names a library, and the
    connection details are resolved from that library's configuration. The
    difference later is only *who* resolves them — Core, not this process.
    """
    from .credentials import CredentialError, fetch

    crypto_key = settings.require_crypto_key()

    try:
        # The Tenant DB, not the index: credentials moved there when
        # /libvox-portal took ownership of them.
        with connect_tenant(settings) as conn, tenant_scope(conn, tenant_id) as scoped:
            credentials = fetch(scoped, tenant_id, crypto_key=crypto_key)
    except CredentialError as exc:
        raise SystemExit(str(exc)) from None

    if credentials is None:
        raise SystemExit(
            f"no ILS configured for tenant {tenant_id!r}; "
            "configure it in /libvox-portal, or run `libvox-sync tenant set`"
        )

    try:
        build = BY_ILS_TYPE[credentials.ils_type]
    except KeyError:
        raise SystemExit(
            f"no driver for ILS type {credentials.ils_type!r}; "
            f"available: {', '.join(sorted(BY_ILS_TYPE))}"
        ) from None

    return build(credentials, page_size)


def _build_driver(args: argparse.Namespace):
    """Resolve --ils to a driver, failing loudly on missing credentials."""
    try:
        required_env, build = DRIVERS[args.ils]
    except KeyError:
        raise SystemExit(
            f"unknown ILS {args.ils!r}; available: {', '.join(sorted(DRIVERS))}"
        ) from None

    missing = [name for name in required_env if not os.environ.get(name)]
    if missing:
        raise SystemExit(
            f"--ils {args.ils} needs these environment variables: "
            + ", ".join(missing)
            + "\nSet them in the environment — never on the command line, where "
            "/proc/<pid>/cmdline exposes them to any local process. To run "
            "offline instead: --ils fixture --fixture tests/koha_example.json"
        )

    return build(args)


def _read_secret(env_var: str) -> str:
    """Take a secret from the environment, or prompt for it.

    Never from argv: /proc/<pid>/cmdline exposes arguments to any local
    process, and shell history keeps them afterwards.
    """
    secret = os.environ.get(env_var)
    if secret:
        return secret

    import getpass

    secret = getpass.getpass(f"{env_var} (input hidden): ")
    if not secret:
        raise SystemExit(f"no secret provided; set {env_var} or type it when asked")
    return secret


def cmd_tenant_set(settings: Settings, args: argparse.Namespace) -> int:
    """Store one tenant's ILS credentials, encrypted."""
    from .credentials import ILSCredentials, store

    crypto_key = settings.require_crypto_key()
    secret = _read_secret("KOHA_CLIENT_SECRET")

    credentials = ILSCredentials(
        tenant_id=args.tenant,
        ils_type=args.ils,
        base_url=args.url,
        client_id=args.client_id,
        client_secret=secret,
    )

    with connect_tenant(settings) as conn, tenant_scope(conn, args.tenant) as scoped:
        store(scoped, credentials, crypto_key=crypto_key)

    print(f"stored {args.ils} credentials for {args.tenant} (secret encrypted)")
    return 0


def cmd_tenant_list(settings: Settings, _args: argparse.Namespace) -> int:
    """Inventory of configured libraries. Cannot return a secret.

    This used to need the admin DSN: the inventory spans libraries, and
    `tenant_ils` carries row-level security, so listing it meant a role that
    bypasses the policy. The registry made that unnecessary — `tenants` has no
    policy, and each library's connection details are then read inside its own
    scope. The superuser connection is out of the normal operating path.
    """
    from .registry import describe

    with connect_tenant(settings) as conn:
        tenants = syncable_tenants(conn)
        rows = [(t, describe(conn, t.tenant_id)) for t in tenants]

    if not rows:
        print("no libraries have configured an ILS")
        return 0
    for tenant, connection in rows:
        ils_type, base_url = connection or ("—", "—")
        print(f"{tenant.tenant_id:<20} {tenant.status:<14} {ils_type:<8} {base_url}")
    return 0


def _driver_failure(exc: DriverError) -> str:
    """Turn a driver error into something the operator can act on.

    Koha answers a permission problem with the flag it wanted, and passing that
    through is the difference between a dead end and an instruction. The
    repository's field notes record hours lost to exactly this: the API user
    needs `catalogue`, and the error said only "403".

    LibVox never writes to an ILS, so `catalogue` is the whole grant. Granting
    `circulate` or `superlibrarian` to make this message go away hands over
    write access the platform will never use.
    """
    lines = [f"{exc.code}: {exc.message}"]

    required = exc.details.get("required_permissions")
    if required:
        lines.append(f"Koha reports the API user needs: {required}")
        lines.append(
            "Grant only that. LibVox reads catalogs and never writes to one, "
            "so `circulate` and `superlibrarian` are not the fix."
        )
    return "\n".join(lines)


def cmd_extract(settings: Settings, args: argparse.Namespace) -> int:
    """Extract and normalize, printing the result. Writes nothing.

    A dry run: it exercises the driver, the pagination contract, and every
    normalization rule against a real catalog, without touching the Discovery
    Index. Useful for eyeballing what a library's data actually looks like
    before importing it.
    """
    if not args.tenant and not args.ils:
        raise SystemExit(
            "give either --tenant (credentials from the database) or --ils "
            "(credentials from the environment)"
        )
    if args.tenant and args.ils:
        raise SystemExit("--tenant and --ils are alternatives; pass only one")

    driver = (
        _driver_from_tenant(settings, args.tenant, args.page_size)
        if args.tenant
        else _build_driver(args)
    )
    total = 0
    try:
        for record in iter_records(driver):
            total += 1
            if args.limit and total > args.limit:
                total -= 1
                print(f"... stopping at --limit {args.limit}")
                break
            year = record.copyright_date or "—"
            author = record.author or "—"
            print(f"{record.biblio_id:>7}  {record.title[:52]:<54}{author[:28]:<30}{year}")
    except DriverError as exc:
        raise SystemExit(_driver_failure(exc)) from None
    finally:
        close = getattr(driver, "close", None)
        if close:
            close()

    print(f"\n{total} record(s) normalized from {driver.name}. Nothing was written.")
    return 0


def _import_one(settings: Settings, discovery, tenant_id: str, args) -> ImportResult:
    """Resolve a driver for one library and run the import, closing the driver."""
    driver = (
        _build_driver(args)
        if getattr(args, "ils", None)
        else _driver_from_tenant(settings, tenant_id, args.page_size)
    )
    try:
        # Unset embedding configuration is a supported state, not a broken one:
        # `Embedder.configured` is false, the import writes no vectors, and the
        # lexical index answers alone -- which is what this did before vectors
        # existed.
        with Embedder(EmbeddingSettings.from_env()) as embedder:
            return full_import(
                discovery,
                tenant_id,
                driver,
                batch_size=args.batch_size,
                allow_empty=args.allow_empty,
                embedder=embedder,
            )
    finally:
        close = getattr(driver, "close", None)
        if close:
            close()


def cmd_full(settings: Settings, args: argparse.Namespace) -> int:
    """Import whole catalogs into the Discovery Index.

    One library at a time, and a failure in one does not stop the rest: a
    library whose server is down for an hour must not keep every other
    library's catalog from being refreshed.
    """
    if not args.tenant and not args.all:
        raise SystemExit("give either --tenant NAME or --all")
    if args.tenant and args.all:
        raise SystemExit("--tenant and --all are alternatives; pass only one")
    if args.all and args.ils:
        raise SystemExit(
            "--all takes each library's credentials from the registry, so "
            "--ils has nothing to apply to"
        )

    if args.tenant:
        tenants = [args.tenant]
    else:
        # The registry, not the credentials table: this question spans
        # libraries, and `tenants` is the one table with no policy on it, so it
        # needs no privileged connection.
        with connect_tenant(settings) as conn:
            tenants = [t.tenant_id for t in syncable_tenants(conn)]
        if not tenants:
            print(
                "no libraries to sync: none has stored ILS credentials yet.\n"
                "Register one in /libvox-portal and configure its catalog."
            )
            return 0
        print(f"{len(tenants)} librar{'y' if len(tenants) == 1 else 'ies'} to sync\n")

    failed = 0
    with connect_discovery(settings) as discovery:
        for tenant_id in tenants:
            try:
                result = _import_one(settings, discovery, tenant_id, args)
            except SyncAlreadyRunning as exc:
                failed += 1
                print(f"{tenant_id:<20} skipped — {exc}")
            except DriverError as exc:
                failed += 1
                print(f"{tenant_id:<20} FAILED\n{_driver_failure(exc)}")
            except SystemExit:
                raise
            except Exception as exc:
                failed += 1
                logger.debug("import failed for %s", tenant_id, exc_info=True)
                print(f"{tenant_id:<20} FAILED — {exc}")
            else:
                print(
                    f"{tenant_id:<20} generation {result.generation}, "
                    f"{result.written} record(s), {result.skipped} skipped, "
                    f"{result.pages} page(s)"
                )

    if failed:
        print(f"\n{failed} of {len(tenants)} failed. `status --tenant NAME` says why.")
    return 1 if failed else 0


def cmd_worker(settings: Settings, args: argparse.Namespace) -> int:
    """Answer import requests until stopped.

    The one long-running thing this module has. Everything else here is a
    command that finishes.
    """
    from .worker import DEFAULT_POLL_S, serve

    return serve(settings, poll_s=args.poll_seconds or DEFAULT_POLL_S)


def cmd_search(settings: Settings, args: argparse.Namespace) -> int:
    """Run the query behind `search_catalog`, against the index.

    The point of the index, exercised: this never touches the library's server.
    """
    try:
        # The embedder too, or this command would exercise half of what
        # `search_catalog` does and report it as the whole answer -- which is
        # exactly the thing it exists to let someone check without a model.
        with (
            connect_discovery(settings) as conn,
            Embedder(EmbeddingSettings.from_env()) as embedder,
        ):
            result = search(
                conn,
                args.tenant,
                args.query or "",
                author=args.author,
                title=args.title,
                limit=args.limit,
                embedder=embedder,
            )
    except (EmptySearch, ValueError) as exc:
        raise SystemExit(str(exc)) from None

    print(f"index: {result.index_status}")
    if result.index_status == "building":
        print("This library has no searchable records yet — run `full --tenant`.")
    elif result.index_status == "stale":
        print("The last sync failed; these records may be out of date.")

    if result.searched_for:
        # Said before the records, because they answer a different question
        # than the one that was typed.
        print(f"nothing matched that; searched for {result.searched_for!r} instead")

    if not result.records:
        print("no matches")
        return 0

    for record in result.records:
        year = record["copyright_date"] or "—"
        author = record["author"] or "—"
        print(
            f"{record['biblio_id']:>7}  {record['title'][:52]:<54}"
            f"{author[:28]:<30}{year}"
        )

    print(f"\n{len(result.records)} record(s)", end="")
    print(", and more matched than were shown" if result.truncated else "")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="libvox-sync",
        description="Populate and inspect the LibVox Discovery Index.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Apply pending schema migrations.")

    worker = sub.add_parser(
        "worker",
        help="Answer import requests raised from /libvox-portal.",
        description=(
            "Wait for a library to ask for its catalogue to be imported, and "
            "import it. Listens on the outbox the portal writes; see worker.py."
        ),
    )
    worker.add_argument(
        "--poll-seconds",
        type=float,
        default=None,
        metavar="S",
        help="Backstop poll interval. A press normally arrives by NOTIFY.",
    )

    status = sub.add_parser("status", help="Show a tenant's index state.")
    status.add_argument("--tenant", required=True, help="Tenant identifier.")

    extract = sub.add_parser(
        "extract",
        help="Extract and normalize records without writing anything (dry run).",
        description=(
            "Connection details come from the environment, per ILS: "
            "koha needs KOHA_URL, KOHA_CLIENT_ID and KOHA_CLIENT_SECRET. "
            "In the finished system this is tenant configuration resolved by "
            "/libvox-core; the flag is a standalone-mode substitute."
        ),
    )
    extract.add_argument(
        "--tenant",
        help="Resolve connection details from this tenant's stored credentials.",
    )
    extract.add_argument(
        "--ils",
        choices=sorted(DRIVERS),
        help=(
            "Take connection details from the environment instead. "
            "'fixture' reads a captured response offline."
        ),
    )
    extract.add_argument(
        "--fixture",
        metavar="PATH",
        help="Captured JSON response, for --ils fixture.",
    )
    extract.add_argument(
        "--page-size", type=int, default=100, help="Records per request."
    )
    extract.add_argument(
        "--limit", type=int, default=0, help="Stop after N records (0 = all)."
    )

    full = sub.add_parser(
        "full",
        help="Import a whole catalog into the Discovery Index.",
        description=(
            "Writes a new generation and swaps to it in one row, so search "
            "never sees a mixed state and a record the library deleted simply "
            "stops being there. Safe to re-run: it supersedes rather than "
            "appends."
        ),
    )
    full.add_argument("--tenant", help="Import this library.")
    full.add_argument(
        "--all",
        action="store_true",
        help="Import every library that has configured an ILS.",
    )
    full.add_argument(
        "--ils",
        choices=sorted(DRIVERS),
        help=(
            "Take connection details from the environment instead of the "
            "database. Only with --tenant."
        ),
    )
    full.add_argument(
        "--fixture", metavar="PATH", help="Captured JSON response, for --ils fixture."
    )
    # The defaults come from the constants rather than being written again
    # here: they were 500 in one place and 500 in the other until one of them
    # changed, which is how two numbers for one decision start disagreeing.
    full.add_argument(
        "--page-size",
        type=int,
        default=DEFAULT_PAGE_SIZE,
        help="Records per request to the library's server.",
    )
    full.add_argument(
        "--batch-size",
        type=int,
        default=DEFAULT_BATCH_SIZE,
        help=(
            "Rows per insert. Each batch commits on its own, so this is also "
            "how often a running import becomes visible to anyone watching."
        ),
    )
    # No --limit, deliberately. A partial catalog published as `ready` tells
    # the model the index is current, and the patron is told the library does
    # not have a book it has. `extract --limit` is for looking at data.
    full.add_argument(
        "--allow-empty",
        action="store_true",
        help=(
            "Publish a catalog with no records. Refused by default: zero "
            "records is usually a permission problem, not an empty library."
        ),
    )

    search_cmd = sub.add_parser(
        "search",
        help="Search a library's index, the way a patron's question does.",
        description=(
            "The read side of the CQRS split: answered entirely from the "
            "Discovery Index, so it never reaches the library's own server. "
            "This is the query /libvox-core will call for search_catalog."
        ),
    )
    search_cmd.add_argument("--tenant", required=True, help="Which library.")
    search_cmd.add_argument("query", nargs="?", default="", help="Free-text terms.")
    search_cmd.add_argument("--author", help="Narrow to an author.")
    search_cmd.add_argument("--title", help="Narrow to a title.")
    search_cmd.add_argument(
        "--limit", type=int, default=20, help="Records to return (1-20)."
    )

    tenant = sub.add_parser(
        "tenant",
        help="Manage per-tenant ILS credentials.",
        description=(
            "Secrets are encrypted with pgcrypto using LIBVOX_CRYPTO_KEY, "
            "which lives in the environment and never in the database."
        ),
    )
    tenant_sub = tenant.add_subparsers(dest="tenant_command", required=True)

    tenant_set = tenant_sub.add_parser("set", help="Store or replace credentials.")
    tenant_set.add_argument("--tenant", required=True, help="Tenant identifier.")
    tenant_set.add_argument(
        "--ils", required=True, choices=["koha"], help="Which ILS this library runs."
    )
    tenant_set.add_argument("--url", required=True, help="ILS base URL.")
    tenant_set.add_argument("--client-id", required=True, help="OAuth2 client id.")
    # No --client-secret: the secret comes from KOHA_CLIENT_SECRET or a hidden
    # prompt. An argument would land in /proc/<pid>/cmdline and shell history.

    tenant_sub.add_parser("list", help="List configured tenants (never secrets).")

    return parser


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("LIBVOX_LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )
    args = build_parser().parse_args(argv)
    settings = Settings.from_env()

    handlers = {
        "migrate": cmd_migrate,
        "status": cmd_status,
        "extract": cmd_extract,
        "full": cmd_full,
        "worker": cmd_worker,
        "search": cmd_search,
        "tenant": lambda s, a: {
            "set": cmd_tenant_set,
            "list": cmd_tenant_list,
        }[a.tenant_command](s, a),
    }
    return handlers[args.command](settings, args)


if __name__ == "__main__":
    sys.exit(main())
