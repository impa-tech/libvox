# /libvox-driver-evergreen — agent rules

Repo-wide rules: [`../AGENTS.md`](../AGENTS.md). Module detail:
[`README.md`](README.md). The Koha driver's rules
([`../libvox-driver-koha/AGENTS.md`](../libvox-driver-koha/AGENTS.md)) apply here
too, with "Koha" read as "Evergreen".

**This module is where Evergreen knowledge belongs.** MARC tags, OpenSRF method
names, IDL classes, copy status ids -- all of it stops here.

## Rules

- **The connection keys are Koha's.** `client_id` is the staff username and
  `client_secret` the password. The portal and Core store and pass every
  library's connection the same way; do not add an Evergreen-only key.
- **The session is closed with the driver.** `EvergreenClient.close()` logs out.
  A client that skipped it would leave a live session on the library's server
  for every question asked.
- **The token never reaches a log or a URL.** The gateway is called by POST, and
  its error text, which quotes the token, goes through `_scrub` before anything
  logs it.
- **Field positions come from the IDL.** Never index a gateway object's `__p`
  by a number written in code; an Evergreen upgrade shifts them.
- **A REST 401 is not always a credential failure.** "Rate limit exceeded" is
  paced and retried; only a refused login is `Unauthorized`.

## Evergreen behaviour that will waste your time

- `/openapi3/v0` is off in a stock install (404). The MOBIUS image needs the
  route added to nginx and `openapi_server` started, after every container start.
- The REST API allows ~1 request per second per session by default; the second
  inside that second answers `401 {"message": "Rate limit exceeded"}`.
- `/bib/{id}` returns `marc: null`. The MARC comes from `pcrud`.
- `/bib/{id}/holdings` answers `[]`, not 404, for a record that does not exist.
- The gateway always answers HTTP 200; the real status is `body.status`.
- `open-ils.search.biblio.multiclass.query` returned 0 hits on the test image
  where SRU found 37; the diagnostic search uses SRU.
- Booleans are `"t"`/`"f"` from the gateway and `true`/`false` from REST.
