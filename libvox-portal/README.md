# /libvox-portal

The Management Plane: where a library registers, hands LibVox the key to its
catalog, and decides how MARC.IA introduces herself.

Two panels in one Laravel application. **`/admin`** belongs to LibVox staff, who
register libraries and read conversations. **`/app`** belongs to a librarian,
who configures one library: its catalog connection, its widget, its channels.

The panel is the backend. Filament renders on the server and writes through
Eloquent, so this application holds sole write access to the Tenant DB and
publishes `TenantUpdated` itself.

**Laravel 13.29, Filament 5.7, PHP 8.5**, Shield for roles,
`spatie/activitylog`, `spatie/settings`, `filament-impersonate`, and Pest.

## Setup

```bash
composer install
cp .env.example .env
php artisan key:generate

# Encrypts stored ILS secrets. Keep it: credentials stored under one key are
# read back with the same one, and /libvox-sync must be given the same value.
echo "LIBVOX_CRYPTO_KEY=$(openssl rand -base64 32)" >> .env

cd ../libvox-db && ./db.sh && cd -   # the one PostgreSQL server
composer migrate                 # artisan migrate --database=pgsql_admin
php artisan db:seed              # roles, plus the first operator account

php artisan serve                # http://localhost:8000
```

`composer setup` does all of that in one command.

For the container, **`./portal.sh`** builds the image, restarts it, and waits
until it answers on **8090**. It always does both halves, because the Dockerfile
bakes the source in and compose reuses a running container — so a build alone
leaves the new image sitting there while the old code keeps serving.

For iterating on code, `php artisan serve` on the host is faster and needs no
build; `.env` already points at the same database.

### The stylesheet is compiled

The panels load a Tailwind theme built from `resources/css/filament/theme.css`,
shared by both. Colours still differ per panel — those are custom properties
Filament emits at runtime from each panel's `->colors()`.

The build runs inside the image, in a node stage, so the compiled stylesheet
ships with the code that uses it. `./portal.sh` covers it; on the host,
`npm ci && npm run build`.

### Signing in the first time

Migrations create the tables and seed nothing, so a freshly built database has
no account at all. `./portal.sh seed` — or `php artisan db:seed` on the host —
creates the two roles and one operator:

| | |
| :--- | :--- |
| **`/admin`** | `operator@libvox.local` / `password` |

`./portal.sh` says which of the two states it found rather than printing that
line unconditionally. It used to print it always, which was true of every
database the script had met until one was rebuilt from empty and the panel came
up advertising an account nobody had created.

Seeding is not part of `up`, though both seeders are idempotent and it would
work: it creates an account whose password is `password`, and doing that unasked
on every start is exactly what the warning below says not to do.

A seeded password is a development convenience, and naming it as one is more
honest than a random string nobody can sign in with. Set your own before seeding
anywhere the panel is reachable by others:

```bash
LIBVOX_OPERATOR_EMAIL=you@example.org \
LIBVOX_OPERATOR_PASSWORD="$(openssl rand -base64 24)" \
php artisan db:seed
```

**Access to `/app` is granted by an operator**, because it hands a person the
configuration of one library. Register a library at **`/admin/tenants`**, then
create its librarian at **`/admin/users`** — the form asks for the library, and
requires it for that role.

A librarian changes her own password at **`/app/profile`**. That page edits the
password and nothing else: the e-mail address is the login and the operator's
record of who holds a library's account.

## Three databases, one of them written

This application **writes** one database and **reads** two.

| | Tenant DB | Discovery Index | Conversation log |
| :--- | :--- | :--- | :--- |
| Owner | this module | `/libvox-sync` | `/libvox-core` |
| Database | `libvox_tenant` | `libvox_index` | `libvox_conversations` |
| Here | read and write | read | read |

Nothing joins across them. `/libvox-sync` reads `tenant_ils` on its own and
resolves a credential before the index is touched.

