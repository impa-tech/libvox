## What changed

<!-- One or two sentences. What this does, not a list of the files. -->

## Why

<!-- The problem or the decision behind it. Link the issue if there is one. -->

## Invariants touched

Tick anything this PR comes near. Any tick means a reviewer reads `AGENTS.md`
in the repository root alongside the diff. (Plain filename, not a link — GitHub
does not resolve relative file paths from a PR body.)

- [ ] Tenant isolation (`auth.py`, RLS policies, credential resolution)
- [ ] The MCP tool contract (schemas, tool count, argument or output shape)
- [ ] The index / live-ILS split (which path answers which question)
- [ ] Database schema or migrations
- [ ] None of the above

## Verification

<!-- What you actually ran, and what it said. Not what should work. -->

- [ ] `ruff check .` passes in every module touched
- [ ] `pytest` passes in every module touched
- [ ] Tested against a live Koha (say which, or say not applicable)

## LLM assistance

- [ ] An agent wrote part or all of this, and I have read the whole diff
- [ ] The diff contains no files outside this PR's stated scope
