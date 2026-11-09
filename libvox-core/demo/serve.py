#!/usr/bin/env python3
"""Serve `opac.html` on 127.0.0.1, as a page pretending to be a library's OPAC.

    ./demo/serve.py --tenant lib-demofranca
    ./demo/serve.py --key wk_... --port 8093

Why a server rather than opening the file:

**`file://` sends `Origin: null`.** The gateway would refuse the handshake, and
the refusal would look like a broken widget instead of the Origin check doing
exactly its job. A page served over http has a real origin, which is the thing
a library registers.

The origin to register is printed on startup, and it has to match **exactly** —
scheme, host and port. `http://127.0.0.1:8093` and `http://localhost:8093` are
different origins to a browser, and the second will be refused if only the
first is on the allowlist.

Standard library only, so this runs with any python3 and needs none of Core's
environment. `--tenant` is the one exception: looking a key up means asking the
Tenant DB, which it does through /libvox-db's compose file, the same way
`core.sh` does.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from functools import partial
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
PAGE = HERE / "opac.html"
COMPOSE = HERE.parent.parent / "libvox-db" / "docker-compose.yml"

DEFAULT_PORT = 8093
DEFAULT_WIDGET_URL = "http://127.0.0.1:8092"


# Not a space, because two of the three fields can contain one: an allowed
# origins list is JSON with a space after each comma, and an assistant can be
# called "Bibliotecária Ana". ASCII unit separator appears in neither.
FIELD_SEP = "\x1f"

DEFAULT_NAME = "MARC.IA"
DEFAULT_ACCENT = "#4f46e5"


def look_up(tenant_id: str) -> tuple[str, list[str], str, str]:
    """This library's web binding key, the origins it allows, her name and colour.

    Through /libvox-db's postgres container rather than a DSN in this file: the
    credentials live in that compose file, and a demo script is the last place a
    second copy of them should appear.

    The name and the colour come from here rather than from a flag so that the
    page this serves is the page the portal would paste. A library that
    rebranded and then ran the demo with the hardcoded defaults would be
    looking at a bug that is only in the demo.
    """
    query = (
        "SELECT b.binding_key, b.allowed_origins::text, "
        f"       coalesce(nullif(w.assistant_name, ''), '{DEFAULT_NAME}'), "
        # No `nullif` on the colour, unlike the name: the column is NOT NULL
        # with a CHECK for `#rrggbb`, so the only way it is absent is a library
        # with no widget row at all, which the LEFT JOIN turns into NULL.
        f"       coalesce(w.theme_color, '{DEFAULT_ACCENT}') "
        "  FROM channel_bindings b "
        "  LEFT JOIN tenant_widget w ON w.tenant_id = b.tenant_id "
        f" WHERE b.tenant_id = '{tenant_id}' AND b.channel = 'web'"
    )
    try:
        out = subprocess.run(
            [
                "docker", "compose", "-f", str(COMPOSE), "exec", "-T", "postgres",
                "psql", "-U", "libvox", "-d", "libvox_tenant",
                "-tA", "-F", FIELD_SEP, "-c", query,
            ],
            capture_output=True,
            text=True,
            timeout=30,
            check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError):
        sys.exit(
            f"could not reach the Tenant DB to look up {tenant_id}.\n"
            "  Is the database up?  cd libvox-db && ./db.sh\n"
            "  Or pass the key directly:  --key wk_..."
        )

    if not out:
        sys.exit(
            f"{tenant_id} has no web channel — or does not exist.\n"
            "  Register it at http://localhost:8090/admin/tenants"
        )

    key, origins, name, color = (out.split(FIELD_SEP) + ["", "", ""])[:4]
    try:
        allowed = json.loads(origins)
    except json.JSONDecodeError:
        allowed = []
    return key, allowed, name or DEFAULT_NAME, color or DEFAULT_ACCENT


def build(key: str, widget_url: str, name: str, color: str) -> bytes:
    """The page, with this library's key in it.

    Every placeholder is checked before being replaced, because `str.replace`
    that matches nothing is silent -- and this failed exactly that way: the
    template was once committed with a real key already substituted into it, so
    every `--tenant` served that one library's key and the widget cheerfully
    connected to the wrong catalogue. The log said `lib-demofranca` while the
    command said `lib-local`, and nothing in between complained.
    """
    page = PAGE.read_text(encoding="utf-8")

    for placeholder in ("__WIDGET_URL__", "__KEY__", "__NAME__", "__COLOR__"):
        if placeholder not in page:
            sys.exit(
                f"{PAGE} has no {placeholder} in it, so this would serve whatever "
                "key is written in the file rather than the one you asked for.\n"
                "  Restore the template:  git checkout -- demo/opac.html"
            )

    return (
        page.replace("__WIDGET_URL__", widget_url.rstrip("/") + "/widget.js")
        .replace("__KEY__", key)
        # Both are read by the launcher, and only until a session opens -- the
        # button is on screen before any socket is, and cannot wait for one:
        # the socket opens when somebody clicks it. See widget.js.
        .replace("__NAME__", name)
        .replace("__COLOR__", color)
    ).encode("utf-8")


class Page(BaseHTTPRequestHandler):
    """One page, at `/`, built in memory.

    No temp directory and no rewritten copy on disk: a stale file left behind
    from a previous run is a widget pointing at a key that has since changed,
    and the failure is a refused handshake with no clue why.
    """

    server_version = "libvox-demo"

    def __init__(self, *args, html: bytes, **kwargs) -> None:
        self._html = html
        super().__init__(*args, **kwargs)

    def do_GET(self) -> None:
        if self.path in ("/", "/index.html"):
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(self._html)))
            # A demo page that caches is a demo page still showing yesterday's
            # key after you changed it.
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(self._html)
            return

        self.send_response(404)
        self.end_headers()

    def log_message(self, fmt: str, *args) -> None:
        # Quiet: the interesting output is the widget's, in the browser.
        pass


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Serve a fake OPAC with one library's widget in it.",
    )
    parser.add_argument("--tenant", help="Look the widget key up for this library.")
    parser.add_argument("--key", help="Use this widget key, without a lookup.")
    parser.add_argument(
        "--name",
        help=(
            "Her name on the launcher button. Looked up with --tenant; "
            f"defaults to {DEFAULT_NAME} when only --key is given."
        ),
    )
    parser.add_argument(
        "--color",
        help=(
            "The accent, on the button before a session opens. Looked up with "
            f"--tenant; defaults to {DEFAULT_ACCENT} when only --key is given."
        ),
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--widget-url",
        default=DEFAULT_WIDGET_URL,
        help=f"Where /libvox-core serves widget.js (default: {DEFAULT_WIDGET_URL}).",
    )
    args = parser.parse_args()

    if not args.key and not args.tenant:
        parser.error("one of --tenant or --key is required")

    allowed: list[str] = []
    key = args.key
    name = args.name or DEFAULT_NAME
    color = args.color or DEFAULT_ACCENT
    if not key:
        key, allowed, found_name, found_color = look_up(args.tenant)
        # An explicit flag still wins, so the demo can show what a rename or a
        # rebrand would look like before anyone commits to it in the panel.
        name = args.name or found_name
        color = args.color or found_color

    origin = f"http://127.0.0.1:{args.port}"
    html = build(key, args.widget_url, name, color)

    # Bound before a word is printed. The banner used to come first, so a port
    # already in use produced the whole friendly message -- "the widget will
    # connect" included -- and only then a traceback, with an older server
    # still answering on that port.
    try:
        httpd = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Page, html=html))
    except OSError as error:
        sys.exit(
            f"cannot listen on {origin}: {error}\n"
            "  Something is already there -- another serve.py, most likely.\n"
            "  Stop it, or pass --port with a free one."
        )

    print(f"\n  {origin}/\n")
    print(f"  widget   {args.widget_url}/widget.js")
    print(f"  key      {key}")
    print(f"  name     {name}")
    print(f"  colour   {color}")

    if args.tenant:
        print(f"  library  {args.tenant}")
        if origin in allowed:
            print("\n  This origin is already allowed. The widget will connect.\n")
        else:
            # Said before the browser refuses, with the fix attached. An
            # unregistered origin is a closed socket and an empty console line.
            print("\n  \033[33mAdd this origin to the library's web channel,\033[0m")
            print("  or the handshake is refused:")
            print(f"      {origin}")
            # Named as the librarian sees it in the panel, not as the column
            # is spelled in the schema.
            print("  http://localhost:8090/app -> Canais -> Origens permitidas")
            print(f"  It allows: {allowed or '(none)'}\n")
    else:
        print(f"\n  Make sure {origin} is one of the library's allowed origins.\n")

    # Flushed explicitly: a server prints before it blocks, and stdout is
    # fully buffered when this is piped or logged rather than watched in a
    # terminal -- so the address you need would appear only on shutdown.
    print("  Ctrl-C to stop.\n", flush=True)

    with httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
