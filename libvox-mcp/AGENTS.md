# /libvox-mcp — agent rules

Repo-wide rules: [`../AGENTS.md`](../AGENTS.md). Module detail:
[`README.md`](README.md).

**This module is a protocol adapter.** It declares tools, resolves the caller's
credential to a tenant by asking Core, and forwards. Business logic belongs in
`/libvox-core`.

## What this module must never grow

Four prohibitions, stated in full — with their reasons — at
[`README.md` §What this module must never grow](README.md#what-this-module-must-never-grow).
Read them there rather than trusting a summary here: a tenant argument on any
tool, direct ILS access, decrypted ILS credentials, and a third tool without an
issue and a stated reason.

`direct_koha.py`, the temporary experiment reached only via
`LIBVOX_MCP_BACKEND=koha`, is an exception to three of the four: it talks to a
Koha directly, holds a `koha_client_secret`, and declares a third tool
(`get_biblio`) that production does not. That last one matters when reading a
session against it — the model was choosing from a larger set than it will face
for real.

`tests/test_server.py` enforces the tenant rule and the tool count. Do not relax
those tests to make a change pass.

## Running it

`LIBVOX_MCP_BACKEND` selects everything (`config.py`). `core` is the
architecture — Core resolves the tenant and routes — and is the default. `koha`
is the experiment: one Koha, directly, no tenant and no authorization. Anything
else is refused at startup rather than falling back, because a silent fallback to
`core` would look like it worked. With `koha` you also need `KOHA_URL`,
`KOHA_CLIENT_ID` and `KOHA_CLIENT_SECRET`.

## Watch for

- **MCP SDK 2.x is load-bearing.** The 1.x→2.x jump renamed `FastMCP` to
  `MCPServer`, moved response fields to snake_case, and swapped `httpx` for
  `httpx2`. Training data is mostly 1.x, so an agent will confidently write
  `FastMCP`. Check the installed SDK before trusting a snippet.
- **Nothing may reach stdout.** stdio transport carries the protocol there; a
  stray `print` corrupts the session. See `logs.py`.
- **`direct_koha.py` is a temporary experiment.** Nothing on the production path
  imports it. It was to go when `/libvox-core` landed; Core landed and it stayed,
  as a debugging convenience — so do not delete it as housekeeping, and do not
  extend it or let its shortcuts leak into `server.py`.
- **Tool descriptions are part of the contract.** They are where a rule reaches
  the model, and editing that text changes runtime behaviour. `search_catalog`
  tells the model to read `index_status` before concluding the library holds
  nothing (`server.py:57`, `:194`), and states its own `limit` bound in prose
  rather than only in the JSON schema — a bound stated only in the schema is one
  a model under pressure stops reading.
