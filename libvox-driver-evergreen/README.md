# libvox-driver-evergreen

The [Evergreen](https://evergreen-ils.org/) driver for LibVox. It produces the
same `BiblioRecord` and `Availability` as `/libvox-driver-koha`, through the same
`Driver` protocol and the same JSON invocation contract, so `/libvox-sync` and
`/libvox-core` treat an Evergreen library like a Koha one.

## How it reaches Evergreen

Evergreen has **no API key**. The driver logs in with a staff **username and
password**, and the session token it gets back opens two doors:

| Door | Path | Used for |
| :--- | :--- | :--- |
| REST API | `/openapi3/v0` | Login (`/self/auth`), a record's copies (`/bib/{id}/holdings`), branch names (`/org_units`) |
| OpenSRF gateway | `/osrf-gateway-v1` | Walking the catalogue (`open-ils.pcrud.search.bre`) and reading one record |
| Public | `/reports/fm_IDL.xml`, `/opac/extras/sru` | Field positions of the gateway's objects; the diagnostic title search |

The REST API cannot list records, which is why the catalogue walk goes through
the gateway: `pcrud` pages `biblio.record_entry` by id, a hundred records per
request with the MARCXML of each.

The session is logged out when the driver closes. Evergreen keeps sessions
server-side until then, and a driver built per invocation would otherwise leave
one behind per question.

## What a library has to set up

1. **Enable the REST API.** Evergreen ships it off: `/openapi3/v0` answers 404
   until the `openapi_server` runs and the web server routes `/openapi3` to it.
   See the Evergreen documentation, *Integrations → RESTful API*.
2. **Create an account for LibVox** with staff login and read access to the
   catalogue, and nothing more. LibVox never writes to an ILS. The password
   must not contain a colon: the REST API reads the login as
   `password:staff`.
3. **Mind the REST rate limit.** A stock installation allows about one REST
   request per second per session. The driver paces itself to that, so an
   availability check costs about a second per record. A library that raises
   the limit for the LibVox account in `openapi.endpoint_user_rate_limit_map`
   gets faster answers; nothing else changes.
4. In the LibVox portal: ILS **Evergreen**, the server address (no path), the
   username and the password, and optionally the OPAC address for record links.

## Mapping

| `BiblioRecord` | From |
| :--- | :--- |
| `biblio_id` | `biblio.record_entry.id` -- the number in the OPAC's `/eg/opac/record/<id>` |
| `title` | 245 `$a`, `$n`, `$p`, then `$b` after a colon |
| `author` | 100/110/111 `$a`, else the first 700/710/711 |
| `isbn` | first 020 `$a`, qualifier removed |
| `publisher` | 264 (2nd indicator 1) `$b`, else 260 `$b` |
| `copyright_date` | first year in 264/260 `$c`, else 008/07-10 |
| `item_type` | leader 06-07 (book, serial, score, video, ...) |
| `series` | 490 `$a`, else 830, else 440 |
| `abstract`, `notes` | 520 `$a`, 500 `$a` |
| `ils_timestamp` | `edit_date` |

A deleted or inactive record is skipped and counted, like a record a Koha
library suppresses from its OPAC.

| `Availability` | From the copies of `/bib/{id}/holdings` |
| :--- | :--- |
| visible copies | not deleted, and the copy, its location and its status all OPAC-visible |
| on the shelf | status `is_available` (Available, Reshelving), copy and location circulate |
| reference only | available, but the copy or its location does not circulate |
| checked out / on hold | status 1 (Checked out) / 8 (On holds shelf) |
| `library`, `shelf`, `call_number` | `circ_lib` name, location name, call number with prefix and suffix |
| `due_date` | always null: it lives on the circulation, a patron-data permission |

## Use

```bash
pip install -e ../libvox-driver-koha   # the contract lives there, for now
pip install -e ".[dev]"

cp .env.example .env                   # EVERGREEN_URL, _USERNAME, _PASSWORD
set -a; . ./.env; set +a
libvox-driver-evergreen availability --id 1
libvox-driver-evergreen get-biblio --id 1
libvox-driver-evergreen search-title --title "piano concerto"
```

```bash
ruff check .
pytest
```

The tests drive a scripted Evergreen through the real HTTP stack with payloads
captured from a running Evergreen 3.17.3 (the MOBIUS Docker image).

## The contract's home

`BiblioRecord`, `Availability`, `Driver` and the error taxonomy are imported
from `/libvox-driver-koha`, where they were written when Koha was the only
driver. Its `AGENTS.md` plans to move them to a shared `libvox-common` when a
second driver arrives; this is that driver, and the move is the next step. It is
a file move as long as nothing Evergreen- or Koha-specific enters the contract.
