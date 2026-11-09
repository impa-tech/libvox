#!/usr/bin/env bash
#
# Open an agent against ONE library, with credentials from the Tenant DB.
#
#   ./tools/libvox-agent.sh lib-adminkuhn
#   ./tools/libvox-agent.sh lib-local run "is 'The C programming language' in?"
#
# Why this exists: /libvox-portal stores each library's ILS credentials
# encrypted, and /libvox-mcp must never read them -- "decrypted ILS
# credentials" is on that module's list of things it must never grow, because
# in the finished architecture /libvox-core injects them into driver calls and
# the MCP layer never sees one. So the credential comes out through
# /libvox-sync, which already has the decryption path, and reaches the agent
# the only way it should: as environment, in the shell that launches it.
#
# The secret never touches disk. It leaves the database as ciphertext, is
# decrypted in memory, and is eval'd straight into this shell's environment --
# no temp file to forget, no shell history entry.
#
# OPENWEBUI_API_KEY still has to be in your environment. That one is not in the
# database and does not belong to a library.

# depois de rodar o comando 
# cp opencode.jsonc ~/.config/opencode/opencode.jsonc
# export OPENWEBUI_API_KEY="sk-..."
# ./tools/libvox-agent.sh lib-local

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

TENANT="${1:-}"
if [ -z "$TENANT" ]; then
    echo "usage: ${0##*/} <tenant-id> [opencode args...]" >&2
    echo "       ${0##*/} lib-adminkuhn" >&2
    echo >&2
    echo "Configured libraries:" >&2
    # Order matters: stdout to stderr FIRST, then silence psql's own stderr.
    # The other way round points stdout at the /dev/null stderr already is.
    docker compose -f libvox-db/docker-compose.yml exec -T postgres \
        psql -U libvox -d libvox_tenant -tAc \
        "SELECT '  ' || tenant_id FROM tenants WHERE ils_configured_at IS NOT NULL ORDER BY 1" \
        >&2 2>/dev/null || echo "  (could not reach the Tenant DB)" >&2
    exit 64
fi
shift

PYTHON="${LIBVOX_SYNC_PYTHON:-$HOME/miniconda3/envs/libvox-sync/bin/python}"
[ -x "$PYTHON" ] || {
    echo "No Python at $PYTHON. Set LIBVOX_SYNC_PYTHON, or create the" >&2
    echo "environment: cd libvox-sync && conda env create -f environment.yml" >&2
    exit 1
}

# The same key the portal encrypted with. Reading its .env works only because
# both modules sit in one checkout; in a deployment each takes the key from a
# secrets manager independently.
if [ -z "${LIBVOX_CRYPTO_KEY:-}" ]; then
    [ -f libvox-portal/.env ] || {
        echo "No libvox-portal/.env, and LIBVOX_CRYPTO_KEY is not set." >&2
        echo "Without the key the stored credential cannot be decrypted." >&2
        exit 1
    }
    LIBVOX_CRYPTO_KEY="$(grep '^LIBVOX_CRYPTO_KEY=' libvox-portal/.env | cut -d= -f2-)"
    export LIBVOX_CRYPTO_KEY
fi

# Captured before it is evaluated, so a failure stops here. `eval "$(...)"`
# takes the exit status of eval -- which is 0 for empty input -- and the script
# would carry on to fail later, somewhere that says nothing about why.
CREDENTIALS="$(cd libvox-sync && "$PYTHON" "$OLDPWD/tools/_ils_env.py" "$TENANT")" || exit 1

eval "$CREDENTIALS"
unset CREDENTIALS

# The MCP server inherits this environment, which is the whole reason
# opencode.jsonc carries no `environment` block. Without this it defaults to
# backend='core' and tries to reach a component that does not exist yet.
export LIBVOX_MCP_BACKEND=koha

if [ -z "${OPENWEBUI_API_KEY:-}" ]; then
    echo "warning: OPENWEBUI_API_KEY is not set; opencode will not reach a model." >&2
fi

echo "library: ${TENANT}  ->  ${KOHA_URL}" >&2
exec opencode "$@"
