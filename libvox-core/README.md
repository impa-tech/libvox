# /libvox-core

The tenant boundary. `/libvox-mcp` declares the tools; this is what answers
them, resolving every call against exactly one library.

Three things live here: the tool implementation, MARC.IA's loop, and the
channel gateway. A reader on a library's OPAC talks to her over a WebSocket, and
the widget is one line of HTML the portal generates — see
[The web channel](#the-web-channel).

## The internal API, and two rules it imposes

`libvox-mcp/src/libvox_mcp/core_client.py` is the specification for these
routes, and two of its properties shape everything here.

**A status code carries nothing.** The client collapses every 4xx and 5xx into
one opaque sentence for the model. So a condition the model has to act on is a
**200 with a field**, never a status — which is why an index that is still
filling answers `index_status: "building"` rather than 503.

**Introspection never raises.** An unreachable Core degrades to "nobody is
authenticated", not to an error, and every 2xx on that path must be JSON or the
token verifier dies decoding it.

| Route | Purpose |
| :--- | :--- |
| `POST /internal/v1/sessions` | Bind a session to one library, from a channel key |
| `POST /internal/v1/sessions/introspect` | Resolve a credential to its library |
| `POST /internal/v1/search_catalog` | Answer from the Discovery Index |
| `POST /internal/v1/check_availability` | Answer live, through a driver |
| `POST /internal/v1/messages` | Answer a patron, which is MARC.IA's whole job |
| `POST /internal/v1/ils/check` | Try a library's catalogue connection, and say what broke |

## Setup

```bash
conda env create -f environment.yml
conda activate libvox-core

# Siblings in this repository, not published packages.
pip install -e ../libvox-driver-koha -e ../libvox-sync
pip install -e ".[dev]"

export LIBVOX_CRYPTO_KEY="$(grep '^LIBVOX_CRYPTO_KEY=' ../libvox-portal/.env | cut -d= -f2-)"
python -m libvox_core          # http://127.0.0.1:8091
```

The database has to be up. All three Core touches are on the one server in
`/libvox-db` (port 5435): the Tenant DB it reads, the Discovery Index it
searches, and the conversation log it writes.

```bash
cd ../libvox-db && ./db.sh
```

In a container instead:

```bash
cp .env.example .env      # then fill in LIBVOX_CRYPTO_KEY
./core.sh                 # build, restart, wait until it answers
./core.sh check           # Core's four routes, against a real library
./core.sh drive           # the same, with /libvox-mcp in front
./core.sh ask lib-local "vocês têm alguma coisa sobre chess?"
```

`check` drives Core's HTTP routes directly, passing `tenant_id` the way the MCP
layer would after resolving it — so it proves this component and stops there.
`drive` puts the MCP layer in front: the real `LibVoxTokenVerifier` asks the
real Core who a credential belongs to, the tool schemas are the ones a model
would see, and a call carrying no credential is refused by `require_tenant`
before it reaches Core. It is the only thing here that covers the tenant
boundary rather than one side of it, and it runs in `/libvox-mcp`'s environment
because it needs the MCP SDK.

`./core.sh` always rebuilds *and* recreates. `docker compose build` alone does
nothing visible — the image is rebuilt while the running container keeps
serving the old code, and the symptom is a bug you already fixed still
happening. That is not hypothetical: a stale Core answered availability with an
empty list for twenty minutes while the fix sat in an image nobody was running.

> **A library whose ILS is on `localhost` is unreachable from the container.**
> Inside a container `localhost` is the container, so a `base_url` of
> `http://localhost:8081` — which is what a local Koha Testing Docker gets
> stored as — fails with `upstream_unavailable` and an empty availability list.
>
> Two ways out. Run Core on the host with `python -m libvox_core`, which
> reaches a host-side Koha directly. Or change that library's catalog URL in
> the portal to `http://host.docker.internal:8081` — compose already declares
> `extra_hosts: host-gateway`, so that name resolves to the `docker0` gateway
> (`172.17.0.1`) inside the container and needs no further wiring. The trade is
> that the value then only works from a container.
>
> In a real deployment a library's ILS has a real hostname and neither arises.
> `./core.sh check` detects the failure and says so rather than reporting a
> pass.

**Port 8091.** The MCP client's default for `LIBVOX_CORE_URL` is 8080, which is
Koha Testing Docker's OPAC — a developer working on this project has one
running. The portal picked 8090 for the same reason, and the database 5435.

## MARC.IA

`libvox_core/agent/` is her own MCP client, not a third-party framework. It
lives inside the Core rather than beside it, and the reason is the invariant
everything else rests on: **the model returns an intent and the Core executes
it.** Splitting the loop from the component that resolves the tenant would mean
passing credentials across one more boundary, or resolving the tenant twice.

She owns four things nothing else does — the system prompt, the tool registry,
conversation state, and the model connection.

### The prompt has three layers, and they are not equal

The **policy layer** says what she may claim, and no library administrator can
reach it. The **example layer** shows the shape of an answer. The **persona
layer** — a name, a tone, a language — is the library's.

The database already enforces half of that: `tenant_widget` has
`assistant_name` and `welcome_message` as free text, `persona_tone` and
`default_locale` as enums, and no free-text instruction column at all. A
textarea reaching the model's instruction slot would let an administrator write
something reasonable-sounding — *"if the catalog has nothing, answer from your
own knowledge"* — and silently disable the guarantee the whole system rests on.

This enforces the other half. Persona goes **last**, inside a delimited block
framed to the model as material the library wrote rather than instruction it
obeys. The markers are stripped from library text: a greeting carrying the
closing marker would end the block early and put whatever followed where
instructions live, and that is the one injection here that would actually work.

**The examples sit in the middle, and their data is a tripwire.** Eleven policy
rules all say what MARC.IA may not claim; none says what an answer looks like,
so the same correct content arrived as three paragraphs one turn and half a
sentence the next. Two worked exchanges show the shape instead — a small
open-weight model imitates a form far more reliably than it follows a
description of one.

The known cost of that is a model reading an example as fact: a `hermes3:8b`
once answered a question about cookery by citing an author it had seen
demonstrated. So nothing in these examples is real. The title, the author, the
branch and the shelf are invented, and a test holds them to appearing in that
one file — which means **"Helena Vasques" turning up in a conversation came from
the prompt and from nowhere else**, and the operator's conversation screen can
be searched for exactly that. The risk does not vanish; it stops being silent.

That test earned itself immediately. The invented branch was first written
"Biblioteca Central", which is the greeting in the module's own test fixture —
an invented datum that was not invented, and it broke the test proving a
greeting never reaches the model.

The examples are per locale, like the tone and language lines, because an
example in Portuguese shown to a library configured for English is an
instruction to answer in the wrong one. What does *not* change with the locale
is the catalogue's own strings: a record is quoted as the library recorded it,
and the English example keeps the Portuguese shelf to say so.

### The tools are whatever `/libvox-mcp` declares

No local list. `list_tools()` on the connection is the registry, which makes it
the one enforcement here that is structural rather than advisory: MARC.IA
cannot search the web because there is nothing to call.

Over HTTP with a Bearer, never stdio — *"authorization exists only over HTTP …
on stdio every caller is anonymous, so every tool call is refused for want of a
tenant"*. One connection per session, because a connection outliving its
session would be a credential outliving its scope.

A tool the model invented is refused before anything executes. A tool that
fails is a turn rather than a crash, and the reason — a 403, an ILS URL, a
permission name — never crosses into the answer.

**Each timeout exceeds the one it waits on**, and the ordering is asserted in
`test_config.py` rather than left to a comment. The Koha driver allows thirty
seconds per request; `/libvox-mcp` allows sixty for the call it makes back here
(`LIBVOX_CORE_TIMEOUT`); and this waits ninety on the tool layer
(`LIBVOX_MCP_TIMEOUT_S`). One `check_availability` against a library on another
continent is four sequential requests, so an outer layer that gave up first
would discard an answer that had already arrived. A reader is not made to wait
ninety seconds by that — the loop's round limit and the channel bound it.

### What she remembers, and what she forgets first

The history goes back to the model on every turn, and what goes back is not
only the words:

```
user       "do you have anything by Dürrenmatt?"
assistant  → search_catalog{"query": "Dürrenmatt"}
tool       ← {"index_status": "ready", "records": [{"biblio_id": 137, …}]}
assistant  "Two, one of them Gustav Mahler."
user       "is the second one available?"
```

Without the `tool` line the model has no `biblio_id` for the second book and
can only invent one or search again. So a tool result is a message like any
other and it carries JSON, both in context and in the table.

Tool results are bulky, so something has to go. What never goes, in order: the
policy layer, the current question, recent turns newest-first, and **old tool
results before the sentences they produced** — *"we have two: Ein Engel and Der
Besuch"* keeps what mattered in a fraction of the space. So in a long
conversation she forgets the ids from the beginning and searches again, which
is right: one more query against the index costs the library nothing, and an
invented id reaching `check_availability` would.

### The loop ends, and it says so to the model

Six rounds. The chain a question needs is two — search, then check availability
— and six is room for a model that varies a term twice and still checks the
shelf. Without a limit a small open-weight model chains searches until the
context bursts.

**Raising it is not free, and this number is also the ceiling on what a reader
waits through.** A round is a model call plus a tool call, each re-sending the
whole history, so token cost grows with the square of the rounds. And nothing
bounds a whole response: there are per-call timeouts and no overall one, so
`max_rounds` is the only brake. Ten was asked for and refused on those grounds —
ten wants an `asyncio.timeout` around `respond()` first.

**Withdrawing the tools used to be silent, and that was a question in itself.**
The final call handed the model the same history with no tools and no
explanation, so it answered what the history implied came next. A reader asking
for chess books got *"I've already searched for xadrez multiple times… Let me
search in English and Spanish now"* — in English, citing a rule number.

`_spoken` never had a chance at it: no `<think`, no tool name, no bracket, and no
marker list distinguishes a plan from an answer.

**And a reply that is nothing but reasoning is refused, not shown.** A reader
once received the model's entire monologue — thousands of words weighing this
system's own numbered rules, drafting and redrafting a sentence, in English —
because the endpoint had put the reasoning in `content` instead of
`reasoning_content`. There was no delimiter to cut on: the answer, if there was
one, sat at the end of 15 KB.

So the test is the opening, not the length. A reply that *starts* with `Thinking
Process:` or `Let me think` is not an answer, and where its answer is cannot be
known — the reader gets the unavailability sentence and the operator's log gets
the cause and the cure, naming `custom_llm_provider` and the two Ollama
endpoints. A sentence that merely mentions thinking later is untouched, and
length is not a signal: a long shelf list is a long answer. The fix was to stop asking the
question that produces one. The final call now carries a line saying the tools
are gone and to answer from what is already there — appended to the payload and
never to the conversation, since stored it would return on the next turn and
appear on the operator's screen as something the library said.

**And the same call twice does not run twice.** Policy rule 7 says never repeat
a search, and a reader asking about pokemon watched
`search_catalog{"query": "pokemon"}` run six times, identically, until the round
limit. The trigger was `truncated: true` — five records matched of more, which
rule 8 says to report as "at least five" and which the model read as a reason to
try again. The same query returns the same five, forever.

A rule the model can ignore is not a bound, so the loop keeps every
`(tool, arguments)` it has already run this turn and refuses the second one. It
saves an index query, and on `check_availability` it saves one live HTTP request
per record against a library's own server — the traffic this project is most
careful about.

What the repeat gets back is deliberately **not** the earlier result. Handing
back the same records leaves the model's input unchanged, and unchanged input is
what a loop is; it gets a sentence naming what it already did, which is the only
kind of information that can end the cycle.

It is scoped to one turn, and that is required rather than incidental. Rule 9
says availability must never be reused from earlier in the conversation, so a
reader's second question has to be able to check the same book again. Hoisting
the record onto the conversation would silently repeal that rule.

The budget used to be four, and four was tight for a reason that is now gone.
Rule 7 asked for three searches before saying a library holds nothing — so
*every* question whose answer was "we do not have it" spent the budget and
reached that final call. That rule is now one search and one sentence.

### Which model, and what a smaller one costs

`Qwen3.5-9B` at Q6_K, and the numbers below are why it is the default in
`.env.example` rather than a preference. Size is not the axis it looks like:
what this workload needs is a *chain* — search, then check the shelf — and
models of the same size differ more on that than models of different sizes.

The same question — *"Opus posthumous está disponível?"*, against `lib-local`,
where the record exists and one copy is on the shelf — run eight times on each.
**`./core.sh bench` is that measurement**, and the question is a constant inside
it rather than an argument: a default is something a caller overrides without
noticing, and the result is a row that does not belong in this table. It prints
the row ready to paste, naming the model the container is actually running
rather than the one `.env` claims — those diverge, because compose interpolates
`${LIBVOX_LLM_MODEL}` from whichever shell started the container.

```bash
./core.sh bench              # lib-local, 8 runs
./core.sh bench lib-local 3  # fewer, while iterating
```

It refuses rather than measuring when the endpoint does not know the model: the
first answer comes back in under three seconds having called no tool, and eight
of those look like a result. That is how `hermes3:8b` began, and how a `.env`
left pointing at an unserved model spent an afternoon looking like a broken
agent.

| | finds it and checks availability | searches per answer | median |
| :--- | ---: | ---: | ---: |
| `Qwen3.5-9B` Q6_K | **8 / 8** | 1 | **4.8 s** |
| `qwen3.6:35b-a3b` | **8 / 8** | 1 | 10.4 s |
| `qwen3.8:27b` | **8 / 8** | 1 – 3 | 36.9 s |

**The 9B wins outright**, which was not the expected result and is worth stating
plainly: same answer quality as a model four times its size, at a fifth of the
slowest one's time, and it is the only one of the three that comes close to
fitting a 16 GB card. Its quant matters to that claim — Q6_K is six bits, where the rounding is small
enough that the model behaves like the original. This measures a model, not a
compression artifact.

The medians carry a caveat the numbers do not: every model here was running
partly on the CPU, because the card they share holds two other services. The
**median column is therefore about that machine**, not about those models — the
9B measured 5.7 s on an earlier run with 13% of it on the CPU, and 4.8 s on a
later one, without the model changing at all. The
searches-per-answer column is not — that is the model's own behaviour, and it is
where the 27B is worse: one to three searches where the other two do one.

Two earlier rows, measured against the same question before the prompt carried
its example block, and no longer reproducible because the endpoint stopped
serving both models:

| | finds it and checks availability | searches per answer | median |
| :--- | ---: | ---: | ---: |
| `ornith-1.5:9b` | 7 / 8 | 3 – 8 | 6.7 s |
| `hermes3:8b` | **0 / 8** | 1 | 1.6 s |

`ornith-1.5:9b` is the sharpest comparison in the table. Same size class as the
winner, same speed — and it searches three to eight times where `Qwen3.5-9B`
searches once. The lesson is not that 9B is enough; it is that *this* 9B is
good, which the Berkeley Function Calling Leaderboard also says: 0.661 against
0.685 for the 27B two rows above it.

`ornith-1.5:9b` gets the term wrong. It mistypes what
the reader wrote on its first search — "Opus posthumus" for "posthumous" — and
once it has an empty result it starts guessing: it decided the book was
Faulkner's, then Max Frisch's, and searched for what each belief implied. The
conversation log has all of it. The larger model searches the reader's words,
once, and answers.

**`hermes3:8b` is the interesting failure**, and it is not a slow one — it is
ten times faster than the 35B and answers in a second and a half. It searches
once, correctly, with the reader's own words. Then it stops and describes the
record. It never calls `check_availability`, in eight runs out of eight, so it
never answers the question that was asked: *"está disponível?"* is met with a
publisher and an ISBN.

The same shape on a search that finds nothing. Asked about culinária it searched
once and then *offered* to try English — which is rule 4's failure exactly, a
question asked in place of work it could have done. One run sent the reader to
look in magazines instead.

And it read its own arguments back as findings. Asked the availability question
it called `search_catalog` with `{"limit": 5, "query": "opus posthumous"}` — the
`limit` was its own choice — got **one** record back, and answered *"Há cinco
edições listadas no catálogo"*. The number came from the request, not the reply.

A model advertised for tool use turns out to be trained for *a* tool call rather
than a chain of them, and every question this system answers is a chain: search,
then check the shelf. The measurement is worth keeping precisely because the
model works — the endpoint returns `finish_reason: tool_calls`, the schemas are
accepted, nothing errors. It answers zero out of eight anyway, three batches
running.

It also paid for the guard on what a reader is shown. Two of its replies were
tool calls written out as prose, and the guard let both through — it was a list
of opening tags, and hermes wrote `<THINKING>` and then, on the next batch,
`<SCRATCHPAD>`, a tag nobody had seen. Each widening of the list held until the
following run. What every leak had in common was not a tag but **the name of a
tool this system had offered**, and `search_catalog` is an identifier no
sentence written for a reader has any use for. That is the test now, and it is
the tool names in play rather than a list to maintain. Zero leaks in the eight
runs after.

Three of the policy rules exist because of failures only the 9B produced, and
they stay: a rule that costs nothing on a model that would not have broken it is
insurance for the day a library runs something smaller. But the rules narrowed
that model's failure rate rather than closing it, and the residual — a
transcription slip on the first query — is not something a prompt can reach. A
trigram fallback in the Discovery Index would match a word off by one letter,
and the current configuration (`unaccent` + `portuguese_stem`) does not.

### What Core needs from an endpoint, and how two proxies differ

Core reads `choices[0].message.content` and shows it to the reader. So an
endpoint has exactly one obligation beyond speaking OpenAI: **whatever reasoning
the model does must not land in that field.** The two proxies this was measured
against handle that differently, and the difference is not a setting on our side.

Same request, same model, same temperature:

```jsonc
// Open WebUI — two fields
"message": {
  "role": "assistant",
  "content": "Entre 3 de março e 17 de julho de 2026, passam-se 136 dias…",
  "reasoning_content": "Thinking Process: 1. Analyze the Request…"
}

// LiteLLM with custom_llm_provider "ollama" — one field, 15 KB of it
"message": {
  "role": "assistant",
  "content": "Thinking Process:\n\n1. Analyze the Request…\n\n[15,699 chars]\n…136 dias."
}
```

**Open WebUI works with no configuration.** It talks to Ollama's `/api/chat`,
which returns the thinking in a field of its own, and the clean `content` is what
Core shows.

**LiteLLM needs the model set to `ollama_chat`.** Its `ollama` provider calls
`/api/generate`, which has no thinking field at all — so there is nothing to
separate, and the model writes its reasoning as ordinary prose at the top of the
answer. In the model's `litellm_params`:

```jsonc
{
  "model": "ollama_chat/granite4.2:latest",   // the prefix, per LiteLLM's docs
  "custom_llm_provider": "ollama_chat"
}
```

The public `model_name` does not change, so `LIBVOX_LLM_MODEL` does not either.

One request tells you which shape you have:

```bash
curl -s "$LIBVOX_LLM_BASE_URL/chat/completions" \
  -H "Authorization: Bearer $LIBVOX_LLM_API_KEY" -H 'content-type: application/json' \
  -d '{"model":"'"$LIBVOX_LLM_MODEL"'","temperature":0,
       "messages":[{"role":"user","content":"Quantos dias entre 3 de março e 17 de julho de 2026?"}]}' \
  | jq '.choices[0].message | keys'
```

`["content","reasoning_content","role"]` is the shape Core wants.
`["content","role"]` on a model that thinks means the reasoning is in the answer.

**And the text is the smaller half of the problem.** LiteLLM's documentation
warns that the `ollama` provider produces "infinite tool call loops and ignoring
previous context", and that is measurable here. Against the same model:

| | finds it and checks | searches per answer | miss answered plainly |
| :--- | ---: | ---: | ---: |
| Open WebUI | 8 / 8 | **1** | 4 / 4 |
| LiteLLM, provider `ollama` | 4 / 4 | **4 – 5** | **0 / 4**, all narrating a plan |
| LiteLLM, provider `ollama_chat` | **8 / 8** | **1** | **8 / 8** |

The transcript shows why: `search_catalog{"query": "Dom Casmurro"}` followed by
`check_availability{[89]}`, **three times identically**, until the round limit.
It was not varying a term; it was not registering that it already had the answer.

So silencing the reasoning is the wrong repair even though it works.
`{"think": false}` and `{"reasoning_effort": "none"}` in `LIBVOX_LLM_EXTRA_BODY`
do clean up `content` — `enable_thinking` and `chat_template_kwargs` travel and
are ignored — but the middle row is *with* thinking off. The reasoning was
what let the model notice it was done, and the loop is the same misconfiguration
wearing a different symptom. Fix the provider, not the text.

**The third row is that fix, measured** — 2026-09-03, `Qwen3.5-9B` Q6_K on
`ollama_chat`, `LIBVOX_LLM_EXTRA_BODY` empty, thinking on. The response carries
`["content","reasoning_content","role"]` with 494 characters of answer and 12,304
of reasoning beside it, and `./core.sh bench lib-local 8` reproduces Open WebUI's
numbers to within a tenth of a second: 6.9 s and 6.8 s on two runs against
7.0 s on the hit, 3.8 s both times against 3.7 s on the miss, nothing leaked
either way. Two proxies, one prefix
apart. The prescription above was written before anyone had run it; this is the
run.

## The conversation is kept

In `libvox_conversations`, a database Core owns. On the Tenant DB's PostgreSQL
instance, because the rule the architecture protects is **one writer per
database** and not one database per server — Core still writes nothing to
`libvox_tenant`.

```bash
./core.sh ask lib-local "vocês têm alguma coisa sobre chess?"
```

### Continuing one, which is the whole point of keeping it

Every answer prints the command that continues it:

```bash
$ ./core.sh ask lib-local "vocês têm alguma coisa sobre chess?"

A biblioteca possui registro de duas obras sobre xadrez:
1. The ideas behind the chess openings (Reuben Fine)
2. Pawn structure chess (Andy Soltis)

  tools used: search_catalog, search_catalog

  ./core.sh ask lib-local --in eb6b5db9-f2e9-4399-b58a-ada43770dfff "..."

$ ./core.sh ask lib-local --in eb6b5db9-f2e9-4399-b58a-ada43770dfff \
    "sim, o segundo está disponível?"

Sim, "Pawn Structure Chess" de Andy Soltis está disponível. Existem 4
exemplares na biblioteca, todos prontos para empréstimo.

  tools used: check_availability
```

Two things in that second turn are the reason the log exists. *"The second
one"* resolved to a title MARC.IA was never told again — the `biblio_id` came
from the stored tool result. And **`search_catalog` did not run**: she went
straight to availability, because the id was in the history rather than only in
the sentence about it.

Through the API it is one field:

```json
{"session_id": "...", "text": "...", "conversation_id": "..."}
```

Continuing mints a **fresh session**. That is not an oversight: a session is a
bearer credential with a short life, a conversation is a transcript with a
ninety-day retention period, and tying them together would make the retention
period apply to a token.

### Applying the schema

```bash
./core.sh migrate          # or: python -m libvox_core migrate
```

**This did not exist until the second migration needed applying.** `001` reached
production because someone ran it by hand, and the only caller of the runner was
the pytest fixture — a schema whose runner is the test suite drifts, and the
drift is silent until a query names a column nobody created.

The runner is `/libvox-sync`'s, pointed at this module's directory, exactly as
`tests/conftest.py` has always called it. Core already imports `tenant_scope`
from that module, so the dependency is not new.

It builds the image first, and that is not caution. Run against whatever image
was already there, `migrate` is an argument the previous entrypoint does not
recognise, so it falls through to the default and starts the web server — a
command that produces no output, no error, and never returns. Observed once,
which is once.

### It is forgotten after ninety days

```bash
./core.sh prune --dry-run          # count what would go
./core.sh prune                    # ninety days, the default
./core.sh prune --older-than 30
```

Retention is not optional here. Storing a conversation does not break the root
README's *"LibVox does not store patron identity"* as long as nothing in it
identifies anyone **and** there is an expiry; without the second half a
transcript log is an accumulation, not a record. Ninety days is long enough to
investigate a reported problem and short enough not to become an archive — and
if a library has an institutional policy, that policy wins, and the window
becomes a column on `tenants` rather than a constant in the code.

**It needs its own connection, and that is the interesting part.** `prune`
spans every library by definition, and `libvox_app` cannot: row-level security
is fail-closed, an operator's job sets no tenant scope, so the role sees no
conversations, its `DELETE` matches nothing, and it commits cleanly. Measured
on the dev database before the guard existed — **0 of 30 visible, 0 candidates
for deletion.** A retention job that logs `forgot 0` every night is worse than
none, because nobody looks again at a task that reports success.

So `LIBVOX_CONVERSATION_ADMIN_DSN` is separate from the one the service writes
through, mirroring `/libvox-sync`'s `admin_dsn`, and `prune` **refuses** a
connection that is subject to the policy instead of returning zero:

```
libvox_app is subject to row-level security, so it sees no conversations
and would delete none while reporting success. Point
LIBVOX_CONVERSATION_ADMIN_DSN at a role that can read across libraries.
```

A `SECURITY DEFINER` function looked like the more principled answer and is
not: `FORCE ROW LEVEL SECURITY` subjects an owner to its own policies, so it
works where the owner is a superuser and silently returns nothing where
provisioning made the owner unprivileged. `/libvox-portal` already wrote that
lesson down in the migration that dropped the idea.

The service never loads the admin DSN, and `./core.sh prune` runs in a
throwaway container rather than the running one: a long-lived web process
should not hold a credential that reads across libraries.

### Whether it helped

Two thumbs under each of MARC.IA's answers in the widget, and the operator's
panel reads them. Three columns hold it — the message, a boolean, a timestamp —
and the fourth is the one that is not there: no session id, no device
fingerprint, no IP, no free-text field. What someone thought of an answer is
more sensitive than the answer, not less.

**The value is the pairing, not the count.** A thumbs-down on an answer whose
tool result was `{"records": []}` says the library told a reader it holds
nothing and the reader disagreed — a gap in the collection, a gap in the index,
or a search that failed on the word they used. That is worth acting on; a
percentage of unhappy readers is not, and both cost the same to collect. So
there is no tally anywhere: the listing filters, and the transcript puts the
thumb next to the tool result that produced it.

**The browser is handed nothing to rate with.** A rating names an answer by its
ordinal — *the third* — which is what a reader can honestly point at. A
`message_id` would be worse than the conversation id the gateway already keeps
to itself: it is a global sequence, so it leaks how much the whole installation
has answered.

That ordinal is sound only because every path that answers a reader stores
exactly one assistant message. One did not, and the gap was invisible until
something needed to count: nineteen of a hundred and seventy-six conversations
were one turn out of step. `test_loop.py` pins it now.

### Nothing in it identifies a person

*"LibVox does not store patron identity. Conversations are scoped to a session,
not to a person"* was easy while history died with the process. Now what
someone asked a library is durable, so a leak exposes it — and the non-goal
survives only if the schema makes it survive.

There is no column for a name, an email, a phone number, an IP or a platform
identifier, and a test reads `information_schema` to keep it that way. The
`conversation_id` is a uuid of its own rather than the session token: a bearer
credential used as a primary key ends up in every backup and every screenshot
of a result set.

Three consequences that are part of the design, not caveats:

- **A channel adapter must not let a platform identifier through.** Mapping a
  Telegram chat id to `session_id` happens inside the adapter and stops there.
  With persistence, a durable one leaking would turn a log into a profile.
- **Retention is a command, not a suggestion.** `prune` deletes by date, spans
  libraries deliberately — it is the operator's job, and a `DELETE` naming only
  a date leaks no transcript — and messages go by cascade. **Ninety days**:
  long enough to investigate something reported, short enough not to become a
  collection.
- **The patron's text goes to the table and nowhere else.** Never a log line.

### The greeting is the channel's line, not the model's

`tenant_widget.welcome_message` reaches the widget and **not the prompt**. A
greeting quoted into the persona layer is read by a small open-weight model as
an example to reproduce, and it answers with the library's welcome message word
for word, under the copy the widget has already shown.

Greeting a reader is the channel's job — the widget displays it before a
question is typed, and a channel with no screen of its own sends it as a first
message. The model has no use for the text and every opportunity to repeat it.

Leaving it out is also the strongest form of an injection defence that used to
be a sanitizer: what is absent cannot close the persona block. The one
library-authored string still reaching the prompt is `assistant_name`, and it
is cleaned — it is `text NOT NULL` with no length limit, the sixty characters
being a form rule rather than a constraint the database enforces.

### Two opaque directions, and neither teaches this project a vendor

`LIBVOX_LLM_EXTRA_BODY` is a JSON object merged into every request to the model,
and Core never reads it. It is the mirror of the section below: one direction
carries back what a provider attached to a tool call, the other carries out what
a deployment wants sent.

It exists because a thinking control is spelled differently by everyone —
Granite takes `enable_thinking` and `reasoning_effort`, Gemini takes
`reasoning_effort`, Ollama takes `think`. Knowing one here would mean knowing all
of them, forever.

The merge is deliberately ordered so configuration cannot overwrite `model`,
`messages` or `temperature`. The first two are not settings, and `temperature: 0`
is a decision this codebase argues for: two identical questions about the same
catalogue should not get two different answers, and creativity here shows up as
an invented shelf mark.

Malformed JSON is refused at startup rather than ignored. A deployment that meant
to turn thinking off and quietly did not would surface as a latency number
nobody could explain, days later, in a table.

**Whether thinking helps here is measurable, and it was measured.** Turning it
off on `Qwen3.5-9B` took the model from one search per answer to four or five,
and every miss ended narrating a plan — the reasoning was what let it notice it
already had the answer. See [What Core needs from an
endpoint](#what-core-needs-from-an-endpoint-and-how-two-proxies-differ): the
reasoning belongs in `reasoning_content`, not silenced and not in `content`.

### What the provider attached, given back untouched

A tool call can come back carrying data the OpenAI schema has no field for, and
some providers require it on the next request. Gemini's thinking models sign the
reasoning behind a call and return the signature under `extra_content`; send the
tool result without it and the request is refused:

```
400 Function call is missing a thought_signature in functionCall parts
```

Which is a specific shape of failure: the first call succeeds, the search runs,
and the call carrying its result dies.

It bites here harder than it bites a thin client, because `Message.as_wire()`
**reconstructs** an assistant tool-call message from `tool_name`,
`tool_arguments` and a call id. Nothing is dropped — the field never existed.

So a message keeps `provider_extra`, and hands it back verbatim. **The Core
never opens it**, and it is not named after what put it there. That is what keeps
"any OpenAI-compatible endpoint" true rather than true-with-an-exception, and it
is why the next provider with a field of its own costs no edit.

It is a column rather than a value in memory. The history goes back to the model
on every turn and a conversation is rebuilt from rows between turns, so without
storing it a reader's *second* question fails exactly as the first did. The call
id above it is synthesized on load, which is right — only the pairing has to
survive. A signature is the opposite: the same bytes or nothing.

And it is emitted only when a provider put something there. A field an endpoint
does not know is a 400 from a strict one, which is the failure this exists to fix
rather than to cause.

#### Pointing Core at Gemini

Three variables, and the base URL is the one that trips people:

```bash
LIBVOX_LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
LIBVOX_LLM_MODEL=gemini-flash-latest
export LIBVOX_LLM_API_KEY="..."     # the shell, never a file
```

`/v1beta/openai`, and **nothing after it** — the client appends
`/chat/completions` itself.

Google's quickstart shows a different API, and it is not the one Core speaks.
The key is the same for both; everything else moves:

| | Google's quickstart | What Core needs |
| :--- | :--- | :--- |
| Path | `/v1beta/models/{model}:generateContent` | `/v1beta/openai/chat/completions` |
| Auth | `X-goog-api-key: <key>` | `Authorization: Bearer <key>` |
| Body | `contents` → `parts` → `text` | `model` + `messages` |

**Measured 2026-09-10, `gemini-flash-latest`.** The signature arrives on the tool
call under `extra_content`; the round trip that carries it back answers from the
tool result; and stripping that one field reproduces the 400 above word for word,
`position 2` and all. Which is this whole section working end to end against the
provider that prompted it — nothing to configure, because the carrying-back is
not a setting.

It thinks by default, and invisibly: asked for a one-token `ok`, the response
reported 132 total tokens, so roughly 124 of them were reasoning nobody sees.
`reasoning_effort` in `LIBVOX_LLM_EXTRA_BODY` is the control. Whether turning it
down helps *here* is measurable and not yet measured — `./core.sh bench` is the
arbiter, as it was for the proxy table above, and the warning from that table
stands: on the local 9B, silencing the reasoning was what broke the loop.

#### `./core.sh` migrates before it serves

The conversation log's schema is checked against `migrations/` on every start,
and applied when it is behind — the same thing `./sync.sh` does for the index,
reached the same way.

Code shipping ahead of its schema is not a loud failure here, which is why it
needs a guard rather than a habit. The container comes up, answers `/healthz`,
takes a question, drives the model, runs both tools — and then the *insert*
fails. A reader waits through a full round trip to be told the library's systems
are unavailable, and the reason is a missing column in a log nobody is tailing.

It runs after the build, so the migration applies on the code about to answer,
and it asks the database again afterwards rather than trusting an exit code.

### The system prompt is rebuilt, never stored

A policy layer read back from a row is a policy layer someone can edit in the
database. And a library that renamed its assistant should not have the old name
resurface mid-conversation.

> **`docker compose down -v` in `/libvox-portal` takes the conversations with
> it.** The same coupling `libvox_tenant_test` already has. The way out, when
> it matters, is one DSN — no code changes, which is the property that
> motivated separating by database rather than by server.

## Three rules that became this component

The root README lands propagation rules 3, 5 and 7 here and calls them this
component's security acceptance criteria. Each is a test.

### Rule 7 — the credential is opaque, short-lived, and cannot be re-scoped

A session says which library a call belongs to and nothing else. `SessionStore`
has `mint`, `resolve` and `revoke`, and no operation that rebinds an existing
credential to a different library — not a slow path, an operation that does not
exist. The token is `secrets.token_urlsafe(32)` and is never logged: a log line
holding a bearer credential is a credential store nobody decided to build.

### Rule 3 — the tenant is re-resolved on every call

By omission: there is no cache anywhere on the read path. `/libvox-mcp`
introspects on every single tool call and caches nothing, and Core answers from
the store every time. A long conversation cannot drift into another library's
scope, and a revoked credential stops working on the next call.

Any cache added here would directly weaken that, which is the reason there
isn't one on the hot path.

### Rule 5 — an id the library does not have never reaches its server

`check_availability` drops any `biblio_id` absent from that library's live
generation before opening a connection to the ILS. The tool description tells
the model to pass only ids a search returned; that is advice, and this is
enforcement. An id the model invented, or one a patron injected through the
conversation, is dropped rather than probed against a library's server.

Dropping is also the contract: a record the library does not hold is omitted,
never padded with an `unavailable` placeholder. "We do not have that" and "we
have it and it is out" are different answers.

## How a session is born

```
POST /internal/v1/sessions
     {"channel": "web", "binding_key": "wk_...", "origin": "https://opac.furg.br"}
```

The README's WebSocket handshake with the WebSocket removed, so the channel
gateway will wrap this rather than replace it. It invents nothing:
`resolve_channel_binding` is a `SECURITY DEFINER` function `/libvox-portal`
already created, owned by a role that cannot log in.

**Every refusal is the same answer.** An unknown key, a channel switched off, a
suspended library and a page not on the allowlist all come back as
`{"active": false}` — and the collapsing happens inside the database function,
so it cannot be undone by an edit here.

The widget key in an OPAC page identifies; it does not authenticate. What makes
the binding trustworthy is the Origin check.

> **Sessions live in memory, and that is a limitation rather than a design.**
> The architecture makes Core a read-only consumer of the Tenant DB —
> `/libvox-portal` is its one writer — so sessions cannot be persisted there
> without giving that database a second author. Redis is what the root README
> earmarks for the genuinely ephemeral and it is not deployed.
>
> The consequences, said out loud rather than discovered: **restarting Core
> logs everyone out**, and two Core processes behind a load balancer would each
> have their own idea of who is signed in. `SessionStore` is shaped as a store
> so that swapping in Redis is one class.

### Pointing another MCP client at the Core path

`./core.sh token <tenant>` prints one session bearer and nothing else, so it
composes:

```bash
TOKEN=$(./core.sh token lib-demofranca)

claude mcp add --transport http libvox-demofranca http://127.0.0.1:8000/mcp \
  --header "Authorization: Bearer ${TOKEN}"
```

That needs `/libvox-mcp` running in `streamable-http` — **stdio has no place to
put the credential**, so on stdio every caller is anonymous and every tool call
is refused for want of a tenant. The MCP layer's own README calls that correct
behaviour rather than a bug, and it is why `LIBVOX_MCP_BACKEND=koha` exists as
a separate, unauthenticated experiment.

Two things follow from the box above, and both bite here:

- **The token expires in an hour and a Core restart ends it.** This is a
  demonstration, not a setup.
- **The token *is* the library.** Nothing downstream accepts a tenant argument,
  so one entry points at exactly one catalog, and a second library means a
  second entry with a second token.

## The web channel

```
0.0.0.0:8092   /ws/v1/chat    Origin, and nothing else
               /widget.js
               /healthz
```

**A second application, not a second router.** The internal API takes
`tenant_id` from its request body, so anything that reaches it reads any
library — which is why `create_app` says it "must never be somewhere a browser
goes looking". Out here `/internal/v1` is not merely unrouted; it does not
exist. A misconfigured reverse proxy cannot undo that, and the failing test is
one line:

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8092/internal/v1/search_catalog   # 404
```

**The credential never reaches the browser.** The page sends a public widget
key and its `Origin`; the gateway resolves the binding through the same
`SECURITY DEFINER` function the mint route uses, mints a session, and holds the
token for the life of the socket. It cannot call `/internal/v1/sessions` to do
this — that route is behind the service token — which is what "the gateway
wraps the handshake" means once you try to write it.

### What it says

| Direction | Event | Carries |
| :--- | :--- | :--- |
| ← | `SessionReadyEvent` | `assistant_name`, `welcome_message`, `theme_color`, `locale` |
| → | `QueryReceived` | `text` |
| ← | `ToolExecuting` | the tool's **name**, and nothing else |
| ← | `ResponseGenerated` | `text` |
| ← | `ErrorEvent` | a sentence for the reader |

`ToolExecuting` is the honest typing indicator: rather than animating dots for
a duration it invented, the widget says a search is running — and an
availability check really is waiting on a library's server. It exists because
the loop grew an optional `on_tool` observer, which is an observer and not a
return value on purpose: it cannot change what runs, and a channel that raises
inside it must not cost an answer the library already paid a model to produce.

Arguments carry the reader's own words back and a result carries the catalogue;
a progress event needs neither.

### `Origin`, and what it does not do

It is imposed by the browser and stops another site from opening a session in a
reader's browser. It stops nothing else — `curl` sends whatever it likes. That
is acceptable because a session reaches one library's **public catalogue**,
which is why propagation rule 5 and `index_status` matter more here than this
gate does.

**Rate limiting is not implemented, and is named rather than assumed.** A
connection that asks a thousand questions costs a thousand model calls. The
root README already says this is the adapter's job on channels without an
Origin; it is no less true on one with it.

Every refused handshake closes with **1008**, whatever the reason — unknown
key, unregistered origin, channel off, suspended library. Telling them apart
lets someone learn which of those a given key is, and the collapsing happens
inside the database function rather than here, so it cannot be undone by an
edit to this module.

### The widget

One file, no build step, no dependency, served from the same origin as its own
socket:

```html
<script src="https://libvox.exemplo/widget.js" data-key="wk_..."></script>
```

The library does not write that line: `/libvox-portal`'s Assistant page
generates it with the library's own key. Nothing in the file is per-tenant —
name, colour and greeting arrive over the socket once the server has decided
whose key it is — which is why one static file serves every library.

Shadow DOM, because a Koha OPAC has twenty years of stylesheets and they will
fight. And `render(config)` is separable from `connect(url, key)`, which is
what lets the portal preview the real component from an unsaved form.

```bash
./demo/serve.py --tenant lib-demofranca          # or: ./core.sh widget lib-demofranca
./demo/serve.py --key wk_... --port 8093         # no database, any python3
```

Served over http rather than opened from `file://`: that sends `Origin: null`,
the binding is refused, and the refusal looks like a broken gateway instead of
the check working.

**Register the printed origin on the library's web channel**, in the portal
under *Canais*. It has to match exactly — scheme, host and port.
`http://127.0.0.1:8093` and `http://localhost:8093` are different origins to a
browser, and the second is refused if only the first is on the allowlist. Both
forms pass the database's format check, so either is fine as long as it is the
one the address bar shows.

The script says on startup whether the origin is already allowed, rather than
leaving a refused handshake and an empty browser console to interpret.

## Testing a library's connection, because the portal cannot

`/libvox-portal` asks this component whether a library's stored credentials
work. It cannot answer that itself: `IlsCredentialStore` has no read path, and
two tests hold that line — one of them tokenizes every PHP file looking for
`pgp_sym_decrypt`. Testing a stored credential means decrypting it, so the
question comes to the component the root README names as the one that may.

**It always answers 200.** A failed handshake is an answer, and saying *which*
failure is the entire point — the driver's taxonomy distinguishes unreachable,
rejected and unpermitted, which have three different fixes. A status code would
collapse them into "something went wrong" on the way through a client.

```
lib-local        ok    1 record
lib-demofranca   fail  unauthorized — Koha rejected the client credentials (HTTP 400)
lib-adminkuhn    ok    1 record
```

One outcome carries its own weight: **authenticated, and no records.** It reads
as success and is the failure this system is most careful about — it is how a
library ends up telling readers it holds no books, and it is almost always the
API user missing Koha's `catalogue` flag.

The detail goes back verbatim. It names the librarian's own server and the
status it returned; the rule that collapses such detail protects a patron
reading a chat window, not the person who owns the server.

> **The portal reaches this over a shared docker network, not a published
> port.** 8091 is bound to the host's loopback, which is the boundary — this
> surface takes `tenant_id` from its request body. Publishing it on the bridge
> to make one call possible would open it to every container on the host.

## Read-only on the Tenant DB

`/libvox-portal` is that database's one writer. Core reads configuration and
credentials and writes nothing, because a registry two components can author is
a registry they can disagree about.

The Discovery Index is the same: `/libvox-sync` writes it, Core searches it.

## The other two databases are reached through /libvox-sync

| What Core needs | Where it already exists |
| :--- | :--- |
| The search query, with ranking and `index_status` | `libvox_sync.search.search()` |
| Decrypting an ILS credential | `libvox_sync.credentials.fetch()` |
| Binding a transaction to one library | `libvox_sync.db.tenant_scope()` |
| Live availability | `libvox_driver_koha.KohaDriver` |

**The tension, since it is visible from the architecture diagram.** There is no
edge between Core and Sync there, and importing the ETL worker from Core looks
backwards. But `search.py` and `credentials.py` are library code rather than
worker code, and the alternative is a second definition of the same query —
which is exactly the duplication that had to be collapsed out of the Koha
driver. When a `libvox-common` exists, those two modules move into it, and it
is a file move.

## The internal API selects a tenant from its own request body

`tenant_id` arrives in the body of the two tool endpoints, authenticated only
by `x-libvox-service-token`. That is not a flaw in `/libvox-mcp` — the tenant
was already resolved there, from the caller's own credential — but it has a
consequence worth stating plainly:

> **Anything that can reach `/internal/v1` can read any library's data.**

Three defences, and none of them is a reverse proxy somebody remembered to
configure:

- Core **refuses to start** without `LIBVOX_CORE_SERVICE_TOKEN` when
  `LIBVOX_ENV` is anything but `development`.
- It binds `127.0.0.1` by default, not `0.0.0.0`.
- There are no interactive docs at `/docs`: this surface must never be
  somewhere a browser goes looking.

`introspect` is deliberately **not** behind the service token. It is the token
verifier's own path, it reveals nothing about a credential the caller does not
already hold, and gating it would turn a missing service token into "every
caller is anonymous" instead of a startup failure.

## Tests

```bash
cd ../libvox-db && ./db.sh && cd -   # all three test databases are on it
cd ../libvox-portal && ./vendor/bin/pest && cd -   # migrates libvox_tenant_test
pytest
```

The portal's suite comes first on a fresh server, and only there. It is what
gives `libvox_tenant_test` its schema: this conftest migrates the Discovery
Index and the conversation log, which are Core's to migrate, and deliberately
does not touch the Tenant DB, which is `/libvox-portal`'s. On a database that
has been through that once, `pytest` alone is enough.

Real PostgreSQL, skipped rather than failed when it is not up — the same
convention as the sibling modules. What is under test is row-level security, a
`SECURITY DEFINER` function and a generated tsvector, none of which have a
SQLite equivalent.

Tests use the `_test` databases, never the development ones.

## What this module keeps out

- **A write path to the Tenant DB.** One writer, and it is `/libvox-portal`.
- **A cache on the introspection path.** It is what makes revocation immediate
  and tenant drift impossible; a cache trades both for latency nobody has
  measured a need for.
- **A live ILS fallback when the index misses.** It would break the guarantee
  that search traffic never reaches the library's server, and a still-filling
  index is exactly when that fallback would generate the heaviest load.
- **A free-text instruction field on the persona.** The schema does not have
  one, and neither should the prompt builder. It is where an administrator
  would disable the policy layer without meaning to.
- **A conversation column that identifies a reader.** See above.
- **A tenant argument reachable from the model.** Tenant comes from the
  credential. Anything the model can fill is attacker-controllable through the
  patron's own words.
- **An error message that reaches the conversation.** A 403, an expired token,
  an unreachable Koha: the operator's log, never the model's context.
- **Interactive API docs.** See above.
