"""Drive the whole chain: MCP layer, Core, index, driver.

Called by `./core.sh drive`. A separate file rather than a heredoc inside that
script, because a Python heredoc nested in a shell heredoc terminates the wrong
one — a mistake already made once in this repository.

It runs in the **/libvox-mcp environment**, since it needs the MCP SDK, and it
is the only thing in the project that exercises the boundary end to end: the
real `LibVoxTokenVerifier` asking the real Core who a credential belongs to,
then two tool calls whose arguments never name a library.

`./core.sh check` covers Core's routes and stops there. This covers what sits
in front of them.
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys

BOLD = "\033[1m"
RED = "\033[31m"
OFF = "\033[0m"


def say(message: str) -> None:
    print(f"\n{BOLD}{message}{OFF}")


async def drive(token: str, query: str) -> int:
    from libvox_mcp import auth
    from libvox_mcp.config import Settings
    from libvox_mcp.core_client import CoreClient
    from libvox_mcp.server import create_server
    from mcp import Client, MCPError

    settings = Settings.from_env()
    if settings.backend != "core":
        print(
            f"{RED}LIBVOX_MCP_BACKEND is {settings.backend!r}. This drives the "
            f"production path, so it has to be 'core'.{OFF}",
            file=sys.stderr,
        )
        return 1

    core = CoreClient(settings.core_url, service_token=settings.service_token)
    server = create_server(settings)

    try:
        say("Verifying the credential — the real verifier, against the real Core")
        verified = await auth.LibVoxTokenVerifier(core).verify_token(token)
        if verified is None:
            print(f"{RED}  Core did not recognise it.{OFF}", file=sys.stderr)
            return 1
        print(f"  tenant {verified.claims['tenant_id']} | scopes {verified.scopes}")

        # The tool call path reads the credential from context. Substituting it
        # here is what a transport would otherwise do; everything downstream --
        # require_tenant, the scope check, the forwarding to Core -- is real.
        auth.get_access_token = lambda: verified

        async with Client(server) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}

            say("The tool surface the model actually sees")
            print(f"  {sorted(tools)}")
            forbidden = {"tenant", "tenant_id", "library", "library_id", "ils_url"}
            leaking = [
                name
                for name, tool in tools.items()
                if forbidden & set((tool.input_schema or {}).get("properties", {}))
            ]
            print(
                "  no tool accepts a library:", not leaking,
                "(propagation rule 1)" if not leaking else f"LEAKS: {leaking}",
            )

            say(f"search_catalog {query!r} — through MCP, answered from the index")
            result = await client.call_tool(
                "search_catalog", {"query": query, "limit": 3}
            )
            body = result.structured_content
            print(f"  index {body['index_status']} | {len(body['records'])} record(s)")
            for record in body["records"]:
                print(f"    {record['biblio_id']:>6}  {record['title'][:52]}")

            ids = [r["biblio_id"] for r in body["records"]]
            if not ids:
                say(
                    f"Nothing matched {query!r}. That is an answer, not a failure —\n"
                    "  the term has to be in the language the library catalogued in."
                )
                return 0

            say("check_availability — through MCP, answered live by the driver")
            availability = await client.call_tool(
                "check_availability", {"biblio_ids": ids + [999999]}
            )
            records = availability.structured_content["records"]
            for record in records:
                print(
                    f"    {record['biblio_id']:>6}  {record['status']:<12}"
                    f"{record['copies_available']}/{record['copies_total']}"
                    f"  {record['library'] or ''}"
                )
            invented = any(r["biblio_id"] == 999999 for r in records)
            print("    999999 is absent above:", not invented, "(propagation rule 5)")

            if not records:
                say(
                    "Nothing came back, and the ids were in the index — so the ILS\n"
                    "  refused. If Core runs in a container, see ./core.sh check."
                )
                return 1

            say("A tool call carrying no credential")
            auth.get_access_token = lambda: None
            try:
                await client.call_tool("search_catalog", {"query": query})
            except MCPError as exc:
                print(f"  refused before reaching Core: {exc}")
            else:
                print(f"{RED}  it was answered. require_tenant did not run.{OFF}")
                return 1
    finally:
        await core.aclose()

    say("The whole chain holds — MCP layer included.")
    return 0


def main() -> int:
    logging.disable(logging.INFO)

    token = os.environ.get("LIBVOX_SESSION_TOKEN")
    if not token:
        print(
            "LIBVOX_SESSION_TOKEN is not set. Run this through ./core.sh drive,"
            " which mints one.",
            file=sys.stderr,
        )
        return 2

    query = sys.argv[1] if len(sys.argv) > 1 else "programming"
    return asyncio.run(drive(token, query))


if __name__ == "__main__":
    sys.exit(main())
