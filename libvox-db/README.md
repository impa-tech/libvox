# /libvox-db

The one PostgreSQL server. Six databases, three roles, and a script that says
what is in them.

```bash
./db.sh                      # start it, wait, list the databases
./db.sh psql libvox_tenant   # a session against one of them
./db.sh logs
./db.sh reset                # delete every database on it
```

Run it before the other modules. `/libvox-portal`, `/libvox-core` and
`/libvox-sync` all connect here, and none of them starts a database.

| | Host | In containers |
| :--- | :--- | :--- |
| | `127.0.0.1:5435` | `postgres:5432` on the `libvox_internal` network |

**Loopback, and that is the boundary.** No deployment needs the host port at
all: the other modules reach the server by name over `libvox_internal`, and
`./db.sh` works through `docker compose exec`. It is published for tooling that
runs on the host — the suites against the `_test` databases, a `psql` session,
the DSNs in every `.env` — and none of that is on the network.

## Six databases, one writer each

That is the rule, and it is why this is six databases on one server rather than
one database with six schemas. Each has exactly one component that authors its
schema and writes to it; everyone else reads.

| Database | Written by | Read by |
| :--- | :--- | :--- |
| `libvox_tenant` | `/libvox-portal` — the registry, the encrypted ILS credentials, the channel bindings | `/libvox-core`, `/libvox-sync` |
| `libvox_conversations` | `/libvox-core` — what readers asked, what MARC.IA answered, the thumbs | `/libvox-portal` |
| `libvox_index` | `/libvox-sync` — the Discovery Index, rebuilt a generation at a time | `/libvox-core` |

Each has a `_test` copy. The suites truncate, so the rule in both `conftest.py`
files is that a test which truncates talks to a `_test` database or refuses to
run. They are real PostgreSQL databases rather than SQLite because what they
assert — row-level security, `pgcrypto`, triggers, `SECURITY DEFINER` — does not
exist under SQLite, and every isolation assertion would pass vacuously.

`libvox_index` was called `libvox` while it had a server to itself, where the
name was unambiguous. Beside `libvox_tenant` and `libvox_conversations` it read
like the main database rather than one of three.

## Three roles

| Role | What it is for |
| :--- | :--- |
| `libvox` | Superuser. Owns the databases and runs the migrations. |
| `libvox_app` | `NOSUPERUSER NOBYPASSRLS`. What every application connects as. |
| `libvox_resolver` | `NOLOGIN`. Owns one `SECURITY DEFINER` function. |

**`libvox_app` exists because superusers bypass row-level security
unconditionally.** An application connecting as `libvox` would make every
isolation policy in every database inert — present in the schema, enforcing
nothing. `/libvox-portal` refuses to serve a request if its connection turns out
to be privileged, because that failure is otherwise silent.

**`libvox_resolver` cannot log in, and that is the point.** Resolving "which
library owns this widget key" happens before any tenant is known, so it cannot
run inside a tenant scope and row-level security would match nothing. It is the
one genuinely cross-tenant read in the system, and it is answered by a
`SECURITY DEFINER` function owned by this role. Since nobody can connect as the
owner, the only way to exercise its privileges is to call the function — so the
width of the exception is the function's return type, which is one column and
never a secret.

## What separates a library's data from another's

Row-level security, and only that. Every table carries a `tenant_id` with the
same `tenant_isolation` policy under `ENABLE` plus `FORCE`, matching
`current_setting('libvox.tenant_id', true)` and failing closed. A missing filter
returns nothing rather than another library's rows, and the database guarantees
it rather than the query author.

This server used to be two, one per module, which gave a second kind of
separation for free: a credential for the conversation log physically could not
reach the Discovery Index. One `libvox_app` across one cluster replaces that
with grants — so `initdb/02-databases.sql` revokes `CONNECT` from `PUBLIC` on
every database before granting it, and reaching one is something somebody wrote
down rather than a PostgreSQL default.

The single role is the design the two servers were already imitating: they
created the same name with the same password, and `/libvox-portal`'s initdb said
the match was deliberate — *one name for one privilege level is what keeps the
grants readable across three components.*

## The volume, and the one thing that surprises people

`initdb/` runs **once**, when the volume is empty, and never again. A volume
created before a change to those files will not have it — a database added to
`02-databases.sql` simply will not exist, and `./db.sh` prints `absent` beside
it rather than letting the discovery happen later inside a container.

The usual cure is to create the missing database by hand, with the same `OWNER`,
`REVOKE` and `GRANT` that `02-databases.sql` applies. `./db.sh reset` is for when
there is nothing on the server worth keeping: it is destructive, asks you to type
the word, and takes the registered libraries and their encrypted ILS credentials,
every stored conversation, and every imported catalogue. The catalogues come back
with an import; the credentials have to be entered again.

**`absent` means the catalog says so, and nothing else.** The listing asks
`pg_database` once for the names that exist, so a query that fails is reported as
a query that failed. An earlier version ran a size query per database and read an
empty answer as "missing" — but an empty answer is also what a connection blip
returns, and the advice printed under it was `reset`. A listing must not be able
to recommend deleting six databases because one query did not come back.

Ready, likewise, means the real server answered over TCP. On a fresh volume the
postgres entrypoint runs `initdb/` against a temporary server that listens only
on the unix socket, and `pg_isready` inside the container answers yes to that
one — before `02-databases.sql` has run.

## Credentials

The passwords in `docker-compose.yml` and `initdb/01-roles.sql` are a local
development convenience. In a real deployment these roles are created by your
provisioning, with secrets from your secrets manager.

`LIBVOX_CRYPTO_KEY` is not among them and never appears here. It encrypts the
ILS credentials stored in `libvox_tenant`, and a key kept in the database it
protects protects nothing.
