# /libvox-sync

The ETL worker that populates the **Discovery Index** — the durable PostgreSQL
read model every catalog search is served from. See the root
[README](../README.md#4-the-sync-engine--the-discovery-index-libvox-sync) for
where this sits in the architecture.

The Discovery Index: its schema, the pipeline that writes records into a
generation and publishes it, the `full` command that drives it, and the search
a patron's question runs — one library or every library in the registry,
answered without ever reaching the library's own server.

**Nothing here knows what an ILS is.** The pipeline takes pages of
`BiblioRecord` from an object with one method and never learns where they came
from; the schema, the search and the index are the same whatever answered.
Koha is the only driver written so far, which is why it is what every example
below uses — see [Drivers](#drivers) for what a second one costs.

## Two ways in, one pipeline

An import starts from a command line or from the outbox: a librarian presses a
button in `/libvox-portal`, a `SyncRequested` row is written in the same
transaction, and `libvox-sync worker` answers it.

|  | Command line | Worker |
| :--- | :--- | :--- |
| Trigger | `full --tenant` | Outbox event |
| ILS credentials | Container environment | Resolved here |
| Pipeline | **identical** | **identical** |

The pipeline takes an already-resolved job and does not know where it came from,
so a third way in is an adapter rather than a change to the middle. Either way
this worker writes straight to the Discovery Index.

> **This module decrypts its own credentials.** The worker resolves each
> library's through `credentials.fetch`, so an import needs nothing but the key
> and the Tenant DB. Core decrypts too, by importing this module — `ils.py`
> calls the same function.

## Setup

```bash
cd ../libvox-db && ./db.sh && cd -    # the one PostgreSQL server

conda env create -f environment.yml
conda activate libvox-sync

# A driver is a sibling directory, not a published package, so it goes in
# first. This module needs one -- without a driver there is nothing to extract
# -- and Koha's is the one that exists.
pip install -e ../libvox-driver-koha
pip install -e ".[dev]"

python -m libvox_sync migrate
```

Conda, one environment per module, is the project standard — see the root
README for the two traps (`conda run` eating extras, and Anaconda's channels
answering `429`).

Postgres is not in this module. `/libvox-db` owns the one server, on host port
**5435** — not 5432, because the Koha Testing Docker stack and a local
PostgreSQL both want the default and a port clash is a confusing failure to
debug. This module owns a database on it, `libvox_index`, whose schema and
generations nothing else writes.

## Two roles, and why

`POSTGRES_USER` creates a **superuser**, and superusers bypass row-level
security unconditionally. An application connecting as that role would make the
tenant isolation policies inert — present in the schema, enforcing nothing.

So there are two roles:

| Role | Job | Used by |
| :--- | :--- | :--- |
| `libvox` | Runs migrations (creates objects) | `migrate` only |
| `libvox_app` | Reads and writes tenant data | Everything else |

`test_application_role_cannot_bypass_rls` asserts the application role is
neither a superuser nor `BYPASSRLS`, because every other isolation test is
vacuous if it is.

## Schema

| Object | Purpose |
| :--- | :--- |
| `discovery_biblio` | Records, a weighted `libvox_search` `tsvector`, and an `embedding` (see *What is searchable*) |
| `discovery_vocabulary` | Every word this library catalogued, for repairing a typo against it |
| `discovery_tenant` | Per-tenant state: active generation, `index_status` |
| `discovery_active` | Read surface — the live generation, generations hidden |
| `discovery_vocabulary_active` | The same, for the vocabulary |

Read from the two `_active` views; write to the two tables under them.

**Generations** exist for one job: a full import with correct deletions. The
worker writes generation N+1, flips `active_generation` in a single-row update,
then drops N. Because the swap is one row, search never observes a mixed state.
A record the library deleted is simply absent from the new generation, so
deletions are correct without ever asking the ILS what it deleted.

That is also what lets each batch commit on its own instead of holding one
transaction open across an entire catalog: an unpublished generation is
invisible to `discovery_active`, so there is no half-written state a reader
could catch. A crashed import leaves rows nobody will publish, and the next run
clears them before writing its own.

The next generation number comes from `MAX(generation) + 1` over the records,
not from `active_generation + 1`. Those differ exactly when an earlier import
died, and the second form would write into the orphan's rows and blend two
imports into one.

Two details in the SQL worth knowing before editing it:

- **`to_tsvector` must take two arguments.** The one-argument form depends on
  `default_text_search_config` and is therefore not `IMMUTABLE`, so PostgreSQL
  rejects it in a generated column. Naming the configuration explicitly also
  pins the language regardless of server configuration.
- **The configuration is `libvox_search`, not `portuguese`.** See *Accents*
  below; the short version is that `unaccent(text)` is `STABLE` and cannot be
  called from a generated column, but works as a dictionary inside a text
  search configuration.
- **The view is `security_invoker = true`.** Without it, row-level security
  would evaluate against the view's owner and the view would quietly bypass
  tenant isolation. Requires PostgreSQL 15+.

### Two states the database refuses

Both encode decisions from the root README as constraints rather than
convention:

- `index_status = 'ready'` with no `active_generation` — a tenant that is
  "ready" with nothing to serve makes an empty search look like a genuine "no
  matches", which is the confidently wrong answer `index_status` exists to
  prevent.
- Any `index_status` outside `ready` / `building` / `stale` — the set is a
  contract with `libvox_mcp.models.IndexStatus`.

## What a full import does, and refuses to do

```bash
python -m libvox_sync full --tenant lib-furg   # one library
python -m libvox_sync full --all               # every library in the registry
```

`--all` takes its list from `/libvox-portal`'s registry. A failure in one
library does not stop the rest: a catalog server that is down for an hour must
not keep every other library from being refreshed. The exit code counts the
failures, and `status --tenant NAME` says what happened to each.

### `index_status` is a promise to the model, so it is set carefully

`libvox_mcp.models.IndexStatus` is how MARC.IA decides whether an empty result
means *"the library has nothing like that"* or *"I have not finished reading
this library's catalog"*. Both are honest answers; saying the wrong one is not.

| Situation | Before | On success | On failure |
| :--- | :--- | :--- | :--- |
| First import | `building` | `ready` | `building` + `last_error` |
| Re-import | `ready`, untouched throughout | `ready` | `stale` + `last_error` |

**A re-import never passes through `building`.** The live generation is
answering perfectly well while the new one is written, and calling it
`building` would have MARC.IA apologize for an index that works. When a
re-import fails, `stale` is the accurate word: there are records, and they are
known to be behind.

### Three things it refuses

- **A catalog of zero.** Publishing it would have MARC.IA state, confidently,
  that the library holds nothing. Zero records is a credential without Koha's
  `catalogue` flag far more often than it is a library without books, so the
  refusal names that first. `--allow-empty` exists for the case that is real.
- **A second import of the same library while one is running.** A session-level
  advisory lock — session-level, because the import spans many transactions and
  a transaction-scoped lock would be released after the first batch. It is
  released even when the import fails, so a crash never needs an operator to
  clear it by hand. `hashtext` can collide, which would make two libraries
  queue behind each other pointlessly; that is a scheduling cost, not a
  correctness one.
- **Importing part of a catalog.** There is no `--limit` on `full`, and that is
  deliberate: a truncated generation published as `ready` tells the model the
  index is current while it holds the first few hundred records, and the patron
  hears "we do not have that". `extract --limit` is for looking at data.

### Two things the database taught us

- **`COPY` cannot be used.** PostgreSQL refuses outright — `COPY FROM not
  supported with row-level security`, naming `INSERT` in the hint. COPY is the
  obvious reflex for bulk loading, and the isolation model rules it out
  permanently. The writes are `executemany`, which psycopg runs in pipeline
  mode.
- **A record can arrive twice.** Koha paginates by offset, so a catalogue
  edited while the walk is in progress shifts rows between pages. Without `ON
  CONFLICT DO NOTHING`, hour three of an import dies on a primary key violation
  because a cataloguer was doing their job.

## The search a patron's question runs

```bash
python -m libvox_sync search --tenant lib-furg "romances brasileiros"
python -m libvox_sync search --tenant lib-furg --author "Machado de Assis"
```

This is the query `/libvox-core` runs for `search_catalog`, and the reason
the index exists: **it never touches the library's own server.** Ranking is
relevance and not insertion order — from the weighted `tsvector`, and from a
second arm that searches by meaning when one is configured, the two fused. The
CLI carries an embedder too, so this command exercises what `search_catalog`
exercises rather than half of it.

### What is searchable

| Weight | Field | |
| :--- | :--- | :--- |
| A | `title` | |
| B | `series`, `author` | |
| C | `publisher` | |
| D | `abstract`, `notes` | **indexed, never returned** |

Two of those need saying out loud, because the index shipped without them and
the failure was the quiet kind.

**Series is often the word.** A catalog of Pokémon records has titles like
*Metapod* and a series of *Pokémon - Generation I*. A patron asking for
"pokemon" was told the library had nothing — with `index_status: ready`, which
means *the index is current and there is nothing*, about a copy on a shelf. The
ILS had the field all along; it arrived in the same request as the title and
was dropped in normalization.

**Abstract and notes are indexed and not returned.** They are what lets a
search for "chrysalis" reach *Metapod*, and they stay out of `discovery_active`
— the view whose every column is a `CatalogRecord` field, asserted by a test.
Twenty abstracts in one tool result is more text than the conversation around
them, and MARC.IA's job is to say what the library holds, not to recite a plot
summary she cannot check. Weight D keeps them from outranking a real title: a
search for "chess" reaching *The Duchess of Malfi* is acceptable; reaching it
*first* is not.

That table is the **lexical** half. There is a second one — see *Two arms*
below — and it reads less: title, series and author, and not the prose. Most
records have no prose at all (218 abstracts in 8,774, measured), so embedding
it would make a handful of records unlike all the rest for no reason a reader
would recognise, and cataloguer prose is long enough to push the part that
names the book into a corner of the vector.

### Accents

`portuguese` stems plurals and conjugations and leaves accents exactly where
they are, which is linguistically right and wrong for a search box:

```
vector  ... 'pokémon' ...        query  'pokemon'      →  no match
```

Every one of "sertao", "memorias postumas", "jose", "guimaraes" missed a record
the library holds, and reported `ready` while doing it. Migration 005 defines
`libvox_search` — `unaccent` first, then `portuguese_stem` — and **both sides of
every search use it**, because an asymmetry there is worse than no folding at
all. The extension is trusted since PostgreSQL 13, so the migration creates it
without a superuser and there is nothing for an operator to do by hand.

### Two arms: words and meaning

`discovery_biblio` carries a `vector(1024)` beside its `tsvector`. Every search
with text in it runs both, and the two ranked lists are fused with Reciprocal
Rank Fusion — `1/(60 + position)`, summed, so a record both arms found outranks
one either arm found alone.

**Why there is a second arm at all: no lexical index crosses a language.** A
reader asked *"tem algo sobre matemática?"* of a collection catalogued in
English and was told the library had nothing. It has the books. `matemática`
and `mathematics` share no stem in any configuration, and `math` and `mathemat`
are two words even inside English. That is the limit of the technique, not a
setting nobody got round to.

**Why the first arm stays.** They fail in different places, measured on the
same collection: `xadrez` is answered only by the vector arm, from titles
containing no Portuguese at all, and `cinema` is answered better by the lexical
arm, from four titles that contain the word. A bare inflected word embeds
poorly. Choosing one would trade one set of failures for another.

| | |
| :--- | :--- |
| model | `qwen3-embedding:4b`, 2560 dimensions truncated to **1024** — pgvector indexes at most 2000, and the Matryoshka prefix ranks identically to the full vector |
| index | HNSW, `vector_cosine_ops`, on the same table and under the same RLS policy |
| size | ~4 KB a record: 1.7 MB for 436, 36 MB for 8,774 |
| import | ~45 records/s, so 3.2 min for 8,774 |
| query | one embedding per question, ~0.2 s on a warm connection |

The fusion is twenty lines of Python in `search.fuse`, not a CTE with two
`row_number()`s and a `FULL OUTER JOIN`. The SQL would do the same thing and
would make `_text_conditions` serve a third caller, while putting the logic
somewhere no test can reach without a database.

#### Two things that are silent when you get them wrong

**The query carries an instruction prefix and the record does not.**
Qwen3-Embedding is asymmetric by design. Read symmetrically, the similarity
measures *length* rather than subject: a two-word Irish title scored 0.616
against `math` while `The hundred penny box  Mathis, Sharon Bell.` scored
0.306, and every question came back with the catalogue's shortest titles.
Nothing raises — both sides still return 1024 numbers. `embed_query` and
`embed` are separate methods for that reason.

**The vector arm needs a floor, because KNN always answers.** Without one, a
library asked about knitting returns the five records least unlike knitting and
stops ever saying no. `SIMILARITY_FLOOR` is 0.47, derived from eleven questions
against a whole collection — six it can answer and five it cannot — where the
lowest right answer scored 0.489 and the highest wrong one 0.454. It is a
property of this model and this instruction: change either and the number has
to be re-derived the same way, and the table in the constant's docstring is the
recipe.

#### Optional, and it degrades rather than fails

`LIBVOX_EMBEDDING_BASE_URL`, `LIBVOX_EMBEDDING_MODEL` and
`LIBVOX_EMBEDDING_API_KEY`. All three here, unlike `/libvox-core`, which falls
back to its chat model's gateway and key — this module has none to fall back
on.

Unset means the index is built without vectors and every search is answered by
the lexical arm, which is what this did for a year. A service that is down
during an import leaves rows with a `NULL` embedding, and those rows are still
found, still answered about, still correct — the column is nullable for exactly
that. `./deploy/up.sh` reports which of the three states a host is in,
including the half-configured one that does nothing at all.

**Vectors are inherited between generations.** Before embedding anything, the
pipeline copies each vector forward from the generation being replaced wherever
the embedded text is unchanged — matched on the three fields `text_of` reads,
so a corrected shelf mark costs nothing and a corrected title costs one
embedding. Without it a nightly reimport of a stable catalogue would pay the
full 3.2 minutes every night, and 37 for a collection of 100,000.

`search` returns five things, because a list of records cannot say why it is
empty — nor that it answered a different question:

| | Answers |
| :--- | :--- |
| `records` | Most relevant first |
| `index_status` | Whether an empty list means "we have nothing like that" or "I have not finished reading this catalogue" |
| `truncated` | Whether "three results" is really "the first three of many" |
| `undated_records` | Whether "14 books from 1992" is all of them, or all of the ones anybody dated |
| `searched_for` | Which word was looked up, when the one that was sent is not in this library |

`undated_records` is present only when `year_from` or `year_to` was passed, and
it counts within the search rather than across the catalogue: for "chess since 1990" the
useful number is how many chess records carry no year. A `copyright_date` of `0`
counts as no year, because that is what it is — one record in a real library
carries one, and without the guard it answers "published before 1900".

`searched_for` is present only when the search came back empty and a word was
repaired. **The candidates are this library's own vocabulary** — every word it
catalogued, collected into `discovery_vocabulary` at the end of each import,
generation by generation like everything else here. That is the whole point: a
spelling dictionary would happily correct a reader onto a word nobody here has
ever shelved, which is the failure the repair exists to prevent rather than a
smaller version of the same thing.

A trigram index shortlists and edit distance decides, in that order, because
trigram similarity alone gets the motivating case wrong:

```
similarity('xadres','xadrez')  = 0.556     -- pg_trgm's default limit is 0.6
levenshtein('xadres','xadrez') = 1
```

The rule is deliberately narrow. Words under five letters are never repaired —
`casa` and `cara` are both real, and both are somebody's search. A word the
library already has is not a typo. A tie refuses rather than guesses, which is
where the measured error rate of zero comes from: over 800 single-character
typos against both test catalogues, 86.8% were repaired, 6% refused as
ambiguous, and none repaired wrongly.

It runs **only when the search found nothing**, so a search that answers costs
exactly the queries it always did. And a repair that still finds nothing leaves
no trace: "I looked for another word and it is not here either" is not an answer
anybody asked for.

A library that was never synced reports `building`, not `ready`. Saying `ready`
with nothing indexed turns "I have not read it yet" into a confident "we do not
have it", which is the failure `index_status` exists to prevent.

### Three refusals

- **A search with no terms.** "Everything you have" answered with an arbitrary
  twenty records is presented to the patron as the collection. An agent asked
  for a catalogue listing will try `query=a` and then `query=the`; both should
  come back empty rather than plausible.
- **A limit outside 1–20.** Refused, not clamped — silently returning 20 of 100
  reads as "that is all of them". The ceiling matches
  `libvox_mcp.server.MAX_RESULTS`, and a test asserts they stay equal.
- **A wildcard a patron typed.** `%` and `_` are escaped before reaching
  `ILIKE`. `/libvox-driver-koha` records a bare `%` matching an entire live
  catalog; here it matches nothing.

## Where the list of libraries comes from

`/libvox-portal` owns the registry, and reading it costs nothing this module
did not already have:

```sql
SELECT tenant_id FROM tenants
 WHERE ils_configured_at IS NOT NULL AND status <> 'suspended'
```

**No privileged connection.** `tenant_ils` carries row-level security, so
listing across libraries used to mean a role that bypasses it. `tenants`
deliberately carries no policy — it is the table that *defines* which tenants
exist, and a policy there would be circular — so the unprivileged role reads it
and `LIBVOX_TENANT_DB_ADMIN_DSN` left the operating path entirely.
`ils_configured_at` is maintained by the portal in the same transaction as the
credential, so it cannot disagree with what is stored.

A `provisioning` library **is** synced. It has to be indexed before it can
answer anyone, and waiting for an operator to mark it `active` would mean the
first thing a reader meets is an empty catalog — which is the state `building`
exists to describe, not one to arrange on purpose. Only `suspended` is skipped:
that one is a decision rather than a stage.

## Tenant isolation

Every query must run inside a tenant scope:

```python
with connect(settings.dsn) as conn:
    with tenant_scope(conn, "lib-furg") as scoped:
        ...
```

`tenant_scope` issues `SET LOCAL`, so the binding dies with the transaction and
cannot leak into the next one on a pooled connection. Outside a scope the
policies match nothing and the tables read empty — **fail closed**, never
cross-tenant.

## Migrations

Numbered `.sql` files in `migrations/`, applied in order, recorded in
`schema_migrations` with a checksum. No ORM and no migration framework: this
schema is SQL-first — a generated `tsvector`, weighted full-text, and row-level
security are all things you would hand-write anyway.

Editing an already-applied migration is refused. Add a new one; a change that
silently never runs on a deployed database is worse than a noisy error.

## Drivers

A driver's whole job is to hand the pipeline pages of `BiblioRecord`. The
interface is one method — `fetch_page(cursor)` — so a driver is replaceable
without the pipeline noticing.

| Driver | Use |
| :--- | :--- |
| `libvox_driver_koha.KohaDriver` | A live Koha instance |
| `drivers.fixture.FixtureDriver` | `tests/koha_example.json`, no network |

### What a second ILS costs

Koha is the only one implemented, and that is a fact about how much has been
written rather than about the shape of this module. A FOLIO or a Pergamum
driver would touch **four places, none of them the pipeline**:

| | |
| :--- | :--- |
| the driver package | `fetch_page(cursor)` returning a `Page` of `BiblioRecord`, and the normalization for that ILS |
| `DRIVERS` in `__main__.py` | the `--ils` choice for the standalone path, its builder, and the environment variables it requires |
| `BY_ILS_TYPE` in `__main__.py` | the same builder keyed by the `ils_type` the Tenant DB stores |
| `tenant set --ils` | the set of ILSs a library can actually *be* |

**Two of those are the same set spelled twice**, and deliberately not one.
`DRIVERS` also holds `fixture`, which is a way to drive an import and not a
thing a library can be configured as — offering it where credentials are stored
would let somebody register a library whose catalogue is a JSON file. But
`tenant set --ils` and `BY_ILS_TYPE` are the same list, one as a literal and
one as keys, and a second driver has to be added to both. `choices=sorted(
BY_ILS_TYPE)` would make it one; it has not been worth doing with a single
entry.

The portal constrains `ils_type` to the same set from its own side, so that is
a fourth place with the same knowledge — outside this module, and the reason
`BY_ILS_TYPE` raises rather than guessing when it meets a type it has no
builder for.

**And one thing would have to move first.** `BiblioRecord`, `Page`,
`normalize_page` and `DEFAULT_PAGE_SIZE` are imported from
`libvox_driver_koha` — the domain contract lives inside the first driver rather
than beside the pipeline that consumes it. It costs nothing with one driver and
becomes absurd with two, where a FOLIO driver would be importing its types from
Koha's package. The fix is a package for the contract, or the contract moving
here; either is a rename and no logic.

**Koha knowledge is not in this module.** It lives in `/libvox-driver-koha`,
which this package imports — `drivers/__init__.py` is a re-export and nothing
else. It used to be a copy: a second `BiblioRecord`, a second normalizer, a
second set of pagination rules, comments and all. Two definitions of what a
title is drift the moment one of them is edited, and then the index and the
live answer disagree about the same book. One place, or the disagreement is
only a matter of time.

Importing the driver also means its error taxonomy arrives with it. A Koha
permission failure is no longer an `HTTPStatusError` traceback: it names the
flag Koha asked for, which is what turns a dead end into an instruction.

The fixture driver reuses the *real* `normalize_page`, so a test that passes
against it exercises the actual normalization against actual Koha payloads. A
fixture driver with its own parsing would prove nothing.

### Where connection details come from

Which ILS a library runs, and how to reach it, is **tenant configuration**.
There are two ways to supply it, and they are alternatives:

```bash
# Multi-tenant: resolved from the database, secret decrypted on read
python -m libvox_sync extract --tenant lib-furg

# Ad-hoc: a single library, straight from the environment
python -m libvox_sync extract --ils koha
```

`--ils` has no default. An implicit "assume Koha" reads fine with one driver
and becomes a silent wrong-ILS bug with two. The registry in `__main__.py` maps
each choice to its builder and its required environment variables, so adding
FOLIO is one entry plus the driver — and only implemented drivers are listed,
because putting `folio` in `--help` before a FOLIO driver exists advertises a
choice that cannot work.

## Two databases, and why

This worker reads two. They are separate databases on one server, not two
schemas in one database — one writer per database is the rule, and it is about
databases rather than servers:

| | Discovery Index | Tenant DB |
| :--- | :--- | :--- |
| Owner | this module | `/libvox-portal` |
| Database | `libvox_index` | `libvox_tenant` |
| Holds | records, generations, index state | libraries, credentials, channels |
| Migrated by | `python -m libvox_sync migrate` | `composer migrate`, from the portal |
| DSN | `LIBVOX_DB_DSN` | `LIBVOX_TENANT_DB_DSN` |

They shared a name with their servers once — the index was simply `libvox`,
which was unambiguous while it had a server to itself.

**Nothing joins across them, and nothing needs to.** `credentials.py` reads
`tenant_ils` on its own — `store`, `fetch` and `list_tenants` are single-table
statements — and a credential is resolved once, before the index is touched.
PostgreSQL cannot join across databases, and that limit never comes up here.

Which is why the split cost this module one extra connection and no SQL change
at all: every function in `credentials.py` already took the connection as an
argument, so moving the table was a matter of handing it a different one.

`migrate` never touches the Tenant DB. If `tenant list` says the table does not
exist, the portal has not been migrated:

```bash
cd ../libvox-portal && ./portal.sh migrate
```

## Credentials

Stored encrypted with `pgcrypto`, in the Tenant DB. The key comes from
`LIBVOX_CRYPTO_KEY` in the environment and is **never written to the database it
protects** — and both sides need the same value, because `/libvox-portal` writes
this ciphertext and this module reads it.

```bash
export LIBVOX_CRYPTO_KEY="$(openssl rand -base64 32)"

# The secret comes from KOHA_CLIENT_SECRET, or a hidden prompt if unset.
# There is deliberately no --client-secret flag.
python -m libvox_sync tenant set \
  --tenant lib-furg --ils koha \
  --url http://localhost:8081 --client-id eaa7f169-...

python -m libvox_sync tenant list      # inventory; cannot return a secret
python -m libvox_sync extract --tenant lib-furg
```

What lands in the table:

```
 tenant_id |              client_id               |          client_secret_enc
-----------+--------------------------------------+--------------------------------------
 lib-furg  | eaa7f169-1275-4ec9-8230-d55a58b4e88f | \xc30d04090302a3e5a87f541142b86fd2...
```

`client_id` is plaintext on purpose: in OAuth2 client-credentials it is an
identifier, the equivalent of a username. Encrypting it would imply a
protection it does not need and does not get.

### `tenant set` and `tenant list` are the standalone path

`/libvox-portal` is the production writer, and it issues the identical
statement, so the two cannot drift. These two commands drive the worker without a portal running, which is what
makes an import testable on its own.

`list_tenants` is protected by its SELECT list and by nothing else, which is
weaker than it looks: the next edit that adds a column defeats it. The portal
answers the same question without reading the table at all — it keeps a
timestamp on the registry, written in the same transaction as the credential.

### Moving the table, if you have rows from before

`tenant_ils` used to live in the Discovery Index. Migration 003 drops it, and
that drop is irreversible: the ciphertext goes with the table. Migration 003
refuses to run while the table still has rows, so the order is enforced rather
than merely documented.

The copy is byte-for-byte. `client_secret_enc` is pgcrypto ciphertext under the
same `LIBVOX_CRYPTO_KEY` on both sides, so nothing is decrypted, nothing is
re-encrypted, and the key never enters the move. Binary `COPY` keeps `bytea`
exact with no hex round-trip.

```bash
# Both databases are on the one server now, so this is one psql session per
# side rather than a pipe between two containers.
DB="docker compose -f ../libvox-db/docker-compose.yml exec -T postgres psql -U libvox"

# 1. tenant_ils gained a foreign key when it moved: the Tenant DB has a
#    registry of libraries, which the Discovery Index never did. Seed it first.
$DB -d libvox_index -tAc "SELECT DISTINCT tenant_id FROM tenant_ils" | while read -r t; do
  $DB -d libvox_tenant -c \
    "INSERT INTO tenants (tenant_id, display_name, status) VALUES ('$t', '$t', 'active')
     ON CONFLICT (tenant_id) DO NOTHING"
done

# 2. Copy.
$DB -d libvox_index  -c "COPY tenant_ils TO STDOUT (FORMAT binary)" \
| $DB -d libvox_tenant -c "COPY tenant_ils FROM STDIN (FORMAT binary)"

# 3. Verify before destroying anything. Same key, other side.
$DB -d libvox_tenant -tAc \
  "SELECT tenant_id, pgp_sym_decrypt(client_secret_enc, '$LIBVOX_CRYPTO_KEY') FROM tenant_ils"

# 4. Only now.
$DB -d libvox_index -c "DELETE FROM tenant_ils"
python -m libvox_sync migrate
```

A database old enough to need this predates both the consolidation and the
rename, so its index is on its own server and is called `libvox`. Point the two
sides wherever they actually are.

On a fresh database this creates the table (002) and immediately drops it (003).
That looks like an oversight and is not: editing an applied migration raises
`MigrationError`, and a migration history that silently rewrites itself is a
worse failure than a redundant pair of steps.

### What this defends, and what it does not

**Defends:** a database dump, a backup, a read replica, an operator with
`SELECT` on the table, a screenshot of a query result. Those are the realistic
ways a credential store leaks, and in every one of them the secret is
ciphertext.

**What it does not defend:** a compromise of the application process, which by
definition holds the key. A KMS or Vault, where the key never enters the
application's memory, is the tool for that threat.

**The strongest control is not here at all.** The API key a library issues to
LibVox should be **read-only** — in Koha, a patron with the `catalogue` flag
and nothing more (see [`docs/install_koha.md`](../docs/install_koha.md)).
Nothing in LibVox ever writes to an ILS. With a read-only key, a total
compromise of this platform cannot alter or delete anyone's catalog, and that
holds regardless of how well the encryption is done.

Note what LibVox *cannot* do: verify that. A Koha API key inherits its patron's
permissions, so a library that reuses an existing staff account hands over far
more than the catalogue flag. The platform stores whatever it is given.

### Operational notes

- **The key is not recoverable.** Credentials written under one key cannot be
  read with another; `tenant set` would have to be re-run for every library.
- **Rotating the key** means decrypting with the old and re-encrypting with the
  new in one transaction. The table holds one row per library — dozens, not
  millions — so this is a short script, not a migration strategy.
- **A wrong key fails clearly**, because "wrong key" and "corrupt data" are
  indistinguishable to pgcrypto and it should not imply the latter:

  ```
  cannot decrypt credentials for 'lib-furg': wrong LIBVOX_CRYPTO_KEY,
  or the stored value is damaged
  ```

- **`ILSCredentials.__repr__` redacts the secret**, because credentials reach
  tracebacks and log lines by accident and the repr is the one place that can
  be fixed for all of them at once.

### What Koha's REST API actually does

Field notes about **the one driver that exists**, not about this module: what
follows is true of Koha and says nothing about the next ILS. Verified against a
running instance (Koha Testing Docker, August 2026), not assumed:

- `_page` / `_per_page` paginate, 1-indexed and non-overlapping.
- **No pagination headers.** No `X-Total-Count`, no `Link` — the total is
  unknowable up front, so extraction runs until a short page arrives. An
  exactly-full final page costs one extra request that returns `[]`.
- A page past the end returns `[]`, not an error.
- OAuth2 tokens last 3600s, which any real catalog import will outlive. The
  driver refreshes 60s ahead of expiry.
- **`q={"timestamp":{">":…}}` returns HTTP 500.** The syntax is fine —
  `{"title":{"like":…}}` and `{"biblio_id":{">":…}}` both work — the
  `timestamp` field specifically is not queryable, so "what changed since" is a
  question for OAI-PMH, which Koha ships and which also reports deletions.

### Normalization decisions

The Koha driver's, and the same caveat: normalization belongs to whichever
driver produced the record, and this table is one of them. MARC data arrives
with ISBD punctuation and packed fields. The rules, each covered by a test
against real records:

| Input | Output | Why |
| :--- | :--- | :--- |
| `"E Street shuffle :"` | `"E Street shuffle"` | Trailing ISBD separator, not part of the title |
| `"Fine, Reuben,"` | `"Fine, Reuben"` | Trailing comma is punctuation |
| `"Mastronarde, Donald J."` | *unchanged* | A trailing period is indistinguishable from an initial; mangling a name is worse than a stray dot |
| `"0131103709 \| 0131103628 (pbk.)"` | `"0131103709"` | Koha packs several annotated ISBNs into one string |
| `copyright_date` null | falls back to `publication_year` | Which one is filled varies by installation |

Two kinds of record are refused rather than half-normalized:

- **OPAC-suppressed.** A record the library hides from its own catalog must not
  surface in a conversational search.
- **No title.** Nothing to match on, and the index column is `NOT NULL`.

Both are counted, not swallowed — a silent shortfall in the index is the hard
kind of bug to notice later.

## Tests

```bash
cd ../libvox-db && ./db.sh && cd -
pytest
```

And the linter, which `environment.yml` installs through `requirements-dev.txt`:

```bash
ruff check .
```


They skip rather than fail when no database is reachable. One test compares the
index columns against `libvox_driver_koha.BiblioRecord` and another against
`libvox_mcp.models.CatalogRecord`, to catch drift between what a driver
produces, what Sync writes and what MCP returns. The MCP one skips if that
module is not installed; the driver one does not, because that dependency is
hard.

> **Tests run against `_test` databases, never development ones.** Both
> databases this module touches have one, and `tests/conftest.py` derives the
> DSN rather than adding a variable to forget.
>
> For the Tenant DB the damage would be permanent: a stored secret is
> ciphertext with no read path anywhere in the system, so truncating it means
> asking every library for its API key again.
>
> For the Discovery Index the reasoning was got wrong first. It is derived
> data, so truncating it looked harmless — true at 436 sample records, false at
> half a million, where the rebuild is half an hour of paginated requests
> against someone else's library server. The symptom was worse than the cost:
> `pytest` quietly emptying an index imported minutes earlier, and searches
> then returning nothing with nothing to say why.
>
> `/libvox-db`'s `initdb/02-databases.sql` creates `libvox_index_test`, and
> `initdb` runs once, on an empty volume. A volume created before that file
> named it will not have it, and `./db.sh` prints `absent` beside the ones that
> are missing rather than letting a test run discover it. The cure is
> `./db.sh reset`, which is destructive and asks you to type the word.

## Testing by hand

The two halves meet at `full`. The steps below still walk them separately,
because knowing which one is broken is worth more than one command that either
works or does not. Every command was run against Koha Testing Docker and
PostgreSQL 17.

The short version, once the index is up and a library has credentials stored:

```bash
python -m libvox_sync full --tenant lib-furg
python -m libvox_sync status --tenant lib-furg     # ready, generation 1
python -m libvox_sync full --tenant lib-furg       # generation 2, same records
```

The second run proves the part that matters: it supersedes rather than appends,
and search answers from the old generation right up until the swap.

Offline, with no Koha at all:

```bash
python -m libvox_sync full --tenant lib-teste \
  --ils fixture --fixture tests/koha_example.json
```

### 1. Extraction, offline

No database and no network. The fastest way to see what normalization actually
does to a catalog:

```bash
python -m libvox_sync extract --ils fixture --fixture tests/koha_example.json --limit 4
```

```
      1  E Street shuffle                     Heylin, Clinton.       —
      2  The ideas behind the chess openings  Fine, Reuben           1990
      3  Introduction to Attic Greek          Mastronarde, Donald J. 1993
      4  The C programming language           Kernighan, Brian W.    1988

4 record(s) normalized from fixture. Nothing was written.
```

Compare against the raw JSON to see the rules land: `"E Street shuffle :"` lost
its ISBD colon, `"Fine, Reuben,"` its comma, and `"Mastronarde, Donald J."` kept
its period.

### 2. Extraction, against a live Koha

`extract` is a dry run — it paginates, authenticates, normalizes, and writes
nothing.

```bash
export KOHA_URL=http://localhost:8081
export KOHA_CLIENT_ID=...      # from docs/install_koha.md
export KOHA_CLIENT_SECRET=...

python -m libvox_sync extract --ils koha --page-size 25 --limit 10
```

Credentials come from the environment and are never accepted as arguments:
`/proc/<pid>/cmdline` exposes them to any local process. Omitting them fails
with a message naming the missing variables rather than a stack trace.

Drop `--limit` to walk the whole catalog and watch pagination work — with
`LIBVOX_LOG_LEVEL=INFO` each page reports rows fetched, indexed, and skipped.
Against the KTD sample catalog that is 436 records across 18 pages.

### 3. Bring up the index

```bash
cd ../libvox-db && ./db.sh && cd -
python -m libvox_sync migrate        # "applied migrations: 1, 2, … 8"
python -m libvox_sync migrate        # "schema already up to date"
```

Inspect what was created:

```bash
docker compose -f ../libvox-db/docker-compose.yml exec postgres \
  psql -U libvox -d libvox_index \
  -c "\dt" \
  -c "SELECT tablename, policyname FROM pg_policies WHERE schemaname='public';"
```

Both tables should appear with a `tenant_isolation` policy. A table listed
without one is a table with no tenant isolation.

### 4. Full-text search, by hand

Connect as `libvox_app` — the unprivileged role. Every statement touching
tenant data runs inside a transaction that sets `libvox.tenant_id`; that is
what the policies read.

```bash
docker compose exec -e PGPASSWORD=libvox_app postgres psql -U libvox_app -d libvox
```

```sql
BEGIN;
SELECT set_config('libvox.tenant_id', 'lib-furg', true);

INSERT INTO discovery_biblio (tenant_id, generation, biblio_id, title, author)
VALUES ('lib-furg', 1, 1, 'Dom Casmurro',     'Machado de Assis'),
       ('lib-furg', 1, 2, 'Romance da terra', 'Silva');

INSERT INTO discovery_tenant (tenant_id, active_generation, index_status)
VALUES ('lib-furg', 1, 'ready');
COMMIT;

BEGIN;
SELECT set_config('libvox.tenant_id', 'lib-furg', true);
SELECT biblio_id, title
  FROM discovery_active
 WHERE search @@ plainto_tsquery('libvox_search', 'romances');
COMMIT;
```

The plural query matches the singular title — Portuguese stemming, which is the
whole reason for a `tsvector` rather than `LIKE`. The same query written
without accents matches too; see *Accents*.

That statement is the **lexical arm alone**, which is what makes it useful for
telling the two apart: a word it finds here and a word the CLI finds are
different facts once embeddings are configured. The vector arm has no
by-hand equivalent, because writing one means embedding the query first.

```
 biblio_id |      title
-----------+------------------
         2 | Romance da terra
```

### 5. Prove the isolation is real

Two checks. Neither should ever return a row.

```sql
-- Another tenant asking for this one's records, explicitly:
BEGIN;
SELECT set_config('libvox.tenant_id', 'lib-ibict', true);
SELECT count(*) FROM discovery_biblio WHERE tenant_id = 'lib-furg';   -- 0
COMMIT;
```

```sql
-- No scope at all — fail closed, never cross-tenant:
SELECT count(*) FROM discovery_biblio;                                 -- 0
```

If either returns a non-zero count, you are connected as a superuser and
row-level security is not in force. Check with:

```sql
SELECT current_user, rolsuper, rolbypassrls
  FROM pg_roles WHERE rolname = current_user;
```

`rolsuper` and `rolbypassrls` must both be false. This is the premise every
other isolation guarantee rests on, which is why a test asserts it too.

### 6. Read the tenant's state

```bash
python -m libvox_sync status --tenant lib-furg
```

```
tenant:      lib-furg
status:      ready
generation:  1
records:     2
last sync:   -
```

An unknown tenant exits non-zero with `unknown tenant (never synced)`, so this
works in a health check.

### 7. Reset

```bash
# Wipe tenant data, keep the schema (as admin, which bypasses RLS):
docker compose -f ../libvox-db/docker-compose.yml exec postgres \
  psql -U libvox -d libvox_index \
  -c "TRUNCATE discovery_biblio, discovery_tenant;"

# Or start this database over, migrations included. Dropping and recreating one
# database rather than resetting the server: `./db.sh reset` deletes all six,
# including the registry and every stored conversation, which is a far bigger
# hammer than an index that only ever holds what an import can rebuild.
DB="docker compose -f ../libvox-db/docker-compose.yml exec -T postgres psql -U libvox -d postgres"
$DB -c "DROP DATABASE libvox_index;"
$DB -c "CREATE DATABASE libvox_index OWNER libvox;"
$DB -c "REVOKE CONNECT ON DATABASE libvox_index FROM PUBLIC;"
$DB -c "GRANT CONNECT ON DATABASE libvox_index TO libvox_app;"
python -m libvox_sync migrate
```

### Running it all in the container

Same commands, one wrapper — useful for checking that the image itself works:

```bash
docker compose build sync                       # after any source change
docker compose run --rm sync migrate
docker compose run --rm sync status --tenant lib-furg
```

Two things differ inside the container:

**Test fixtures are not in the image.** `tests/` is deliberately not copied —
test data has no business in a deployable image. Mount it when you want the
offline extract:

```bash
docker compose run --rm -v "$PWD/tests:/app/libvox-sync/tests:ro" \
  sync extract --ils fixture --fixture /app/libvox-sync/tests/koha_example.json --limit 5
```

**The image is built from the repository root.** This module imports
`/libvox-driver-koha`, and a Docker build context cannot reach outside itself,
so `docker-compose.yml` sets `context: ..` and the root `.dockerignore` keeps
that context to the two directories that are actually needed. Both packages are
installed editable under `/app`, which is why the paths above carry the
`libvox-sync/` prefix — and why the migration runner still finds `migrations/`
next to its own source rather than in site-packages.

**`localhost` is the container.** A Koha running on the host is reachable at
`host.docker.internal` (or the host's LAN address), not `localhost`.

**`LIBVOX_CRYPTO_KEY` has to reach the container.** Compose passes it through
from your shell or `.env`; without it, anything that resolves a library's
stored credentials stops with instructions rather than a stack trace. It is not
made mandatory the way the portal makes it, because `migrate` and the fixture
path have no use for a key and refusing to start over one they will never touch
is a bad trade.

**Rebuild after a source change.** `COPY . .` bakes the source in, and compose
happily reuses a container that is already running — so a command you just
added is missing from `--help` and a bug you just fixed still happens.

## Two numbers that look alike and are not

`--page-size` is how many records are asked of the **library's server** per
request. `--batch-size` is how many are written to **our own Postgres** before
committing. Confusing them costs somebody else's server.

Each batch commits on its own — safe because the generation is not live yet —
which makes the batch, not the page, the granularity at which a running import
becomes **visible**. That matters now that `/libvox-portal` shows progress: at
500 a 436-record library displayed nothing at all until it finished, because
the batch never filled. It is 25.

Lowering the batch costs a few more small transactions here and **not one extra
request to the library**. Lowering the page size is the one that multiplies
requests against someone else's machine — 1,462 records go from 15 requests to
59 — and it does not make the progress finer on its own.

## Starting the module

```bash
./sync.sh            # index up, migrated if behind, and the worker running
./sync.sh status     # what is up and what the index holds, changing nothing
./sync.sh down       # stop this module's services; the index keeps its data
./sync.sh logs
```

Cold, that is one command for four things: the Discovery Index, its migrations,
the Tenant DB that holds the outbox, and the worker.

**Two of those belong to other people, and it says so.** The outbox lives in
`/libvox-portal`'s database, so a worker without it is a worker with nothing to
hear — `sync.sh` starts that one container and prints a line saying it did.
`down` deliberately leaves it running: it is not this module's to stop.

Migrations run only when the count on disk is ahead of `schema_migrations`.
`migrate` is idempotent and would be safe every time, but it builds the image
first, and paying a build on every start to be told *"schema already up to
date"* is how a startup script becomes one people skip.

It also warns about something that cost an hour: **a bridge left behind by a
previous Docker daemon, sharing a subnet with a live one.** The stale interface
is `DOWN`, the route picks it anyway, and the host gets `no route to host` while
the container answers normally from inside itself. Seen after Docker's data-root
was repointed — published ports accepted connections and reset them, every
host-side test skipped, and no container log said a word. A warning rather than
a refusal: containers reach each other over the compose network and are fine. It
is the host — the test suites, `./core.sh`, a browser — that cannot get in.

## The one long-running thing here

`sync.sh` hands the worker over to this rather than reimplementing it, so the
checks below have one home.

```bash
./worker.sh          # checks, builds, starts, and says what is waiting
./worker.sh logs
./worker.sh stop
./worker.sh check    # the checks alone, changing nothing
```

**Every check in it is a failure that happened**, not one that was imagined.
That is worth listing, because knowing what it refuses to start over is most of
what the script is for:

| It refuses when | Because |
| :--- | :--- |
| `LIBVOX_CRYPTO_KEY` is missing | Compose reads it from `.env`, this module had none, and the container restart-looped on the same message. It copies the portal's, since byte-identical is the requirement anyway. |
| **The key differs from the portal's** | The one worth automating. The worker starts perfectly well and then fails every import with a decryption error, one library at a time, hours later. |
| Either database is down | Named separately: the outbox it listens on is in the Tenant DB, and the index it writes is not. |
| The outbox rejects `SyncRequested` | The portal's migration has not run, so every press of the button is refused by a `CHECK` the worker never sees. |
| It did not stay up | `up -d` reports success for a container that dies a second later. It waits, looks, and prints the log instead. |

It also **rebuilds every time** — not because that is cheap, but because the
alternative already happened: a whole import ran on an image from before the
fix that made the driver pace itself, and nothing said so.

**The `sync` and `worker` services share one image**, named explicitly in the
compose file rather than left to compose's default of one per service. They are
the same program — `python -m libvox_sync` — differing only in `command`,
`restart` and `profiles`, and the two names could drift because different
scripts built them: `./sync.sh` built one, `./worker.sh` the other.

That drift is why this is written down. The `sync` image once held three
migrations while `worker` was current, so `migrate` applied the three it could
see and reported *"schema already up to date"* — and an import died hours later
on a `series` column that migration 004 would have created. One name means
either build updates the one thing both services run.

Underneath it is just compose, if you would rather:

```bash
docker compose --profile worker up -d --build worker
# or, on the host: python -m libvox_sync worker
```

Every other subcommand finishes. This one waits for a librarian to press
**Atualizar índice** in `/libvox-portal` and imports that library's catalogue.

**It consumes `SyncRequested` and nothing else.** `TenantUpdated` fires on every
configuration change — the widget's colour included — and re-importing a
catalogue for one of those would be hundreds of requests to a library's own
server because somebody picked a new shade of blue. They are available to any consumer that wants them.

**LISTEN for latency, the table for the guarantee.** `pg_notify` is delivered on
commit and dropped when nobody is connected, so a worker restarting when the
button was pressed would never hear it. The backstop poll is what makes that a
delay rather than a loss.

Its interval is **five seconds**, and that is not a free choice: `/libvox-portal`
tells a librarian nobody is answering after ten, so a longer poll here would
make that screen accuse a worker that was seconds from acting. One query against
a partial index, so the rate costs nothing worth measuring.

**A claim is stamped before the import, not after.** Stamping afterwards reads
better until a worker is killed mid-import: the request stays pending forever,
the unique index refuses every new one, and the button never enables again.
Stamping first costs one lost request when a worker dies, and the fix for that
is pressing the button again.

**A failure is logged, and the loop carries on.** One library's server being
down must not stop the next press being answered — and `_record_failure` has
already written the reason to `discovery_tenant.last_error`, which is what the
librarian reads. Note that `_driver_from_tenant` raises `SystemExit` for a
library with no credentials: right for a command, fatal for a loop, and not
caught by an `except Exception`.

It refuses to start without `LIBVOX_CRYPTO_KEY`. Every job decrypts a library's
ILS credentials, and a worker that started anyway would consume each request
and fail it — losing the request and re-disabling the button for nothing.

### Knowing it actually populated something

A button that reports success and a catalogue that grew are two different
facts, and only the second one matters. Three ways to see it, from the loudest
to the most conclusive.

**The worker says what it is doing.**

```bash
./worker.sh logs
```

```
lib-local: importing, asked for in event 161
koha page 5: 36 rows, 36 usable, 0 skipped
lib-local: generation 3 live — 436 record(s), 0 skipped
```

**The Acervo screen reads the index on every load.** Reload after pressing:
the state moves from *em dia* to *sendo construído* and back, with the record
count and the time changing. A failure puts the sentence the ILS answered right
there, because it is the librarian's own server and hers to fix.

**The two sides together, which is the one that proves it.**

```bash
docker compose -f ../libvox-db/docker-compose.yml exec -T postgres \
  psql -U libvox -d libvox_tenant -tAc \
  "SELECT tenant_id, occurred_at, consumed_at FROM tenant_events
    WHERE event='SyncRequested' ORDER BY id DESC LIMIT 5"

docker compose -f ../libvox-db/docker-compose.yml exec -T postgres \
  psql -U libvox -d libvox_index -tAc \
  "SELECT tenant_id, index_status, active_generation, last_sync_at
     FROM discovery_tenant ORDER BY 1"
```

`consumed_at` filled says the worker **took** the request. `active_generation`
having gone up says it **published** one. Either without the other is the thing
worth catching: a claim with no new generation is an import that failed, and
`last_error` says why.

Two things that look like the button being broken and are not:

- **The worker has to be the container, not a host process.** A library reached
  through `host.docker.internal` — a Koha Testing Docker on the same machine —
  does not resolve from the host, and the import fails with
  `[Errno -2] Name or service not known`.
- **A failed re-import leaves `stale`, not `building`,** and the previous
  generation keeps answering. That is deliberate: calling it `building` would
  have MARC.IA apologise for an index that works.

## Running a sync, start to finish

Everything above explains *why*. This is the order to type things in.

### Without a Koha at all

Proves the whole pipeline against a captured response. No credential, no
portal, no network:

```bash
cd libvox-sync
conda activate libvox-sync

python -m libvox_sync migrate
python -m libvox_sync full --tenant lib-teste \
  --ils fixture --fixture tests/koha_example.json
python -m libvox_sync status --tenant lib-teste
```

`status` should answer `ready`, generation 1, 20 records. Run `full` again and
the generation becomes 2 — that is the swap working, and it is the part worth
watching.

### With a real library

The order matters, because each step depends on the one before it.

**1. Register the library**, at `http://localhost:8090/admin/tenants`. The
`tenant_id` has to match `^lib-[a-z0-9]…` — `lib-furg`, not `furg`. A row in
`tenants` is what `tenant_ils` references, so this cannot be skipped.

**2. Store its ILS credentials**, at `http://localhost:8090/app`. The panel is
the write path for a secret; `tenant set` below is the standalone shortcut.

**3. Export the crypto key — the same one the portal used.**

```bash
export LIBVOX_CRYPTO_KEY="$(grep '^LIBVOX_CRYPTO_KEY=' ../libvox-portal/.env | cut -d= -f2-)"
```

Reading the portal's `.env` is a local-development convenience and nothing
more: it works because both modules sit in one checkout. In any deployment the
key comes from your secrets manager, and the two components get it
independently.

If the key is wrong, `full` stops with *"wrong `LIBVOX_CRYPTO_KEY`, or the
stored value is damaged"*. pgcrypto cannot tell those two apart, and the
message says so rather than guessing.

**4. Import.**

```bash
python -m libvox_sync full --tenant lib-furg   # one library
python -m libvox_sync full --all               # every configured library
```

`--all` takes the list from the portal's registry. A library whose server is
down does not stop the others: the exit code counts the failures, and `status
--tenant NAME` says what happened to each.

### Look before you import

`extract` is a rehearsal — it paginates, authenticates, normalizes and **writes
nothing**:

```bash
python -m libvox_sync extract --tenant lib-furg --limit 10
```

This is where to start when setting a library up. A credential that is wrong,
or missing Koha's `catalogue` flag, fails here without touching the index.

### In the container

```bash
cd ../libvox-db && ./db.sh && cd -
docker compose build sync          # after any source change
docker compose run --rm sync full --tenant lib-furg
```

`LIBVOX_CRYPTO_KEY` has to be in your shell or `.env`; compose passes it
through. The rebuild is not optional: the image bakes the source in and compose
reuses a container that is already running, so skipping it means debugging code
that is not the code on disk.

### Two refusals you will meet

**An empty catalog.** Zero records is an API key without Koha's `catalogue`
flag far more often than a library without books, and publishing it would have
MARC.IA state confidently that the library holds nothing. `--allow-empty` if it
is genuinely empty.

**A second import while one is running.** The second exits cleanly saying so.

And there is no `--limit` on `full`, deliberately — see
[What a full import does, and refuses to do](#what-a-full-import-does-and-refuses-to-do).
