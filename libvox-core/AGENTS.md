# /libvox-core — agent rules

Repo-wide rules: [`../AGENTS.md`](../AGENTS.md). Module detail:
[`README.md`](README.md).

**This is the tenant boundary and MARC.IA's loop.** Every rule below exists
because this module is the one place where being wrong leaks one library's data
into another library's answer.

## Rules

- **Two ASGI apps, two ports, and that is the security boundary.**
  `create_app` (`src/libvox_core/app.py:33`) serves the internal API on 8091 and
  `create_public_app` (`:122`) serves the reader-facing WebSocket on 8092. The
  internal surface takes `tenant_id` **from the request body**, so anything that
  reaches it can read any library's data — which is why compose publishes it to
  `127.0.0.1` only (`docker-compose.yml:87`). Never widen that binding, and never
  merge the two apps into one.
- **The tenant is re-resolved from the credential on every call, and nothing is
  cached.** A long conversation must not be able to drift into another tenant's
  scope, and a revoked credential must stop working at once. Do not add a cache
  "since it's the same session".
- **`EXPECTED_TOOLS` is a second place a tool is registered.**
  `src/libvox_core/agent/tools.py:36` is a frozenset asserted against the live
  MCP schemas, and it warns loudly when upstream declares something it does not
  expect. A third tool means an edit here *and* in `/libvox-mcp`; that is
  deliberate, so a widening tool surface is noticed rather than absorbed.
- **This module decrypts ILS credentials, and it is the only one that may.**
  `src/libvox_core/ils.py` and `availability.py` reach the stored secret through
  `LIBVOX_CRYPTO_KEY`. `/libvox-mcp` is forbidden from doing this — that
  prohibition is only coherent because the capability lives here.
- **The conversation log has no column that identifies a person**, and the
  retention is ninety days (`agent/store.py:32`). The absence of a column is the
  enforcement; a `session_id`, an IP or a free-text "reason" field would each
  re-identify a reader. Do not add one, and do not add a second timestamp.
- **No ILS writes.** No holds, no renewals, no patron mutations.

## The system prompt

`agent/prompt.py` is the most edited and most dangerous file here.

- **Every rule in `POLICY` is scar tissue.** Each was added after a specific
  failure, and the comments in the file name it. Shortening the prompt without a
  regression gate reopens those failures, and a wrong answer reaches the reader
  carrying the library's authority.
- **An example outvotes a rule.** The file records this measured: a worked
  example that searched twice kept teaching that behaviour after rules 6 and 7
  forbade it. If you change an example, check what it demonstrates, not only what
  it says.
- **An instruction that demands a field the model may not have is an instruction
  to invent it.** Adding a formatting rule produced fabricated shelf marks and
  copy counts in one run in eight. That is the cost this block always pays.
- **`build()` orders POLICY → examples → persona, and persona is last.** The
  stated reason is language: the persona frame's closing sentence has to outrank
  the language the examples are written in. Reordering also costs the shared
  prefix that every tenant of a locale has in common.
- Library-authored text reaches this prompt through one function, which strips
  the delimiters. Everything a library writes goes through there.

## What the loop does that surprises people

- **`answer()` catches `ModelUnavailable` and returns its own sentence.** Right
  for a reader; from outside, an endpoint that is down is indistinguishable from
  a model that answered badly. Anything measuring this loop must check for
  `_UNAVAILABLE` per locale, or it will record a dead server as a grid of bad
  answers.
- **`temperature: 0` is hardcoded and `extra_body` is merged first**
  (`agent/llm.py`), so a deployment cannot raise it from an environment
  variable. Two identical questions about one catalogue must not get two
  different answers.
- **`provider_extra` is carried back unopened.** It is a signature over
  something only the model saw; parsing it would buy nothing and would teach this
  module a provider.
- **`/internal/v1/messages` reports what was called, not what came back**
  (`routes.py`). A reader's channel has no business carrying tool payloads. If
  you need the payloads — a bench does — drive `loop.answer` in process instead
  of widening the route.
- **A page size larger than we serve is clamped, not refused.** Asking for zero
  is refused, because that is a request with no answer rather than a smaller one.

## Tests

```bash
cd ../libvox-db && ./db.sh && cd -
cd ../libvox-portal && ./vendor/bin/pest && cd -   # owns libvox_tenant_test
pytest
```

Without a database, `110 passed, 106 skipped` — the skipped set is the isolation
and endpoint suite. The run exits zero, so do not report "tests pass" on that
basis. The portal's suite is not optional: it owns the Tenant DB's schema, and
without it the tenant tests skip rather than fail.