Reading the other two is what makes two screens possible: the Catalogue screen
shows a librarian what the index holds for her library, and the Conversations
screen shows an operator what MARC.IA answered. The one-writer rule is about
writing, and the symmetry holds — `/libvox-core` reads the Tenant DB the same
way round.

Three things make that safe:

- **Each connection uses `libvox_app`**, which holds `SELECT` on what it reads
  and nothing more. `CatalogRecord::scopeToTenant` and
  `Conversation::scopeToTenant` verify the role is unprivileged before binding,
  because a superuser there would show every library's data to every librarian
  and the screen would look correct while doing it.
- **The read models refuse writes.** `save()` and `delete()` throw on
  `CatalogRecord`, `Conversation`, `ConversationMessage` and `MessageRating`.
  The index is rebuilt from the ILS and the log is written by `/libvox-core`.
- **The connection is bound where it is read**, not in middleware. Binding opens
  the connection, so binding per request would let a second database being down
  cost the whole panel instead of one screen.

**Ports.** HTTP is on **8090**, because 8080 and 8081 are Koha Testing Docker's
OPAC and staff interfaces. All three databases are on one PostgreSQL server, in
`/libvox-db`, on **5435** — separate databases on a shared server, because one
writer per database is a rule about databases and not about servers.

## Three roles, and none of them is a shortcut

