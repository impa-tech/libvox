#!/usr/bin/env bash
#
# Point Claude Code at ONE library's catalog, through the real MCP layer.
#
#   ./tools/claude-mcp.sh lib-demofranca
#   ./tools/claude-mcp.sh lib-local
#
# Replaces the entry each time rather than adding a second one, because a
# session token *is* a library: nothing downstream accepts a tenant argument,
# so two entries would be two catalogs with no way to tell which answered.
# Re-running with another id is how you switch.
#
# This is the authenticated path -- Core, the Discovery Index, tenant
# isolation, the propagation rules. Not to be confused with the direct-Koha
# experiment, which is stdio, has no tenant, and searches titles only:
#
#     search "pokemon" against demo-admin  ->  direct 0, indexed 20
#
# WHY IT CANNOT BE STDIO
#
# Authorization exists only over HTTP. On stdio every caller is anonymous and
# every tool call is refused for want of a tenant -- correct behaviour, says
# the MCP layer's README, not a bug. The bearer needs a header to travel in.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

NAME="${LIBVOX_MCP_NAME:-libvox}"
MCP_URL="${LIBVOX_MCP_URL:-http://127.0.0.1:8000/mcp}"
CORE_URL="${LIBVOX_CORE_URL:-http://127.0.0.1:8091}"

die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

TENANT="${1:-}"
if [ -z "$TENANT" ]; then
    echo "usage: ${0##*/} <tenant-id>" >&2
    echo >&2
    echo "Libraries with a web channel, which is what a session is minted from:" >&2
    docker compose -f libvox-db/docker-compose.yml exec -T postgres \
        psql -U libvox -d libvox_tenant -tAc \
        "SELECT '  ' || t.tenant_id || '  (' || t.status || ')'
           FROM tenants t JOIN channel_bindings b USING (tenant_id)
          WHERE b.channel = 'web' ORDER BY 1" \
        >&2 2>/dev/null || echo "  (could not reach the Tenant DB)" >&2
    exit 64
fi

command -v claude >/dev/null || die "no \`claude\` on PATH."

# Both halves are checked before anything is changed: adding an entry that
# cannot connect leaves Claude with a broken server and no hint which of the
# two processes is down.
curl -fsS -o /dev/null --max-time 5 "${CORE_URL}/healthz" 2>/dev/null \
    || die "Core is not answering on ${CORE_URL}.
  cd libvox-core && ./core.sh up"

# 405 is the right answer here: the endpoint exists and refuses GET. A refused
# connection is what "not running" looks like, and curl says so differently.
curl -fsS -o /dev/null --max-time 5 "$MCP_URL" 2>/dev/null || {
    curl -s -o /dev/null --max-time 5 "$MCP_URL" 2>/dev/null || die \
"The MCP layer is not answering on ${MCP_URL}.

  cd libvox-mcp && LIBVOX_MCP_TRANSPORT=streamable-http \\
      LIBVOX_MCP_BACKEND=core LIBVOX_CORE_URL=${CORE_URL} \\
      python -m libvox_mcp"
}

# core.sh owns the binding lookup and the mint, including the two different
# refusals -- a library that does not exist, and one with no web channel.
TOKEN="$(cd libvox-core && ./core.sh token "$TENANT")" || exit 1
[ -n "$TOKEN" ] || die "no session token came back for ${TENANT}."

# Absent is the normal case on a first run, so its failure is not one.
claude mcp remove "$NAME" >/dev/null 2>&1 || true

claude mcp add --transport http "$NAME" "$MCP_URL" \
    --header "Authorization: Bearer ${TOKEN}" >/dev/null \
    || die "claude refused to add the server."

printf '\n\033[1m%s\033[0m is now %s\n' "$NAME" "$TENANT"
printf '  %s  ->  search_catalog, check_availability\n\n' "$MCP_URL"
cat <<'NOTE'
  The session expires in an hour, and restarting Core ends it early -- sessions
  live in Core's memory. Re-run this when tool calls start coming back refused.

  Start a new Claude Code session, or /mcp in a running one, to pick it up.
NOTE
