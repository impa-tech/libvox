# /libvox-driver-koha — agent rules

Repo-wide rules: [`../AGENTS.md`](../AGENTS.md). Module detail:
[`README.md`](README.md).

**This module is where Koha knowledge belongs.** MARC field numbers, Koha's
query syntax, its status codes — all of it should stop here, and downstream of a
driver nothing knows what a MARC field is.

**It is the only place that knows Koha, and that is recent.**
`/libvox-sync` used to carry a second implementation — the same ISBD stripping,
ISBN packing, suppression handling and pagination, duplicated — and `a93c89d`
collapsed it into this module: the live path and the bulk path have to agree
about the same book, so they read the same code. `/libvox-sync` now imports the
contract from here (`libvox-sync/src/libvox_sync/drivers/__init__.py:15`).

Do not re-fork it. A second copy is free to drift, and one of the two will be
wrong first.

## Rules

- **`contract.py` is ILS-neutral.** It is the contract between the platform and
  *any* driver, sitting here only because Koha is the first one. When
  `/libvox-driver-folio` arrives it moves to a shared `libvox-common` — a file
  move, provided nothing Koha-specific leaked into it. Adding a Koha detail there
  turns a move into an untangling.
- **The driver never learns which tenant it serves.** A request carries a
  connection — URL plus credential — not an identity. `handle()` rejects unknown
  connection keys against an allowlist (`src/libvox_driver_koha/handler.py:159`),
  which is what turns a stray `tenant_id` into a `bad_request`; note the
  allowlist is generic, so nothing in `contract.py` mentions a tenant at all.
  `test_the_contract_has_no_place_for_a_tenant`
  (`tests/test_handler.py:121`) pins the tenant keys specifically.
- **Nothing is held between invocations.** The OAuth token is cached for the
  lifetime of a `KohaClient` object and no longer. A cache that outlives the
  client is where library A's bearer token ends up on library B's request.
- **`search_by_title` is not the reader's search.** Diagnostics and
  reconciliation only. Reader search is served by the Discovery Index; wiring
  this to the chat path breaks the guarantee that search traffic never reaches
  the library's server.
- **One normalization serves both paths.** `get_biblio` and `fetch_page` share
  it deliberately — a catalog that answered one way live and another way to the
  importer puts the index and the live answer in disagreement about the same
  book. `tests/test_normalize.py` asserts this against captures from both
  endpoints.

## Koha behaviour that will waste your time

- A `403` carrying `required_permissions: {"catalogue": "1"}` can mean a **null
  `borrowers.userid`**, not a missing permission. Koha resolves permissions
  through `haspermission($userid, …)`.
- Tokens expire in 3600s. Cache and refresh; never re-authenticate per call.
- `_match` is unsupported on some versions — use `like`.
- `like` matches inside words (`chess` finds *The Duchess of Malfi*) and a bare
  `%` matches the whole catalog. Escape wildcards in any input.
- `q={"timestamp":{">":"…"}}` answers HTTP 500, so "what changed since" is not
  available. OAI-PMH is the likelier path.
