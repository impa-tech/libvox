# /deploy

Bringing the whole of LibVox up, in the order that works.

```bash
./deploy/up.sh           # pull, check, then start everything
./deploy/up.sh check     # only the checks; changes nothing, pulls nothing
./deploy/up.sh down      # stop every container, keeping every database

LIBVOX_PULL=0 ./deploy/up.sh     # deploy the tree as it stands
```

## It pulls first

`up` fetches before it deploys, because everything it does deploys the working
tree and the point of a deploy is the commit somebody pushed. `check` does not,
because it says it changes nothing; `down` does not, because stopping
yesterday's containers is what it is for.

**Fast-forward only, and never over local changes.** A deploy host that merges
is a host running a commit that exists nowhere else. Uncommitted changes stop
the deploy with the files named — commit them, `git stash` them, or say
`LIBVOX_PULL=0` and deploy what is there. The `.env` files never stand in the
way: they are gitignored, which is why they are checked separately at all.

And when the pull changes `up.sh` itself, the script re-executes the new one
before going any further. Bash reads a script by byte offset as it runs, so a
file rewritten underneath it leaves the interpreter reading the rest of a
*different* file from wherever it had got to.

## The order, and why it is one

```
libvox-db      ./db.sh                 everything connects to it, and it creates
                                       the libvox_internal network
libvox-portal  ./portal.sh migrate     schema before the service that needs it
libvox-sync    ./sync.sh               migrates the index and starts the worker
libvox-portal  ./portal.sh
libvox-core    ./core.sh migrate
libvox-core    ./core.sh
libvox-mcp     ./mcp.sh
```

The index is migrated **before the portal starts**, not after. The librarian's
import screen reads a column of `discovery_tenant`, so a portal built with that
query and started ahead of the migration serves a 500 for as long as the deploy
takes to reach `/libvox-sync`. What the worker needs from the portal is its
*database* — the outbox it listens on — which the step above creates, not the
portal container.

`down` is the same list backwards, and it stops containers without removing a
single volume: every library, its encrypted ILS credentials, every conversation
and every imported catalogue survive it. Deleting those is `./db.sh reset`,
which makes you type the word.

This used to be prose in the root README, and half of one week's failures were
order: 184 tests failing because the portal's suite had not run first, and an
incomplete schema because `migrate` ran inside an image built before the
migration existed. A sequence somebody has to remember is a sequence that
eventually runs out of order.

`up.sh` reimplements none of it. Every module already has a script that builds,
restarts, waits and names what it depends on, and this calls them with `set -e`
so a failed migration stops the deploy rather than leaving half a stack up.

**All three migrate paths build their image first**, which is what makes
"migrate, then start" safe: the migration runs on the code being deployed rather
than on whatever image was lying around. `/libvox-core` and `/libvox-sync`
learned that the same way, and `/libvox-portal` joined them for this.

## What it checks before touching a container

Secrets live outside git. That is a choice, and its cost is that the `.env`
files are the one part of the system a `git clone` does not reconstruct — so the
deploy verifies them rather than letting a container discover the gap.

**Keys you must supply.** A key declared in a `.env.example` **with no value** is
the repository saying *you supply this*; one with a value documents a default the
code also holds. That convention was already in the files, and it was checked
against reality before being relied on.

**The three that must agree across modules.** This is the part worth the most,
because these fail silently:

| | Between |
| :--- | :--- |
| `LIBVOX_CRYPTO_KEY` | portal, sync, core |
| `LIBVOX_CORE_SERVICE_TOKEN` | portal, core |
| `LIBVOX_MCP_SERVICE_TOKEN` = Core's service token | mcp, core |

A crypto key that merely *differs* starts everything perfectly and then fails
every import with a decryption error, hours later and one library at a time —
and the error talks about pgcrypto rather than about configuration. The MCP one
was documented in the root README as a shell line copying one into the other,
which demonstrates the relationship without checking it.

`worker.sh` keeps its own version of the first check, because it also runs on
its own.

## `versions.env`

```bash
LIBVOX_VERSION=dev
```

Inert today: nothing publishes an image, so `dev` is the only tag that exists and
every compose file builds it. All five `image:` lines read the variable, so when
a registry exists, changing what runs is this line — and no compose file changes
at all.

## Which ports are public, and which are not

Two of them are, and it is worth knowing which before this runs on a host with a
public address.

