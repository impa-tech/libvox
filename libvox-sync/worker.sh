#!/usr/bin/env bash
#
# Start the import worker, after checking the things that have actually gone
# wrong.
#
#   ./worker.sh              check, build, start, and say what is waiting
#   ./worker.sh logs         follow it
#   ./worker.sh stop
#   ./worker.sh check        the checks alone, changing nothing
#
# Every check here is a failure that happened, not one that was imagined:
#
#   * The container restart-looped on "LIBVOX_CRYPTO_KEY is not set", because
#     compose reads that from .env and this module had none. It worked when it
#     was first brought up only because the key happened to be exported in that
#     shell.
#   * A key that differs from /libvox-portal's decrypts nothing, and says so
#     one library at a time, hours later.
#   * The worker ran an import with a stale image, from before the pacing fix.
#     `up -d` alone does not rebuild.
#   * A request sat unconsumed because nothing was listening at all.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PORTAL_ENV="../libvox-portal/.env"
DB_COMPOSE="../libvox-db/docker-compose.yml"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

key_in() { grep -E "^LIBVOX_CRYPTO_KEY=.+" "$1" 2>/dev/null | head -1 | cut -d= -f2-; }

# --- the checks --------------------------------------------------------------

check_key() {
    local mine theirs
    mine=$(key_in .env || true)
    theirs=$(key_in "$PORTAL_ENV" || true)

    if [ -z "$mine" ]; then
        [ -n "$theirs" ] || die \
"No LIBVOX_CRYPTO_KEY, here or in /libvox-portal.

It decrypts the ILS credentials the portal stored, so the worker cannot do the
one thing it exists for without it. Configure a library in the portal first --
that is what generates a key worth copying."

        warn "no key here; copying /libvox-portal's into .env"
        [ -f .env ] || cp .env.example .env
        if grep -qE "^LIBVOX_CRYPTO_KEY=" .env; then
            sed -i "s|^LIBVOX_CRYPTO_KEY=.*|LIBVOX_CRYPTO_KEY=${theirs}|" .env
        else
            printf '\nLIBVOX_CRYPTO_KEY=%s\n' "$theirs" >> .env
        fi
        mine="$theirs"
    fi

    # The silent one. A key that merely *exists* gets the worker running and
    # then fails every import with a decryption error, one library at a time,
    # hours later. Credentials stored under one key cannot be read back with
    # another.
    if [ -n "$theirs" ] && [ "$mine" != "$theirs" ]; then
        die \
"The key here is not /libvox-portal's.

Credentials stored under one key cannot be read back with another, so every
import would fail to decrypt -- and the worker would start perfectly well
before doing it.

  grep '^LIBVOX_CRYPTO_KEY=' ${PORTAL_ENV}"
    fi

    ok "crypto key present, and identical to the portal's"
}

# One check where there were two. Both databases this worker needs -- the index
# it writes and the tenant registry whose outbox it listens on -- are on the one
# server in /libvox-db, so a single container being down explains both.
check_databases() {
    docker compose -f "$DB_COMPOSE" ps postgres --status running --quiet \
        | grep -q . || die \
"The database is not running. The index this worker writes and the outbox it
listens on are both on it.
  cd ../libvox-db && ./db.sh"
    ok "database up (5435)"
}

check_schema() {
    local has_index has_outbox
    has_index=$(docker compose -f "$DB_COMPOSE" exec -T postgres psql -U libvox -d libvox_index -tAc \
        "SELECT to_regclass('discovery_biblio') IS NOT NULL" 2>/dev/null | tr -d '\r')
    [ "$has_index" = "t" ] || die \
"The Discovery Index has no schema yet.
  docker compose run --rm sync migrate"
    ok "Discovery Index migrated"

    # The request the button raises. Its absence means /libvox-portal has not
    # run the migration that added SyncRequested, and every press would be
    # refused by a CHECK constraint the worker never sees.
    has_outbox=$(docker compose -f "$DB_COMPOSE" exec -T postgres \
        psql -U libvox -d libvox_tenant -tAc \
        "SELECT count(*) FROM pg_constraint
          WHERE conname = 'tenant_events_event_check'
            AND pg_get_constraintdef(oid) LIKE '%SyncRequested%'" 2>/dev/null | tr -d '\r')
    [ "$has_outbox" = "1" ] || die \
"The Tenant DB does not know about SyncRequested, so the button cannot raise one.
  cd ../libvox-portal && php artisan migrate --database=pgsql_admin"
    ok "outbox accepts SyncRequested"
}

show_state() {
    say 'Waiting to be imported'
    docker compose -f "$DB_COMPOSE" exec -T postgres psql -U libvox -d libvox_tenant -tAc \
        "SELECT '  ' || tenant_id || '  pedido ' || to_char(occurred_at,'HH24:MI:SS')
           FROM tenant_events
          WHERE event = 'SyncRequested' AND consumed_at IS NULL
          ORDER BY id" 2>/dev/null | tr -d '\r' | grep . || echo '  (nothing pending)'

    say 'What the index holds'
    docker compose -f "$DB_COMPOSE" exec -T postgres psql -U libvox -d libvox_index -tAc \
        "SELECT '  ' || rpad(t.tenant_id, 16) || rpad(t.index_status, 10) ||
                coalesce('generation ' || t.active_generation, 'never built') ||
                ', ' || count(b.biblio_id) || ' records' ||
                coalesce(' — ' || left(t.last_error, 48), '')
           FROM discovery_tenant t
           LEFT JOIN discovery_biblio b
             ON b.tenant_id = t.tenant_id AND b.generation = t.active_generation
          GROUP BY t.tenant_id, t.index_status, t.active_generation, t.last_error
          ORDER BY 1" 2>/dev/null | tr -d '\r' | grep . || echo '  (no library indexed yet)'
    echo
}

# --- commands ----------------------------------------------------------------

cmd_check() {
    say 'Checking'
    check_key
    check_databases
    check_schema
}

cmd_up() {
    cmd_check

    # --build every time, not because it is cheap but because the alternative
    # already happened: the worker ran a whole import on an image from before
    # the fix that made it pace itself, and nothing said so.
    say 'Building and starting'
    docker compose --profile worker up -d --build worker

    # Long enough for a crash loop to have shown itself.
    sleep 4
    if ! docker compose ps worker --status running --quiet | grep -q .; then
        printf '\n\033[31mIt did not stay up:\033[0m\n' >&2
        docker compose logs --tail 15 worker >&2
        exit 1
    fi
    ok 'worker running'

    show_state
    printf '  ./worker.sh logs   follows it\n\n'
}

case "${1:-up}" in
    up|start) cmd_up ;;
    check)    cmd_check; show_state ;;
    logs)     docker compose --profile worker logs -f worker ;;
    stop)     docker compose --profile worker stop worker ;;
    *)        sed -n '3,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
