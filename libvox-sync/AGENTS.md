# /libvox-sync — agent rules

Repo-wide rules: [`../AGENTS.md`](../AGENTS.md). Module detail:
[`README.md`](README.md).

**This is the write side of the CQRS split.** It extracts from the ILS and
populates the Discovery Index, which every reader search is then served from.

## Rules

- **Tenant isolation is PostgreSQL Row-Level Security, not a `WHERE` clause.**
  Every query runs inside `tenant_scope(conn, tenant_id)`, which issues
  `SET LOCAL` so the binding dies with the transaction and cannot leak onto the
  next one on a pooled connection. Outside a scope the policies match nothing and
  the tables read empty — **fail closed**. Never add a manual tenant filter as a
  substitute; a forgotten one returns another library's records, while a missing
  scope returns nothing.
- **Migrations are numbered, append-only, and checksummed.** Editing an applied
  migration is refused by design: a change that silently never runs on a deployed
  database is worse than a noisy error. Add a new file.
- **No ORM, no migration framework.** The schema is SQL-first — a generated
  `tsvector`, weighted full-text, and RLS policies are all hand-written anyway.
  Do not introduce SQLAlchemy or Alembic to "clean this up".
- **Extraction throttles its own pagination.** A full initial import must never
  saturate the library's server. This constraint outranks import speed.
- **`/libvox-driver-koha` is a hard dependency, and the Koha knowledge lives
  there.** Declared at `pyproject.toml:32`, installed editable through
  `requirements.txt:16`, and imported at `src/libvox_sync/drivers/__init__.py:15`
  — which re-exports the contract rather than restating it. The driver defines
  five methods (`name`, `get_biblio`, `search_by_title`, `fetch_page`,
  `check_availability`, in `libvox-driver-koha/src/libvox_driver_koha/contract.py:214`),
  not one.

  **Do not re-fork it.** This module used to carry a second Koha implementation —
  its own `BiblioRecord`, its own normalizer, its own pagination rules — and
  `a93c89d` removed it on purpose: the live path and the bulk path have to agree
  about the same book, so they read the same code. Adding a local Koha module
  back, or "inlining the driver to drop a dependency", undoes that.

  The contract is ILS-neutral and moves to a shared `libvox-common` when
  `/libvox-driver-folio` gives it a second implementation to be neutral between.

## A green `pytest` here means very little

Of 150 tests, **119 need a database** — the entire schema and tenant-isolation
set, including the fail-closed behaviour above. Without one the suite reports
`31 passed, 119 skipped` and exits zero. Do not report "tests pass" for this
module on that basis.

```bash
cd ../libvox-db && ./db.sh && cd -
pytest                      # 119 passed, 31 skipped
```

`docker compose` here starts the sync worker, not a database — the server moved
to `/libvox-db` and is shared by every module.

The 31 that still skip are not failures: 27 need the Tenant DB's schema, which
`/libvox-portal` owns and migrates, and 4 need `/libvox-mcp` installed for the
wire-contract drift check. Both say so in their skip reason.

## Watch for

- **Search has two arms and they cover each other.** The lexical one is a
  hand-built configuration, `libvox_search` — Portuguese stemming *plus*
  `unaccent`, so "memorias" finds "memórias" (migration 005). Not
  `to_tsvector('portuguese', …)`: changing the configuration name silently
  changes what every reader search matches. The second arm is a pgvector
  embedding (migration 008), which is what lets a question in Portuguese reach a
  collection catalogued in English. Fusion is Reciprocal Rank Fusion in
  `search.py`; neither arm replaces the other, and the similarity floor is what
  lets the library still answer "we do not have it".
- **A typo is repaired by the index, not by the model** (migration 006): trigram
  shortlist, edit distance to decide, candidates drawn only from words this
  library actually catalogued. Do not move this back into the prompt.
- Incremental sync against Koha is blocked upstream (`timestamp` filter answers
  HTTP 500). Do not write code that assumes it works.