| Port | Bound to | Why |
| ---: | :--- | :--- |
| 8090 | `0.0.0.0` | The panels. Librarians and the operator sign in here. |
| 8092 | `0.0.0.0` | The widget and its WebSocket. A reader's browser has to reach it. |
| 8091 | `127.0.0.1` | Core's internal API. It takes `tenant_id` from its own request body, so anything that reaches it reads any library. |
| 8000 | `127.0.0.1` | The tool layer. It terminates a bearer token. |
| 5435 | `127.0.0.1` | The database. Published for host-side tooling only. |

The last three are a security boundary rather than a convention. `5435` was
unrestricted until the first deployment to a public host, which put PostgreSQL —
holding the library registry with its encrypted ILS credentials, and the
conversation log — on the internet behind the development passwords that are in
this repository.

**Over plain HTTP the two public ports carry credentials in the clear**: a
librarian's password on 8090, and a reader's question on 8092. A reverse proxy
terminating TLS in front of both is the first thing to add for anything but a
test. It is also what makes the widget usable on an HTTPS catalogue at all — a
browser refuses to load an HTTP script into an HTTPS page, and the widget then
does not appear with no error on the page to say why.

The generated embed snippet points at whatever address the librarian is reaching
the panel at, with Core's public port — so on `http://37.60.237.232:8090/app` it
offers `http://37.60.237.232:8092/widget.js`, with nothing to configure. Set
`LIBVOX_WIDGET_URL` in `libvox-portal/.env` when that guess is wrong: separate
hosts, or a proxy terminating TLS in front of one and not the other. An explicit
value always wins.

## TLS, on a host that has a name

`deploy/proxy/` is Caddy in front of the two surfaces that face people. It is a
compose project of its own and `up.sh` starts it **only when two hostnames are
configured**, which is the same evidence the widget URL is derived from: a host
meant to be reached by name wants a certificate, and a laptop would get a Caddy
failing an ACME challenge every few minutes.

**A certificate cannot be issued for a bare IP address.** Both names must already
resolve here; that is the prerequisite, not a detail.

```bash
cp deploy/host.env.example deploy/host.env
```
```
LIBVOX_PORTAL_DOMAIN=libvox.example.org
LIBVOX_WIDGET_DOMAIN=marcia.example.org
LIBVOX_BIND=127.0.0.1
```

Caddy reaches `portal:8080` and `core:8092` **by name over `libvox_internal`**,
never through the host's published ports — which is why `LIBVOX_BIND=127.0.0.1`
belongs with it. Left on `0.0.0.0`, the same panel stays reachable over plain
HTTP beside the one with TLS, and the certificate protects nothing anybody has
to use.

Everything above is absent on a laptop, and every default reproduces the
unproxied behaviour exactly.

Three things in the modules' own `.env` follow, and the first two break visibly
if they are missed:

```
libvox-portal/.env
  TRUSTED_PROXIES=*                                  # or Laravel emits http:// URLs
  APP_URL=https://libvox.example.org
  LIBVOX_WIDGET_URL=https://marcia.example.org       # the snippet's address
```

**The portal's container has no `.env`.** It is in `.dockerignore`, deliberately
— secrets do not belong in an image — so Laravel reads real environment
variables and nothing else. What `.env` does is give `docker compose` values to
interpolate into the `environment:` block, and **a variable absent from that
block does not reach the container at all.** All three above are in it; anything
added later has to be, and the symptom of forgetting is a setting that looks
applied in the file and has no effect.

`TRUSTED_PROXIES` is empty by default, which is exactly the behaviour before it
existed. Behind the proxy its absence is not subtle: TLS terminates at Caddy, so
Laravel sees plain HTTP and the redirect after a successful login sends the
browser back out of `https` — on the one request that just carried a password.
`*` is right here because binding to loopback leaves exactly one hop, and it is
ours.

`LIBVOX_WIDGET_URL` is needed again for the same reason: the Assistant page
derives the address from the request, and behind a proxy the scheme it reads is
the proxy's rather than the reader's.

Last, the **allowed origins** on each library's web channel become `https://…`.
Core validates the `Origin` header, which Caddy passes through untouched, so a
page still registered as `http://` is refused — correctly, and identically to
every other refusal, which means the reason is only in `./core.sh logs`.

## Proving a deploy worked

```bash
cd libvox-core && ./core.sh bench lib-local 4
```

`4/4` crosses all four images and all three databases in one command: the
registry came from `libvox_tenant`, the search from `libvox_index`, availability
went to the ILS through the driver, and the conversations were written to
`libvox_conversations`.

## What this is not

It is a deploy script, not GitOps. There is no CI, no registry and nothing
reconciling: a person runs it, and `versions.env` is a hook that does not pull
yet. What it does deliver — the right order, and secrets verified — is worth the
same without any of that, and is a prerequisite for all of it.
