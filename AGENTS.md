# Working on LibVox

Instructions for coding agents (Claude Code, opencode, Copilot) and for the
humans reviewing what they produce. Each module carries its own `AGENTS.md` with
rules specific to it — read the nearest one too.

Human workflow — branches, commits, review — is in [`CONTRIBUTING.md`](CONTRIBUTING.md).

## Read first

[`README.md`](README.md) describes a system that runs. It was a specification
once, and this file used to warn that most of it was unbuilt — that warning is
what is now out of date.

**Six modules, and the spine runs end to end.** A reader asks MARC.IA a question
on a library's OPAC page and she answers from that library's catalogue.

| Module | What it is |
| :--- | :--- |
| `/libvox-core` | The tenant boundary, the agent loop, the conversation log, the WebSocket and the widget |
| `/libvox-mcp` | The MCP tool layer. Declares two tools; implements none |
| `/libvox-sync` | Extraction and the Discovery Index — schema and pipeline |
| `/libvox-driver-koha` | Everything that knows what a Koha looks like |
| `/libvox-portal` | Laravel. The registry, encrypted ILS credentials, channel bindings |
| `/libvox-db` | One PostgreSQL server, six databases, three roles. No application code |

The module READMEs are the current description of each. Two sections of the root
README are load-bearing before you write anything:
[§The MCP Tool Contract](README.md#-the-mcp-tool-contract) and
[§Tenant Propagation & Isolation](README.md#-tenant-propagation--isolation).

## Non-negotiable invariants

These are architectural decisions with reasons already written down. Several of
them look *worse* than the obvious alternative until you know why. Do not
"improve" one; if you think a rule is wrong, open an issue.

- **No tool schema accepts `tenant_id`, a library name, or an ILS URL.** Any
  argument the model fills is attacker-controllable through the user's prompt,
  and that prompt is typed by anonymous members of the public. Tenant identity
  travels out-of-band and is re-resolved on every call. Enforced by
  `test_no_tool_accepts_a_tenant` in `libvox-mcp/tests/test_server.py`.
- **`/libvox-mcp` declares tools and nothing else.** No business logic, no
  credentials, no database access — with exactly one carve-out: `direct_koha.py`,
  the temporary experiment, does talk to a Koha directly and `config.py` holds a
  `koha_client_secret` for it. That path is selected only by
  `LIBVOX_MCP_BACKEND=koha` and is never imported by the production path. It was
  to be deleted when `/libvox-core` landed; Core landed and the file is still
  there, now a debugging convenience rather than the only way in. **Do not delete
  it as housekeeping** — that is a decision with an open case, not a chore. Note
  it declares a third tool, `get_biblio`, which production does not — so an
  agent observed against that backend is choosing from a larger set than it will
  face for real. Nothing else in the module gets that licence: it is a protocol
  adapter, and the same implementation should be exposable over gRPC without
  moving a line.
- **`search_catalog` never touches the live ILS**, and an empty index does not
  fall back to one. Cold start is exactly when an unindexed tenant would generate
  the heaviest load. It returns `index_status` instead, so an empty result is
  distinguishable from an unbuilt index.
- **`check_availability` always hits the ILS.** No caching is implemented yet,
  and adding one is a `/libvox-core` decision, not a local optimization: the only
  sanctioned form is a short TTL scoped to a single conversation, so one exchange
  does not ask the ILS the same question twice. A cached "available" is a wrong
  answer, not a stale one.
- **No ILS writes.** No holds, no renewals, no patron mutations. No patron
  identity is stored — conversations are scoped to a session, never to a person.
- **A third tool needs an issue and a stated reason.** Small, well-named tool
  sets measurably improve tool-selection accuracy in the small open-weight models
  a self-hosted library will actually run. Two is a design decision.

## Conventions already in the code

Match the surrounding code rather than introducing a new style.

- Python 3.10+ (`requires-python = ">=3.10"`), environments pin 3.12.
- `from __future__ import annotations` at the top of every module that declares
  anything — two `__init__.py` re-export shims are the only files without it.
- ruff, `line-length = 88`, `target-version = "py310"`.
- `src/` layout, hatchling build backend, one `pyproject.toml` per Python
  module. `/libvox-portal` is Laravel (`composer.json`) and `/libvox-db` is
  compose and SQL with no package at all; neither follows this.
- Frozen dataclasses for domain types: `@dataclass(frozen=True, slots=True)`.
- Module docstrings explain the **decision**, not the code — see
  `libvox-mcp/src/libvox_mcp/auth.py` and
  `libvox-driver-koha/src/libvox_driver_koha/contract.py`. A docstring that
  restates the function signature is noise; one that says why the obvious
  alternative was rejected is why this codebase is readable.
- Dependencies are declared twice on purpose — `pyproject.toml` and
  `requirements.txt` — and the comment `# Keep in sync with requirements.txt.`
  means it. Change both.

## Environments

**One conda environment per module. They are not interchangeable** — the driver
and the MCP server pin different dependency sets deliberately.

```bash
cd libvox-mcp            # or libvox-core, libvox-sync, libvox-driver-koha
conda env create -f environment.yml
conda activate libvox-mcp
pip install -e ".[dev]"
```

Two traps that cost real time:

- **Do not install through `conda run`.** It re-parses the argument and loses the
  extras bracket, failing with "no matching distribution". Activate first, or
  call the environment's `pip` by full path.
- **`nodefaults` is pinned in every `environment.yml`** because Anaconda's own
  channels answer `429` without client registration. Creating an environment by
  hand needs `conda create -c conda-forge --override-channels`.

Cross-module installs are the editable siblings, and they chain:
`/libvox-sync` needs `/libvox-driver-koha`, and `/libvox-core` needs both.
Install deepest first — pip reads a sibling's `pyproject.toml`, never its
`requirements.txt`, so a hop you skip is looked for on PyPI, where none of these
is published.

## Before you commit

Run both, in the module you touched:

```bash
ruff check .
pytest
```

**A green `pytest` is not always evidence.** Both `/libvox-sync` and
`/libvox-core` skip their database-bound tests when no PostgreSQL is reachable,
and in both the skipped set is the larger one: sync reports `31 passed, 119
skipped` and Core `110 passed, 106 skipped`. The tenant-isolation guarantee goes
unexercised while the run still looks clean. Start the server first —
`cd libvox-db && ./db.sh` — and say whether you did.

One concern per commit. Conventional-commit prefix (`feat:`, `fix:`, `docs:`,
`refactor:`, `test:`, `chore:`), imperative mood, no trailing period, subject
≤72 characters.

```
bad:  feat: implement centralized file logging, add agent configuration
      documentation, and expand research benchmarking notes.
good: feat: route MCP server logs to a single state file
```

The bad one is real (`4f79ede`) and is three commits wearing one hat. An agent
will write that by default — it summarizes the diff instead of naming the change.
Rewrite it.

## Never commit

Credentials, tokens, `.env` files, or anything read from `LIBVOX_CRYPTO_KEY`,
`LIBVOX_CORE_SERVICE_TOKEN`, `LIBVOX_MCP_SERVICE_TOKEN`, `LIBVOX_LLM_API_KEY`,
`KOHA_CLIENT_SECRET` or `OPENWEBUI_API_KEY`. Secrets come from the environment
only.

`LIBVOX_CRYPTO_KEY` is the one to be most careful with: it decrypts every
library's stored ILS credential, so a repository that leaks it leaks every
library's catalogue account at once.

A scratch file with a real key in it is the way this actually happens — not a
deliberate commit, but an unrelated commit that swept one up. Check `git diff
--staged` before every commit that touches more than the files you edited.

Also not: personal agent wrappers, session ids, resume shortcuts, or local editor
state. They are unresolvable on anyone else's machine and they leak how you work.

## Ask, don't assume

Stop and ask when a change touches:

- **Tenant isolation** — anything in `auth.py`, the RLS policies, or how a
  credential is resolved.
- **The tool contract** — schemas, tool count, argument names, output shape.
- **The index/live split** — which path answers which question.

Everything else: make the routine call and say what you assumed.