| Role | Password | Can log in | What it is for |
| :--- | :--- | :--- | :--- |
| `libvox` | `libvox` | yes | Migrations, and owns the databases. Superuser, from `POSTGRES_USER`. |
| `libvox_app` | `libvox_app` | yes | Everything the application does. Unprivileged. |
| `libvox_resolver` | *none* | **no** | Owns one function. See [Resolving a channel](#resolving-a-channel-to-a-library). |

```
postgresql://libvox_app:libvox_app@127.0.0.1:5435/libvox_tenant   # the application
postgresql://libvox:libvox@127.0.0.1:5435/libvox_tenant           # migrations only
```

Those passwords live in `../libvox-db/`, which creates the roles, and printing
them here changes nothing: they are a local development convenience. In a
deployment, provisioning creates these roles with a secret from your secrets
manager. `initdb` runs only when the volume is empty, so changing them later
means `ALTER ROLE`.

**The same three roles serve all six databases**, which is deliberate rather
than incidental. They used to be two sets that merely shared names across two
servers; now there is one set, and one name per privilege level keeps the grants
readable across every component. `libvox_app` reaches a database because
`/libvox-db` granted `CONNECT` on it after revoking it from `PUBLIC` — and what
keeps one library out of another's rows was never the server count, but
row-level security under `FORCE`.

`libvox_resolver` is `NOLOGIN`, which is the point — see
[Resolving a channel](#resolving-a-channel-to-a-library).

**`LIBVOX_CRYPTO_KEY` and `APP_KEY` are the two genuinely secret values**, and
both are blank in `.env.example` so a missing one fails loudly rather than
falling back to something shared. The crypto key must match `/libvox-sync`'s, and
belongs outside the database it protects.

`libvox_app` is `NOSUPERUSER NOBYPASSRLS`, because a superuser ignores row-level
security unconditionally — every policy would still be present, still readable,
and enforcing nothing, while the panels worked and the isolation tests passed.

So the application checks rather than trusts. `AppServiceProvider` verifies its
connection is unprivileged before serving a request, and the first test in the
suite asserts the same thing. Every other isolation test is meaningful because
that one passes.

## One library per request

Isolation is PostgreSQL row-level security. `BindTenantScope` binds every request
on the library panel, taking the tenant from the authenticated user and from
nowhere else — not a route segment, not a header, not a form field. Same
reasoning that keeps `tenant_id` out of the MCP tool schemas: anything the
request can carry is something the request can change.

Code that scopes explicitly — the provisioner, the credential store, a test —
uses `run()`, which opens a transaction and restores whatever binding was in
force:

```php
app(TenantScope::class)->run($tenantId, fn () => /* ... */);
```

The binding is a side effect on the connection rather than a wrapper around a
call: applied on bind, released on `terminate`, re-applied on
`ConnectionEstablished` in case Laravel reconnects mid-request, and overwritten
by the next request that binds. A Laravel and Livewire request has no single
call to wrap — Livewire's update route runs its own pipeline — so the connection
carries the scope instead.

That form holds because a connection belongs to one request. Persistent PDO
connections, Octane and pgbouncer in transaction pooling mode each break that
assumption, and each is named below as something this module keeps out.

Outside a scope, `current_setting('libvox.tenant_id', true)` is NULL, every
policy matches nothing, and the tables read empty. **Forgetting to bind yields
no rows — never another library's rows.**

### Which tables carry it, and which do not

| Table | RLS | Why |
| :--- | :--- | :--- |
| `tenant_ils` | yes | Credentials are tenant data. |
| `tenant_widget` | yes | Per-library configuration, read at session start. |
| `channel_bindings` | yes | Holds channel secrets. Plus one narrow exception below. |
| `tenants` | no | The registry that *defines* which tenants exist. |
| `users` | no | Consulted before a tenant is known — the login query determines it. |
| `tenant_events` | no | Polled before the consumer knows which library an event concerns. |

The rule, so the next table has an answer already: **row-level security is for
tables whose rows must be unreachable from another library's request. A table
consulted before a tenant is known, and the table that says which tenants exist,
are not among them.**

Every policy fails closed: an unbound session matches nothing. A policy that
matched everything when unbound would invert the invariant the rest of this
module rests on.

> **`FORCE ROW LEVEL SECURITY` reaches a table's owner, not a superuser.**
> Superusers and `BYPASSRLS` roles go through regardless. In this compose file
> the owner is the superuser from `POSTGRES_USER`, so `FORCE` is written for the
> deployment where provisioning creates an unprivileged owner. What keeps the
> application inside its policies is the role it connects as.

## Two logins that cannot be confused for each other

`canAccessPanel` decides which panel a person may open, from a Shield role.

**That is authorization: which screens exist for whom. Isolation is a separate
thing**, done by row-level security in the database, and it would still hold if
`canAccessPanel` returned `true` for everybody. The panels do not carry that
weight.

`users.tenant_id` is null for an operator, who belongs to LibVox rather than to a
library. A librarian is required to have one, and is refused the panel without
it: with no library there is nothing to scope the request to, and an unscoped
session reads every tenant table as empty, which looks like data loss rather
than a misconfiguration.

> **Filament's built-in multi-tenancy is deliberately unused.** It models a user
> belonging to several tenants, puts the tenant in the URL as `/app/{tenant}/…`,
> renders a switcher, and scopes through a global Eloquent scope. Here a
> librarian belongs to one library by a foreign key, the tenant must never be a
> URL segment a browser can edit, and the scoping is Postgres.

## What a librarian does

### Connecting a catalog

**Conexão com o catálogo** stores the ILS type, base URL and credentials.
**Testar conexão** answers the question a librarian has no other way to answer:
are these credentials right? It says which of three things happened, because
unreachable, rejected and unpermitted have three different fixes.

```
lib-local        OK     1 record
lib-demofranca   FAIL   unauthorized  — Koha rejected the client credentials (HTTP 400)
lib-adminkuhn    OK     1 record
```

One result deserves its own message: **credentials accepted, catalogue empty.**
It looks like success and is the failure this system is most careful about — it
is how a library ends up telling readers it holds no books, and it is almost
always the API user missing Koha's `catalogue` flag.

This application asks `/libvox-core` to run that test, which the root README
names as the component that may decrypt a credential. Preserving that property
was worth a network call.

Both compose files declare a network named `libvox_internal`, and the two
services reach each other by name. Core publishes its internal API on the host's
loopback only — that surface takes `tenant_id` from its request body, so anything
reaching it reads any library — and the shared network is what lets this call
happen without publishing it anywhere.

### Seeing what MARC.IA can find

The **Acervo** screen lists exactly what the Discovery Index holds for the
library: a record missing from it is a record MARC.IA cannot see.

**The search box runs the same query MARC.IA runs** — `plainto_tsquery` against
`libvox_search`, the index's own configuration. It is accent-insensitive and it
reaches the series and the description as well as the title, so a word that finds
nothing here finds nothing for a reader either. `plainto_tsquery` rather than
`to_tsquery`, for the reason `/libvox-sync` gives: the second parses operators,
and a librarian typing `chess & !duchess` would get a syntax error instead of a
search.

**Ver** opens one record with every field the index holds, including the two the
tool contract does not return: the abstract and the cataloguer's notes. Both are
indexed at the lowest weight, so a record that matched on a word appearing only
in its abstract is explained here and nowhere else.

Above the table is the line that matters most: **whether the index is current,
and when it was last rebuilt.** An empty result against a `building` index and
against a `ready` one are opposite answers — *"not finished importing"* and *"the
library does not have it"*. It is the same reason `index_status` travels with
every tool result to MARC.IA.

The library's dashboard carries the same two facts as a card: how many records
the index holds, and when they were last read from the ILS. The read is wrapped,
so a Discovery Index that cannot be reached costs the card and not the panel.

### Asking for a re-import

**Atualizar índice** writes a `SyncRequested` event to the outbox, and
`/libvox-sync`'s worker answers it. The request goes through
`request_catalog_sync()`, which **takes no argument**: it acts on the tenant the
request is already scoped to, so there is nowhere to name another library. One
pending request per library is a unique partial index rather than a check the
panel performs.

The button leads to a screen that refreshes every second and says which of five
things is going on:

| | |
| :--- | :--- |
| **Na fila** | the request is written and something will claim it |
| **Ainda não pego** | it has sat for over 10s — with the two things that cause that, and how to check |
| **Importando** | the count, and the bar if there is a basis for one |
| **Concluída** | how many records the catalogue now holds |
| **Falhou** | what the library's own server answered |

The second state is careful about what it claims. A request still queued past ten
seconds means nothing has *taken* it — true both when no worker is running and
when one is busy importing another library. This screen sees one library's index
and no other's, so it names both causes and offers the command.

**An import in flight outranks a queued request**, because both are true at once
whenever the button is pressed twice: the first press is claimed and starts
writing while the second waits behind it.

**The count is a fact; the bar is an estimate.** Koha's `/api/v1/biblios` returns
no total — no `X-Total-Count`, no `Link` header — and paging the whole catalogue
to count it would double the load on the library's own server. What does exist is
the previous import: `/libvox-sync` drops the old generation in the same
transaction that promotes the new one, so the last one's size stays countable
throughout. That is the denominator, and it is labelled as approximate.

The number imported is always shown. The percentage appears when there was a
previous import to measure against, and **a catalogue that grew passes 100%**,
which the screen says rather than freezing at 99% to look tidy. Polling stops
once the import is finished or failed.

### The widget

**Configurações do widget** sets the assistant's name, greeting, tone, colour and
language, and generates the snippet to paste into an OPAC template. A live
preview renders the same `widget.js` a reader loads, from the unsaved form.

## What an operator does

### Reading conversations

`/libvox-core` stores what patrons asked and what MARC.IA answered. The
**Conversas** screen reads that log to answer one question: *is she answering
correctly?* — which nobody can judge without reading the answers. That is why it
is here and not in a library's panel.

Three columns carry the signal, and each is also a filter:

| | |
| :--- | :--- |
| **Busca vazia** | a lookup came back with no records at all — the one situation in which *"the library has none"* can be confidently wrong |
| **Falha** | a tool could not answer |
| **Avaliação** | *positiva*, *negativa*, or blank when nobody said anything |

**Read together they are a finding.** A negative rating on a conversation whose
search returned nothing is *"she told a reader the library has nothing, and the
reader disagreed"* — a gap in the collection, a gap in the index, or a search
that failed on the word the person used. Either column alone is a list.

A negative outweighs a positive on the same conversation, and the filter selects
exactly what the column shows.

The transcript reads as the conversation it was: questions and answers as they
happened, with each tool call folded into one line carrying which tool, how many
records, and whether it failed. **Ver JSON** opens the arguments and the result
in full, highlighted — a bad search is usually a bad query, and the arguments are
the only place that shows.

### One library at a time, enforced by the database

Both conversation tables carry `FORCE ROW LEVEL SECURITY`, and this panel binds
no tenant of its own. So choosing a library is what makes the screen show
anything:

```
sem biblioteca escolhida  →  0 conversas
lib-demofranca 15   ·   lib-adminkuhn 3   ·   lib-local 20
```

The alternative would put a credential reading every library's conversations
inside the application that serves requests.
`assertTheApplicationCannotBypassRowLevelSecurity` holds that line for the Tenant
DB, and `Conversation::scopeToTenant` makes the same check here before binding.

The filter above the table *chooses*; the policy *enforces*. Its `query()` is
deliberately a no-op, so that nobody removes a `where` clause believing it
redundant and takes the protection with it.

### What protects a reader

- **Nothing in the schema identifies anyone** — no name, email, phone, IP or
  platform identifier. A test asserts the columns this screen exposes.
- **Retention is a command.** Conversations are forgotten after ninety days by
  `libvox-core prune`, and a rating goes with the conversation it belongs to.

## The secret is written, never read

`IlsCredentialStore` has `put`, `updateConnection`, `describe` and `forget`. The
root README names `/libvox-core` as the only component that may decrypt a
library's credentials; making that true here means the read method does not
exist. Two tests hold the line: one asserts the class's method list, and one
tokenizes every PHP file under `app/` looking for `pgp_sym_decrypt` — tokenized
rather than grepped, so the comments explaining the design do not register as a
match.

What the portal legitimately needs is `describe()`: which ILS, at which URL,
under which `client_id`, last changed when. None of that is the secret.

**pgcrypto, not an application-level cipher.** The root README's rule is *never
Laravel's `Crypt` facade*, which is easy to misread as *never encrypt in the
database*. `Crypt` is keyed by `APP_KEY` and wraps AES-256-CBC in a
Laravel-specific envelope Python would have to reimplement to read one column.
pgcrypto is language-neutral and is the format `libvox_sync/credentials.py`
already reads — verified end to end: the portal writes a credential, and
`python -m libvox_sync tenant list` lists it while `fetch()` decrypts it. The
cipher sits behind an interface, so an AEAD later is one class and a binding.

### Where a secret could escape, and what stops it

Storing a credential passes the plaintext *and* `LIBVOX_CRYPTO_KEY` to PostgreSQL
as query bindings. That is what makes the ciphertext readable by another language
without a second implementation, and it puts both on a path Laravel is unusually
good at writing down.

| Path | What stops it |
| :--- | :--- |
| `QueryException` | Its message interpolates the bindings into the SQL. The store catches it at the source and rethrows with no message and `previous: null`. |
| Anything else that throws one | The exception handler replaces a `QueryException` whose message contains the key. Only that one: mapping all of them would cost the operator the database's own explanation for every constraint violation. |
| Log files | A Monolog processor strips the key from every record. |
| Query inspectors | A test asserts Telescope and Debugbar are absent from `composer.json`. Both render bindings, and *"it is only local"* is how a developer's log file ends up holding a library's credentials. |
| Session flash | `dontFlash` covers `client_secret`. Laravel's default list is `password`, `password_confirmation` and `current_password`; without this, one failed validation returns the secret to the form as old input. |
| Validation messages | `:input` echoes the submitted value, so rule messages avoid it. Stated in `lang/pt_BR/validation.php`. |
| Filament tables | There is no column to add: the model hides `client_secret_enc` and `describe()` does not select it. |

### Two states the database refuses

The secret field is write-only, so it renders blank on every edit — which means a
librarian changing only the catalog URL submits no secret at all. Hence
`updateConnection` as a separate method rather than a nullable argument: the two
are different statements, and a nullable version reads as harmless while
overwriting a working secret the first time anyone edits a URL.

`tenant_ils.tenant_id` references `tenants`, so a stored credential always names
a library that exists.

## A channel is a row

A widget key in an OPAC page, a Telegram bot, a WhatsApp business number and an
inbound e-mail address look like four things and are one: a per-tenant channel
binding. One table makes resolving a tenant a single lookup whatever the channel.

**`binding_key` is never a secret.** Telegram's is the numeric bot id — the
public half, before the colon — and WhatsApp's is the `phone_number_id`. Anything
secret lives in `secret_enc`. That separation is what lets a tenant be resolved
without the resolver touching ciphertext.

Five states the table refuses, each of which would look plausible in a form:

* a web binding **enabled with an empty allowlist**, which is a widget
  embeddable from any page on the internet;
* an origin that is not a bare scheme and host, because an `Origin` header is
  compared as a string and four spellings of one origin are four rows;
* a Telegram or WhatsApp binding with no secret — a channel that receives a
  message and cannot answer;
* a web binding carrying a secret, which would invite treating the public key as
  one and the Origin check as optional;
* two libraries claiming one key, which is a tenant crossing and therefore a
  constraint rather than a validation rule: row-level security would hide the
  conflict from any check written in PHP.

A new library starts with the web widget, the one channel needing no account from
a third party. It goes live the moment an origin exists, which is automatic when
the operator supplied an OPAC URL.

### Resolving a channel to a library

`resolve_channel_binding(channel, key, origin)` answers *"which library owns this
widget key, and may this page embed it?"* — asked before any tenant is known,
because its answer is what determines the tenant. So it runs outside a scope, and
it is the one genuinely cross-tenant read in this module.

`SECURITY DEFINER` runs as the function's owner, and `FORCE ROW LEVEL SECURITY`
subjects an owner to its own policies. So the owner is `libvox_resolver`: a role
that **cannot log in**, holding `SELECT` on two tables and one permissive policy
scoped `FOR SELECT`. Nobody can connect as it, and the width of the hole is the
return type — one `text` column that is a `tenant_id` and can never be a secret.

**Every refusal is the same answer.** An unknown key, a channel switched off, a
suspended library and a page not on the allowlist all return nothing, because
telling them apart would let a caller learn which of those a given key is.

Origins are compared exactly, which is correct rather than lax: a browser sends
an `Origin` header already lowercased, without a path and without a default port,
which is exactly the stored form. `Origin::normalize` cleans up what a librarian
types into a form.

## Saying that something changed

`tenant_events` is a transactional outbox: a row written in the same transaction
as the change, with `pg_notify` layered on as a latency optimization. NOTIFY is
delivered on commit, which is what makes the pair safe — a listener never learns
of a change that then rolled back, and the table is the guarantee either way.

```sql
LISTEN libvox_tenant_events;   -- {"id": …, "tenant_id": "lib-furg", "event": "TenantUpdated"}
```

`SyncRequested` is raised by the catalogue page's button and consumed by
`libvox-sync worker`. `TenantUpdated` fires on every configuration change, the
widget's colour included, and is available to any consumer that wants it.

Three decisions in the shape of it. **No payload column:** the event says which
library changed, and the consumer re-reads under its own scope. A payload would
be a second, unversioned copy of the schema, and the one place an encrypted
column could escape in cleartext. **No foreign key on `tenant_id`:** a log entry
that survives the thing it describes is what makes the deletion of a library
recordable. **A trigger, not an Eloquent observer:** a trigger also fires for the
bulk update, the `psql` session and the cascade.

## Two languages, one codebase

Code, comments, migrations, column names, test names and this README are English.
Only `lang/` is translated. Every label goes through `__()`, and a test refuses a
string literal in any panel class. A second test asserts both locales carry the
same keys.

The locale a person sees is: their own preference, then their library's default,
then the browser's, then the configured default. The person wins — a librarian
who set their account to English meant it, even at a library whose readers are
answered in Portuguese. `users.locale` is nullable so *"not chosen yet"* can be
said at all, which is a different thing from *"chose Portuguese"*.

> Laravel's `lang/` directories use `pt_BR` with an underscore; the BCP 47 tag
> belonging in `<html lang>`, in `tenants.locale` and in whatever `/libvox-core`
> hands the widget is `pt-BR` with a hyphen. The database stores the hyphenated
> tag, because that value crosses the wire. `App\Support\Locale` is the boundary,
> and it is a named class because getting this backwards produces missing
> translations rather than an error.

`lang/pt_BR/validation.php` is deliberately partial. Laravel falls back per key
rather than per file, so the rules these forms use are translated and anything
else answers in English rather than showing a raw key.

## Tests

```bash
cd ../libvox-db && ./db.sh && cd -   # the test databases live here too
composer test                        # 280, Pest
vendor/bin/pint --test               # style
```

This suite is also what gives `libvox_tenant_test` its schema, so it runs before
`/libvox-core`'s Python suite on a fresh server — that one reads the Tenant DB
and does not migrate it.

**A real PostgreSQL, never SQLite.** What this module is made of — row-level
security, pgcrypto, `bytea`, CHECK constraints, triggers and a `SECURITY DEFINER`
function — has no SQLite equivalent, so every isolation assertion would pass
while proving nothing. Tests skip with instructions when a database is not up,
the same convention as `libvox-sync/tests/test_schema.py`.

> **`migrate:fresh` once, `TRUNCATE` between tests** — what the Python `clean`
> fixture does. `RefreshDatabase` wraps each test in a transaction, which puts
> the tenant binding in a savepoint rather than where the assertions look for it,
> and the role-switching tests need real commits.

> **A Livewire component test runs no panel middleware**, so a component tested
> in isolation runs unscoped. That surfaces as an insert refused by the policy —
> the fail-closed behaviour working — and is kept as a test of its own. It also
> resolves the *default* panel, so a test of a library-panel resource says
> `Filament::setCurrentPanel('library')`. `LivewireScopeTest` drives
> `Utils::applyMiddleware` directly, which is the same pipeline Livewire uses.

**`EveryScreenRendersTest` renders every page of both panels against a library
that has everything filled in** — credentials stored, several origins listed,
every channel bound — and asserts each comes back 200. A fixture that is full is
worth more than an assertion that is clever: a column formatting an array of
origins is only exercised when the fixture has some.

## What this module keeps out

* **A privileged database connection.** The one cross-tenant read goes through a
  function owned by a role that cannot log in.
* **A read path for a stored secret.** `/libvox-core` decrypts; this writes.
* **Telescope, Debugbar, or anything else that renders query bindings.**
* **A free-text instruction field on `tenant_widget`.** The persona layer belongs
  to the library; the policy layer does not, and stays unreachable from a form. A
  textarea reaching the model's instruction slot would let an administrator write
  something reasonable-sounding — *"if the catalog has nothing, answer from your
  own knowledge"* — and disable the guarantee the whole system rests on.
  `persona_tone` is an enum for the same reason.
* **A persistent PDO connection, or pgbouncer in transaction pooling mode.** Both
  break the assumption that a connection belongs to one request.
* **`SESSION_DRIVER=database`.** The default connection has row-level security
  forced, and a session row is written at the login screen, before any tenant is
  bound. The symptom is a login form that redirects to itself forever.
* **Reader-facing anything.** No public pages, no chat, no catalog. That is
  `/libvox-core`.
