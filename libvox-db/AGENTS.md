# /libvox-db — agent rules

Repo-wide rules: [`../AGENTS.md`](../AGENTS.md). Module detail:
[`README.md`](README.md).

**No application code lives here.** One `docker-compose.yml`, two SQL scripts in
`initdb/`, and `db.sh`. It is the PostgreSQL server every other module connects
to, and the place where tenant isolation is either enforced or quietly inert.

Run it before anything else. `/libvox-portal`, `/libvox-core` and `/libvox-sync`
all connect here and none of them starts a database.

## Rules

- **Six databases, one writer each.** That is why this is six databases on one
  server rather than one database with six schemas: `libvox_tenant` is written
  by `/libvox-portal`, `libvox_conversations` by `/libvox-core`, `libvox_index`
  by `/libvox-sync`, and each has a `_test` copy. Everyone else reads. Do not
  add a second writer to an existing database.
- **`libvox_app` exists because superusers bypass row-level security
  unconditionally.** It is `NOSUPERUSER NOBYPASSRLS`
  (`initdb/01-roles.sql:40-41`), and every application connects as it. A change
  that makes something connect as `libvox` instead does not fail — it makes
  every isolation policy in every database inert while the tests still pass.
  This is the single most damaging edit possible in this repository.
- **`libvox_resolver` is the one deliberate hole, and it cannot log in**
  (`initdb/01-roles.sql:64`). Resolving "which library owns this widget key"
  happens before any tenant is known, so it cannot run inside a tenant scope and
  RLS would match nothing. It is answered by a `SECURITY DEFINER` function that
  this role owns. Since nobody can connect as the owner, the only way to use its
  privileges is to call the function — so the width of the exception is the
  function's return type, which is one column and never a secret. The function
  itself is created by `/libvox-portal`, which owns that schema
  (`libvox-portal/database/migrations/2026_08_27_032000_create_resolve_channel_binding_function.php`).
  Never widen its return type, and never add a second `SECURITY DEFINER`
  function without an issue.
- **Isolation is row-level security, and only that.** Every tenant table carries
  the same `tenant_isolation` policy under `ENABLE` **plus** `FORCE`, matching
  `current_setting('libvox.tenant_id', true)` and failing closed. A missing
  filter returns nothing rather than another library's rows. Never add a manual
  `WHERE tenant_id = …` as a substitute: a forgotten one leaks, a missing scope
  returns empty.
- **The image is `pgvector/pgvector:pg17`, not stock postgres.** `/libvox-sync`'s
  migration 008 creates the `vector` extension, and the stock image does not
  carry it. Anything standing this server up — compose, CI, a test fixture —
  needs that image or the migration refuses with a message naming this cause.

## `initdb/` runs once, and that surprises everyone

It runs when the volume is **empty**, and never again. A volume created before a
change to those files will not have it: a database added to
`02-databases.sql` simply will not exist, and `./db.sh` prints `absent` beside
it rather than letting the discovery happen later inside a container.

The usual cure is to create the missing database by hand, with the same `OWNER`,
`REVOKE` and `GRANT` the script applies.

**`./db.sh reset` is destructive and asks you to type the word.** It takes every
registered library and their encrypted ILS credentials, every stored
conversation, and every imported catalogue. The catalogues come back with an
import; the credentials have to be entered again. Never run it to "get a clean
state" while debugging something else, and never suggest it as a first step.

`absent` in the listing means the catalog says so and nothing else. An earlier
version read a failed query as "missing" and printed advice to `reset` — a
listing must not be able to recommend deleting six databases because one query
did not come back.

## Watch for

- **The passwords in `docker-compose.yml` and `initdb/01-roles.sql` are a local
  development convenience.** In a real deployment these roles are created by
  provisioning, with secrets from a secrets manager.
- **`LIBVOX_CRYPTO_KEY` is not here and must never be.** It encrypts the ILS
  credentials stored in `libvox_tenant`, and a key kept in the database it
  protects protects nothing.
- **The port is 5435**, published to loopback only. No deployment needs the host
  port at all — the other modules reach the server by name over
  `libvox_internal`. It is published for tooling that runs on the host: the
  suites against the `_test` databases, a `psql` session, the DSNs in every
  `.env`.
- There is no test runner here. This module's SQL is exercised through
  `/libvox-sync`'s suite, which is where the RLS and fail-closed assertions live.
