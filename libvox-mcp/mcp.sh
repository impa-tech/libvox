#!/usr/bin/env bash
#
# Start the tool layer, after checking the thing that fails silently.
#
#   ./mcp.sh              check, build, start, and prove it answers
#   ./mcp.sh logs         follow it
#   ./mcp.sh down         stop it
#   ./mcp.sh check        the checks alone, changing nothing
#
# **The check worth having is the token.** A value that merely exists gets this
# server running, listening, and answering the handshake — and then returns 401
# on every tool call, which reaches a reader as "não consegui consultar o
# catálogo" and reaches the operator's log as an unhelpful "could not reach the
# tool layer". Nothing crashes. Nothing restarts. It just never works.
#
# The rest is what a container in a shared network can get wrong: the network
# not existing yet, Core not being on it, and the image being stale.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

CORE_ENV="../libvox-core/.env"
CORE_COMPOSE="../libvox-core/docker-compose.yml"
NETWORK="libvox_internal"
PORT=8000

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# Read, never echoed. A token printed to a terminal is a token in a scrollback
# buffer, and this one authenticates every call Core will honour.
value_of() { grep -E "^$2=.+" "$1" 2>/dev/null | head -1 | cut -d= -f2-; }

# --- the checks --------------------------------------------------------------

check_token() {
    local mine theirs
    mine=$(value_of .env LIBVOX_MCP_SERVICE_TOKEN || true)
    theirs=$(value_of "$CORE_ENV" LIBVOX_CORE_SERVICE_TOKEN || true)

    if [ -z "$mine" ]; then
        [ -n "$theirs" ] || die \
"No service token, here or in /libvox-core.

Core demands one on every internal call and this is the value presented, so
without it every tool call is refused. Generate one, put it in both files under
its own name, and restart Core:

  openssl rand -hex 32"

        warn 'no token here; copying /libvox-core’s into .env'
        [ -f .env ] || cp .env.example .env
        if grep -qE "^LIBVOX_MCP_SERVICE_TOKEN=" .env; then
            sed -i "s|^LIBVOX_MCP_SERVICE_TOKEN=.*|LIBVOX_MCP_SERVICE_TOKEN=${theirs}|" .env
        else
            printf '\nLIBVOX_MCP_SERVICE_TOKEN=%s\n' "$theirs" >> .env
        fi
        mine="$theirs"
    fi

    # The silent one, and the reason this script exists rather than a line in
    # the README. Two different tokens produce a server that starts perfectly.
    if [ -n "$theirs" ] && [ "$mine" != "$theirs" ]; then
        die \
"The token here is not /libvox-core’s.

Core would refuse every tool call with a 401, and this server would start,
listen and answer the handshake regardless. A reader sees the catalogue being
unavailable; the log says only that the tool layer could not be reached.

  grep '^LIBVOX_CORE_SERVICE_TOKEN=' ${CORE_ENV}"
    fi

    [ -n "$theirs" ] || warn 'Core has no token of its own; nothing will be demanded'
    ok 'service token present, and identical to Core’s'
}

check_network() {
    # `external: true` in the compose file: /libvox-core creates this network,
    # and compose fails with a bare "network not found" that names neither who
    # owns it nor how to get one.
    docker network inspect "$NETWORK" >/dev/null 2>&1 || die \
"The ${NETWORK} network does not exist.

/libvox-core creates it, and this server joins it to reach Core without going
through the host. Start Core once and it will be there:

  cd ../libvox-core && ./core.sh"
    ok "${NETWORK} network exists"
}

check_core() {
    docker compose -f "$CORE_COMPOSE" ps core --status running --quiet 2>/dev/null \
        | grep -q . || die \
"/libvox-core is not running, and every tool here is implemented over there.

This server would start and answer the handshake, then fail every call.

  cd ../libvox-core && ./core.sh"
    ok 'Core running'
}

# --- commands ----------------------------------------------------------------

cmd_check() {
    say 'Checking'
    check_token
    check_network
    check_core
}

# Proves the path this design is new at: container to container over
# libvox_internal, rather than out to the host and back. `core` is a service
# name that only resolves on that network, and 8091 is a port bound to the
# host's loopback -- so this succeeding is the whole claim.
prove_path_to_core() {
    local code
    code=$(docker compose exec -T mcp python -c "
import urllib.request
try:
    with urllib.request.urlopen('http://core:8091/healthz', timeout=5) as r:
        print(r.status)
except Exception as e:
    print(type(e).__name__)
" 2>/dev/null | tr -d '\r' | tail -1)

    [ "$code" = "200" ] && ok 'reaches Core at core:8091, over the shared network' \
                        || warn "could not reach core:8091 from the container (${code:-no answer})"
}

cmd_up() {
    cmd_check

    # --build every time, for the reason worker.sh gives: `up -d` alone reuses a
    # container that is already running, so a rebuilt image sits there while the
    # old code keeps serving.
    say 'Building and starting'
    docker compose up -d --build

    sleep 3
    if ! docker compose ps mcp --status running --quiet | grep -q .; then
        printf '\n\033[31mIt did not stay up:\033[0m\n' >&2
        docker compose logs --tail 20 mcp >&2
        exit 1
    fi
    ok 'container running'

    # 401 is the right answer to an unauthenticated request, and the only cheap
    # proof that it is serving rather than merely alive: it got far enough to
    # demand a bearer.
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' --max-time 8 "http://127.0.0.1:${PORT}/mcp" || true)
    case "$code" in
        401) ok "answering on ${PORT}, and demanding a bearer" ;;
        000) die "Nothing answered on ${PORT}. ./mcp.sh logs" ;;
        *)   warn "answered ${code} on ${PORT}, expected 401 — an unauthenticated call should be refused" ;;
    esac

    prove_path_to_core

    printf '\n  ./mcp.sh logs   follows it\n\n'
}

case "${1:-up}" in
    up|start) cmd_up ;;
    check)    cmd_check ;;
    logs)     docker compose logs -f mcp ;;
    down|stop) docker compose down ;;
    *)        sed -n '3,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
