# A page pretending to be an OPAC

`opac.html` is a library catalogue page that does not exist, with the real
widget in it. It is here to answer one question before a library is involved:
**does a reader on someone else's page actually get an answer?**

```bash
./demo/serve.py --tenant lib-demofranca
```

Then open <http://127.0.0.1:8093> and click the button in the corner.

---

## Why a server, and not just opening the file

Because `file://` sends `Origin: null`.

The gateway refuses a handshake whose origin the library did not register —
that is what makes a *public* widget key trustworthy. Opened from disk, the
page would be refused, and the refusal would look like a broken widget instead
of the check doing exactly its job.

So the page is served over http, which gives it a real origin. That origin is
the thing you register.

## What has to be running

| | | |
| :--- | :--- | :--- |
| Core | `cd libvox-core && ./core.sh up` | internal `8091`, public `8092` |
| The MCP layer | see below | `8000`, in HTTP mode |
| A library | with a **web channel** in the portal | |

The MCP layer runs over `streamable-http`, which is what its container serves:
authorization rides HTTP headers, and on stdio every caller is anonymous and
every tool call is refused for want of a tenant.

```bash
cd libvox-mcp && ./mcp.sh
```

It checks that its service token matches Core's before starting, which is the
one mismatch that produces a server answering the handshake and refusing every
tool call afterwards.

## Registering the origin

`serve.py` prints the origin it serves from and says whether the library
already allows it:

```
  http://127.0.0.1:8093/

  widget   http://127.0.0.1:8092/widget.js
  key      wk_f4mXL8eqbdJ2vtAhpTmSfuOtgzwltKsU
  library  lib-demofranca

  Add this origin to the library's web channel,
  or the handshake is refused:
      http://127.0.0.1:8093
  http://localhost:8090/app -> Canais -> Origens permitidas
  It allows: ['https://catalogue-v2511-koha.demo.biblibre.eu']
```

Add that string under **Canais → Origens permitidas**, and it changes to *"This
origin is already allowed. The widget will connect."*

> **It matches exactly, as a string.** `http://127.0.0.1:8093` and
> `http://localhost:8093` are different origins to a browser. Register the one
> your address bar shows — registering the other and opening this one is a
> refused connection that looks like a bug.

## What you should see

Ask *"vocês têm alguma coisa sobre pokemon?"* and the panel shows, in order:

1. the library's own greeting, in its own colour — that is `SessionReadyEvent`,
   and it arrives only after the server has decided whose key this is;
2. **consultando o catálogo…** — `ToolExecuting`, which is a real search
   running and not an animation timed to look like one;
3. the answer.

Ask *"o segundo está disponível?"* next. It should go straight to
**vendo se está na estante…** without searching again: the conversation is
stored, so the second question knows which book the first one was about.

## Options

```
--tenant NAME      look the widget key up for this library
--key wk_...       use this key, without touching a database
--name NAME        her name on the launcher button
--color #rrggbb    the accent, on the button before a session opens
--port N           default 8093
--widget-url URL   where Core serves widget.js (default http://127.0.0.1:8092)
```

`--tenant` looks the key up by running `psql` inside `/libvox-db`'s container,
so it needs that server running and nothing else — no DSN in this script, and no
second copy of the credentials. It reads her name and the library's accent from
the same query, so the page this serves is the page the portal would paste.

`--name` and `--color` are only for the launcher button, and only until a
session opens. The button is on screen before any socket is — and the socket
opens *when somebody clicks the button*, so it cannot wait for the session and
cannot be hidden until it either. Given explicitly they win over the lookup,
which is how to see what a rename or a rebrand would look like before
committing to it in the panel. With `--key` and neither flag they are `MARC.IA`
and `#4f46e5`, the same two defaults the widget itself falls back to.

`--key` needs no database and no Docker at all, which is what makes this
runnable against a Core someone else is hosting.

`./core.sh widget lib-demofranca` is the same thing with the defaults filled
in.

## When it does not work

**The button appears and nothing happens after clicking.** The handshake was
refused. Nine times in ten it is the origin — check the line `serve.py` printed
against what the address bar says. The browser console shows a failed
WebSocket and no reason, deliberately: every refusal looks identical from
outside, so that nobody can learn which of *unknown key*, *unregistered
origin*, *channel switched off* or *suspended library* a given key is. The
reason is in Core's log.

```bash
cd libvox-core && ./core.sh logs
```

**No button at all.** `widget.js` did not load. Check
<http://127.0.0.1:8092/widget.js> answers, which means Core's public surface is
up.

**"Não consegui responder agora."** The gateway reached the agent and the agent
could not finish — usually no model configured, or the MCP layer not running.
The reader is told a sentence and never a status code; the actual reason is in
Core's log.

## How a library actually installs it

The portal's **Assistente** page generates the `<script>` line with that
library's own key, next to a live preview of the widget — a librarian copies one
line. This folder exists to test the channel without a portal, a browser
extension, or a real OPAC in the way.
