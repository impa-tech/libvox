# Contributing to LibVox

Six modules run, and the spine works end to end — a reader asks a question on a
library's OPAC page and gets an answer from that library's catalogue. What is
most useful right now is listed in
[`README.md` §Contributing](README.md#-contributing).

Coding agents get their own instructions in [`AGENTS.md`](AGENTS.md), plus one
per module. Read those before writing code — the architectural invariants live
there, and they bind humans too.

## Branching

`main` takes no direct pushes. Branch, open a PR, get one approval.

This is convention, not configuration: there is no branch-protection rule and no
CODEOWNERS on the repository yet, and every commit before this document landed
directly on `main`. Someone with admin rights should configure it; until then the
rule holds only because we hold it.

Branch names use the commit prefixes: `feat/`, `fix/`, `docs/`, `refactor/`,
`test/`, `chore/` — for example `feat/folio-driver-auth`.

Keep a branch short-lived. The project is small and the modules are young; a
branch open for two weeks is a merge conflict with interest.

## Commits

One concern per commit; the message format is specified in
[`AGENTS.md` §Before you commit](AGENTS.md#before-you-commit), with a worked
example. Body explains *why* when the diff does not.

Agent-written commit messages default to summarizing the diff — three unrelated
changes joined by "and". Split the commit or rewrite the message.

## The README problem

`README.md` is roughly 1,200 lines and every architectural discussion touches it.
It is the highest-conflict file in the repository, and with more than one person working it
will conflict constantly unless we are disciplined:

- **One section per PR.** Do not sweep through the document.
- **Rebase before pushing.** `git pull --rebase origin main`.
- **Never reflow or reformat prose you did not change.** An editor or agent that
  rewraps a paragraph turns a three-line change into a two-hundred-line conflict
  that no reviewer can read. Check `git diff --stat` before committing — if a
  docs change is much larger than what you actually edited, something reformatted
  behind you.
- Prefer putting new detail in a module `README.md` and linking to it.

Same rule for Python: no drive-by reformatting. `ruff check` reports problems; it
is not licence to reformat a file you happened to open.

## Review

One approval from another core developer. What a reviewer is actually checking:

1. **The invariants in [`AGENTS.md`](AGENTS.md)** — tenant identity out of the
   model's reach, no logic in `/libvox-mcp`, no live-ILS fallback for search, no
   ILS writes, no new tool without an issue.
2. **Whether the change is what the PR says it is** — no unrelated files, no
   silent scope growth.
3. **Tests for behaviour that matters**, especially anything touching isolation.

Not style. CI runs `ruff` and `pytest`; a reviewer arguing about formatting is
duplicating a machine.

**Locally, a green `pytest` may not cover the isolation tests.** Both
`/libvox-sync` and `/libvox-core` skip their database-bound tests when no
PostgreSQL is reachable, and in both the skipped set is the larger one: sync
reports `31 passed, 119 skipped`, Core `110 passed, 106 skipped`. The module's
central guarantee goes unexercised while the run looks clean.

CI provides a `postgres` service, so sync runs 119 of its 150 there. If you are
reporting a local result on anything touching RLS, start the server first — the
one in `/libvox-db`, shared by every module — and say that you did:

```bash
cd libvox-db && ./db.sh
```

## Working with LLM assistance

Agent-written code is welcome. The rules are about accountability, not suspicion:

- **You are the author.** Read the whole diff before opening the PR. "The agent
  wrote it" is not a review comment, and it is not a defence.
- **Keep the agent in scope.** Agents helpfully "fix" neighbouring code; that is
  how a small PR becomes unreviewable. The test is whether every file in the diff
  follows from what the PR says it does — not whether they share a directory,
  since a driver contract change legitimately reaches `/libvox-sync` and
  `/libvox-core`, which both depend on it.
- **Agents will contradict the invariants, confidently.** `tenant_id` as a tool
  argument, business logic in the MCP layer, a live-ILS fallback when the index
  misses — each looks like good design and each is explicitly rejected in the
  README. If you think a rule is wrong, that is an issue, not a PR.
- **Do not weaken a test to make a change pass.** `test_no_tool_accepts_a_tenant`
  and `test_the_contract_has_no_place_for_a_tenant` exist to fail exactly when
  someone is doing this.
- **Verify claims against a running system.** An agent that says it validated
  something against Koha, when no Koha was running, has said nothing. See
  `docs/install_koha.md`.
- **A delegated agent may start with none of this.** Whether `AGENTS.md` reaches
  a subagent depends on the tool and its version, and in at least some setups it
  does not — the delegated task begins with an empty context and finds the rules
  only if it happens to list the repository first. Do not rely on it: name
  `AGENTS.md` in the task description, or paste the invariants that apply.
  Cheap to check, and worth checking once for whatever you use — ask the agent
  what was in its context before it read any file.

## Before opening a PR

In every module you touched, in its own conda environment: `ruff check .` and
`pytest`. Setup, the conda traps, the commit-message convention, and the caveat
about what a green `pytest` does *not* prove in `/libvox-sync` are all in
[`AGENTS.md`](AGENTS.md) — that file is the single home for those rules, so they
do not drift between two documents.

## Security

Never commit credentials, tokens, or API keys. Everything is configured through
environment variables — `tools/koha.sh` included. If a secret does land in a
commit, say so immediately and rotate it; rewriting history alone does not
un-publish it.
