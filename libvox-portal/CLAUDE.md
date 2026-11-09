# /libvox-portal

Rules for this module are in [`AGENTS.md`](AGENTS.md), alongside the repo-wide
ones in [`../AGENTS.md`](../AGENTS.md). Read those; this file only points at
them.

One file rather than two, because two drift. This one used to hold a copy of the
Laravel Boost bootstrap and nothing else — no mention of the Tenant DB schema
this module owns, of the write-only ILS secret, or of the privileged-connection
check that is the whole isolation guarantee. An agent reading it got the
framework's generic setup instructions and none of the rules that matter here.

The Boost bootstrap still exists, at the bottom of `AGENTS.md`, behind a
statement of when it applies.
