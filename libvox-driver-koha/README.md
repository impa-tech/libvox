# /libvox-driver-koha

The Koha integration driver — the only component in LibVox that knows what Koha
looks like. Everything downstream deals in `BiblioRecord` and never in MARC.

Every caller goes through one `handle()` function; see
[The invocation contract](#the-invocation-contract).

## Two paths, one driver

| Method | Caller | When |
|---|---|---|
| `get_biblio(id)` | `/libvox-core`, for live answers | Now, because the answer has to be current |
| `check_availability(id)` | `/libvox-core`, for live answers | Now, and never cached — see below |
| `fetch_page(cursor)` | `/libvox-sync`, to build the Discovery Index | Bulk, on a schedule |
| `search_by_title(text)` | Diagnostics and reconciliation — **not patrons** | Title and subtitle; never on the chat path |

They share authentication and normalization on purpose. A catalog that answered
one way to a live lookup and another way to the importer would put the
Discovery Index and the live answer in disagreement about the same book.

Verified on a running Koha: `GET /biblios/{id}` and `GET /biblios` return the
same flat object, so one normalization serves both. `tests/test_normalize.py`
asserts it, against captures from both endpoints.

## Quick start

```bash
cp .env.example .env      # then fill in the credentials
docker compose build
docker compose run --rm driver get-biblio --id 1
```

```json
{
  "ok": true,
  "action": "get_biblio",
  "data": {
    "biblio_id": 1,
    "title": "E Street shuffle",
    "author": "Heylin, Clinton.",
    "isbn": "9780670026623",
    "publisher": null,
    "copyright_date": null,
    "item_type": "BK",
    "series": null,
    "abstract": null,
    "notes": null,
    "ils_timestamp": "2020-01-29T12:06:37+00:00"
  }
}
```

`get-biblio` is the integration smoke test: one call exercises authentication,
permissions, content negotiation and normalization. If it works, the connection
to that library works.

**`series`, `abstract` and `notes` arrive in the same response as the title**,
and were dropped in normalization until a catalog of Pokémon records made the
cost visible: titles like *Metapod*, a series of *Pokémon - Generation I*, and a
reader asking for "pokemon" told the library held nothing. The Discovery Index
weights them below the title and returns only `series`; the other two exist to
make a record findable, not to be quoted back.

> A Koha running on the host is not `127.0.0.1` from inside the container. Set
> `KOHA_URL_DOCKER=http://host.docker.internal:8081` in `.env`; the compose file
> maps that name on Linux too, and leaves `KOHA_URL` alone for native runs.

### Without Docker

Install once:

```bash
conda env create -f environment.yml
conda activate libvox-driver-koha
$(conda info --base)/envs/libvox-driver-koha/bin/pip install -e ".[dev]"
```

Then load the credentials into the shell and run any command:

```bash
set -a; source .env; set +a

python -m libvox_driver_koha get-biblio  --id 1
python -m libvox_driver_koha search-title --title "chess"
python -m libvox_driver_koha search-title --title "chess" --limit 5

echo '{"action":"search_by_title","params":{"title":"greek","limit":3}}' \
  | python -m libvox_driver_koha invoke
```

```json
{
  "ok": true,
  "action": "search_by_title",
  "data": {
    "count": 3,
    "truncated": false,
    "records": [
      {"biblio_id": 2,   "title": "The ideas behind the chess openings", "...": "..."},
      {"biblio_id": 23,  "title": "The Duchess of Malfi",                "...": "..."},
      {"biblio_id": 303, "title": "Pawn structure chess",                "...": "..."}
    ]
  }
}
```

`set -a` exports every variable the file defines, so the CLI finds them;
without it `source` only sets shell variables and the driver reports the three
as missing. Credentials never go on the command line — `/proc/<pid>/cmdline` is
readable by any local process.

Two notes:

* Use the environment's own `pip` by full path. `conda run pip install ".[dev]"`
  re-parses the argument and loses the extras bracket.
* `conda env create` needs `conda-forge` alone. Anaconda's own channels now
  answer `429` without client registration, so a fall-through to `defaults`
  fails the whole solve; `environment.yml` pins `nodefaults`, and creating an
  env by hand needs `conda create -c conda-forge --override-channels`.

**One `.env`, both modes.** `KOHA_URL` is the host-native address and is what
the CLI uses directly. Inside a container `127.0.0.1` is the container, so a
Koha on the host is reached by name — set `KOHA_URL_DOCKER` and compose
substitutes it for the container only. Leave it unset and both paths use
`KOHA_URL`.

```bash
KOHA_URL=http://127.0.0.1:8081              # used by the CLI
KOHA_URL_DOCKER=http://host.docker.internal:8081   # used by the container
```


## Being gentle with someone else's server

**Thirty seconds per request, and that number is load-bearing outside this
module.** `/libvox-mcp` allows sixty for the call it makes to Core and Core
allows ninety on the tool layer, each more patient than the one it waits on. One
availability answer is up to four sequential requests, so a layer that gave up
first would discard work the layer below had finished.

An import is hundreds of requests against a library's own machine, and the
first rule of this project is that a patron's search never becomes load there.
Bulk extraction is the one path that does, so it is the one that has to be
careful.

**The pause before a request is a share of what the last one cost.** A page
that took eight seconds buys the server four seconds of quiet; one answering in
200ms is paced by the fixed 0.2s floor and unaffected. So the courtesy only
ever slows down servers that are already struggling — measured locally, a
healthy 436-record import still finishes in three seconds.

> **The bug this replaced, because it is easy to write again.** The stamp used
> to be taken *before* the request. After a page that took eight seconds,
> `elapsed` was already eight, any floor was long past, and no wait happened —
> a rate limit for fast servers and nothing at all for slow ones, which is
> backwards. It took down a demo Koha: pages 1 and 2 answered in ~8s each,
> nothing waited in between, and page 3's body never finished arriving.
>
> The first fix did not work either, and the test caught it: measuring the gap
> is only meaningful **from when the last request finished**, because otherwise
> the request spends the pause it earned.

**A timeout is retried once, after at least fifteen seconds**, and then the
import gives up. A timeout is the strongest signal a server can send that it
needs room; retrying briskly is how a struggling server becomes an unreachable
one. Two is enough to survive a blip and few enough not to pile on.

The message a librarian sees names the load, which is what makes a second press
a decision rather than a reflex.

## Searching by title — and why it is not the patron's search

```bash
docker compose run --rm driver search-title --title "chess" --limit 20
```

```json
{"ok": true, "action": "search_by_title",
 "data": {"count": 3, "truncated": false,
          "records": [{"biblio_id": 2, "title": "The ideas behind the chess openings", ...}]}}
```

**Do not wire this to the chat path.** A patron searching the catalog is
answered from the Discovery Index and generates no traffic against the
library's server — that constraint is the reason the index exists. This method
would work if connected to chat, which is exactly what makes the mistake easy
to make and hard to notice.

Its jobs are proving an integration end to end, reconciling the index against
its source, and answering "does this library really hold this?" while
debugging.

It is also weak search, on purpose. `LIKE '%chess%'` matches inside words: on
the real sample catalog that query returns **The Duchess of Malfi**. It covers
the title and the subtitle, and it cannot stem, weight fields or rank; the
Discovery Index does all three. There is a
test asserting the Duchess result, against a live capture, so nobody
mistakes the limitation for a bug.

### Two things this gets right that are easy to get wrong

**Wildcards typed by a person are escaped.** Verified on a live Koha: a bare
`%` as the search term matched *every record in the catalog*, because it
reaches MySQL's `LIKE` as a wildcard. `_` is the same hazard one character
wide. `escape_like()` neutralizes `\`, `%` and `_` — backslash first, or
escaping the escape character doubles back over the wildcards added after it.
An empty term is refused outright, since `''` becomes `'%%'`.

**Truncation is reported, not hidden.** `truncated` says the ILS had at least
one more match than the limit allowed. "3 results" and "the first 20 of
unknown many" are different answers, and a caller that cannot tell them apart
will state the wrong one confidently. It is measured on what Koha sent rather
than on what survived normalization — a page filled entirely with suppressed
records still has more behind it. An out-of-range limit is refused rather than
clamped, for the same reason.

## Getting a Koha API key that works

*Administration → Manage API keys*, on a patron that has **both**:

1. the **`catalogue`** permission, and
2. a **`userid`**.

Both, or it fails. On a live instance an API key whose patron held the
`catalogue` flag still returned `403` on every call, because the patron had no
login name and Koha resolves permissions through `haspermission($userid, …)`.
The flag alone is inert. This costs an afternoon if you do not know it, because
the error names the permission you have already granted:

```json
{"error": "Authorization failure. Missing required permission(s).",
 "required_permissions": {"catalogue": "1"}}
```

**Grant `catalogue` and nothing else.** It covers reading bibliographic and item
data — everything LibVox needs — with no circulation, no editing and no patron
data. LibVox never writes to an ILS. Do not reach for `superlibrarian` to make
an error go away: it works, and it silently ends the guarantee that the
integration runs read-only.

## The invocation contract

`handle()` takes a dictionary and returns a dictionary. That is the whole
interface, and it is what makes the serverless version an adapter rather than a
rewrite.

```python
from libvox_driver_koha import handle

handle({
    "action": "get_biblio",
    "params": {"biblio_id": 1},
    "connection": {"base_url": "...", "client_id": "...", "client_secret": "..."},
})
```

Exercise it exactly as the platform will:

```bash
echo '{"action":"get_biblio","params":{"biblio_id":1}}' \
  | docker compose run --rm -T driver invoke
```

The CLI fills in `connection` from the environment when the request omits it, so
a secret never has to be typed into a JSON document that might be saved or
pasted.

**Failure is a response, not an exception.** `handle()` never raises. A driver
that raised across the boundary would surface as a 500 with a stack trace, and
the caller would lose the difference between "no such book" and "the library is
down".

```json
{"ok": false, "action": "get_biblio",
 "error": {"code": "not_found", "message": "Bibliographic record not found"}}
```

### Error codes

Match on `code`; the message is for logs.

| Code | Means | Who fixes it |
|---|---|---|
| `not_found` | No such record in this catalog | Nobody — it is an answer |
| `suppressed` | Exists, but hidden from the library's own OPAC | Nobody. Show the patron the same thing as `not_found` |
| `unusable_record` | Present but unrepresentable — no title | The cataloguer |
| `unauthorized` | Credential wrong, inactive or revoked | Operator: re-issue the key |
| `token_refused` | The key is valid and the token is issued, then refused. Koha running under CGI answers this way | The library's Koha administrator: `koha-plack --enable` |
| `permission_denied` | Credential valid, grant missing. Carries `required_permissions` | The library's Koha administrator |
| `upstream_unavailable` | Unreachable, timed out, or 5xx. Retryable | Nobody — retry |
| `upstream_error` | Answered with something uninterpretable | Us |
| `bad_request` | Malformed invocation | The caller |

`suppressed` is deliberately distinct from `not_found` so the library can
diagnose a missing result — but callers must show the patron the same message
for both. Revealing that a hidden record exists leaks exactly what the
suppression flag was set to hide.

## Two properties worth not losing

**The driver never learns which tenant it is serving.** A request carries a
*connection* — a URL and a credential — not an identity. There is nothing here
to correlate two invocations, and the contract rejects a `tenant_id` if one is
passed. Tenant identity stays in `/libvox-core`, which is what keeps it out of
anything the model can read. `test_the_contract_has_no_place_for_a_tenant`
enforces this.

**Nothing is held between invocations.** The access token is cached for the
lifetime of the `KohaClient` object and no longer. That one rule serves both
paths without a flag: a bulk import holds one client across hundreds of requests
and pays for one token, while `handle()` builds a client per invocation and so
holds nothing between them. A cache that outlived the client is where library
A's bearer token ends up on library B's request.

## Relationship to /libvox-sync

**Intended, not yet true.** The plan is that `/libvox-sync` imports this package
and the dependency runs one way, so there is a single definition of what a title
is. Today it does not: `libvox-sync` imports nothing from here, declares no
dependency on it, and carries its own `Page`, `BiblioRecord`, `Driver` protocol
and Koha implementation in `src/libvox_sync/drivers/`. The two definitions the
one-way dependency exists to prevent both exist right now, and are free to drift.

Collapsing them is real work, not a cleanup: it belongs with the `libvox-common`
move below, when `/libvox-driver-folio` forces the question. Until then, read any
claim of a one-way dependency in this repository as a statement of intent.

## Tests

```bash
pytest
```

And the linter, which `environment.yml` installs through
`requirements-dev.txt`:

```bash
ruff check .
```


No network. Tests drive the real HTTP stack through `httpx2.MockTransport`
against a scripted Koha, so status mapping, the `Accept` header and the token
flow stay under test — replacing `KohaClient` instead would leave all three
unexercised. Fixtures are verbatim captures from a live instance;
`test_the_fixture_is_a_real_capture` guards against anyone tidying the ISBD
punctuation out of them.

## Availability is a cataloguing question

`check_availability(id)` answers "can I have this book?" — and every rule that
turns Koha's item flags into that answer is a judgement about what a library
means by its own data, not plumbing. They live in `normalize.py`, together,
with their reasoning.

**One copy is classified before the record is summarized:**

| Koha says | LibVox calls it |
|---|---|
| `lost_status`, `withdrawn`, `damaged_status` or `restricted_status` set | gone |
| `checked_out_date` present | out |
| `effective_not_for_loan_status` set | reference |
| none of the above | on the shelf |

Order matters. A copy that is lost *and* checked out is **gone**, not out:
calling it out would promise a return that is not coming.

`effective_not_for_loan_status` rather than the raw field, because most
libraries set "reference only" on the *item type*, not the item. Reading the
raw flag alone would miss a whole reference collection and offer every copy in
it as a loan.

Koha writes `0`, `"0"`, `null` and `false` for "nothing is wrong", varying by
field and version, and anything else is a library-defined code. So the rules
ask only whether a flag is *set*, never what it says.

### Three counts, because four words are not enough

`AvailabilityStatus` has four values and none of them is "you may read it here
but not take it home". So the status describes **borrowing**, and the counts
carry what it cannot:

```python
Availability(status="unavailable", copies_total=2,
             copies_available=0, copies_reference_only=2)
```

That record is not "we do not have it". Folding reference copies into
`copies_available` would promise a loan the desk will refuse; dropping them
would hide two books sitting on a table. A caller that reads only `status` will
tell someone to stay home from a library that has what they want.

### Where the book is, in two parts

`library` is the branch — the building to walk to. `shelf` is Koha's *shelving
location*, which is where to walk once inside: `General stacks`, `Reference`,
`Children's area`. A reader given only the first still has to ask at the desk,
which is the question MARC.IA exists to save them.

Both arrive as codes and both are resolved to names, because `MPL` and `GEN` are
not answers. Branches come from `/api/v1/libraries`; shelving locations are
authorised values in category `LOC`, read from
`/api/v1/authorised_value_categories/LOC/authorised_values`.

**That endpoint needs `catalogue: 1`** — the permission LibVox already asks a
library for, and the reason this field exists at all. One that required
`parameters` would not have been worth it, on the same terms the due date was
refused below.

Each list is fetched once per driver instance, and failing to fetch one costs
the name rather than the answer: the raw code is passed through, since `GEN`
tells a reader less than `General stacks` and more than nothing.

**The shelving list is fetched only when a copy carries a code.** A library that
puts everything in one room fills in none, and asking for the vocabulary anyway
is a fourth sequential request to someone else's server for a field nobody
filled in — which is what took an availability check on a catalogue in New
Zealand past six seconds, and past what the caller was then willing to wait. The
branch list is unconditional: every answer names a building.

Two details that are not obvious from the field names:

* **`opac_description` before `description`.** Koha keeps two labels per
  authorised value and the plain one belongs to the staff interface. It was null
  on all ten locations of the instance this was written against, so the fallback
  is the ordinary path — but where a library has set a public label, that is the
  one a reader should read.
* **`location` before `permanent_location`.** The first is where the copy sits
  today, the second where it belongs. A book on the display shelf or the book
  cart has the two disagreeing, and the one worth telling a reader is where to
  walk now.

`shelf` is called that rather than `location`, which is Koha's own word for it
and would be the accurate one. This contract spent its first weeks calling the
*branch* `location`, and a model reading the misnamed field told a reader a book
was *"na seção Midway"* when Midway is a building. Reusing the word now would be
correct and unreadable.

### The due date is refused, not missing

Koha's item record carries `checked_out_date` and no due date at all — verified
against a live catalog, and `tests/items_checked_out.json` is the capture that
settles it. The due date exists only through `/checkouts`, which answers:

```
PermissionDenied: [{'circulate': 'circulate_remaining_permissions'}, …]
```

LibVox asks a library for a read-only key with `catalogue` and nothing else,
because it never writes to an ILS. Granting `circulate` to fill one field would
hand this driver the power to alter loans in exchange for a date. `due_date`
stays on the dataclass for the day an ILS offers it without that trade.

### Branch codes are resolved to names

A patron told the book is at `CPL` has learned nothing; `Centerville` is an
address. `/api/v1/libraries` answers with `catalogue`, and the map is fetched
**once per driver instance** — the same lifetime as the token, for the same
reason, since one instance serves one library and branches move about as often
as buildings do.

If that call fails the answer still goes out with the raw code. Knowing a copy
is free and not knowing which building is a worse answer than the one above it,
but it is still an answer.

### Never cached, at any layer

This is the one thing the Discovery Index deliberately does not hold. A stale
"available" sends someone across town for a book that is not there, which is
worse than saying nothing at all.

## Known Koha behaviour

Verified against Koha Testing Docker, August 2026.

* **`Accept` is mandatory.** `GET /biblios/{id}` with no `Accept` header answers
  `406` plus the list of types it serves. The endpoint negotiates
  `marcxml+xml`, `marc-in-json` and `marc`; only `application/json` gives the
  flat object.
* **No pagination metadata.** No `X-Total-Count`, no `Link`. The total is
  unknowable up front, so extraction runs until a short or empty page arrives —
  an exactly-full final page costs one extra request that returns `[]`.
* `_page` / `_per_page` are 1-indexed and non-overlapping. A page past the end
  returns `[]`, not an error.
* OAuth2 tokens last 3600s, so any full import outlives one. The client
  refreshes 60s ahead of expiry.
* A wrong `client_secret` gets `403` on the token endpoint, not `401` — mapped
  to `unauthorized`, because "your key is wrong" and "your key lacks a grant"
  send the administrator to different screens.
* **`q` takes SQL::Abstract as JSON**, e.g. `{"title":{"like":"%chess%"}}`.
  `like` is case-insensitive, `-or` across fields works, and `q` combines with
  `_page` / `_per_page` normally. `_match` is not supported across versions, so
  `like` is what to use.
* **A misspelled field name in `q` returns `500`, not `400`.** A permanent bug
  therefore looks like a transient outage, and this driver would map it to the
  retryable `upstream_unavailable`. Field names here are constructed by the
  driver, never taken from a caller, so the failure stays a code bug.
* `subtitle` is a queryable field, and every title search includes it: Koha
  stores it apart from the title, and a patron typing a book's full name types
  both.
