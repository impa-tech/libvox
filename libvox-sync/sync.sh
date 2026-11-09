#!/usr/bin/env bash
#
# Bring up everything /libvox-sync needs, and the worker with it.
#
#   ./sync.sh              start the database, migrate the index, run the worker
#   ./sync.sh down         stop this module's services; the index keeps its data
#   ./sync.sh logs         follow the worker
#   ./sync.sh status       what is up and what the index holds, changing nothing
#
# **Why this exists next to `worker.sh`.** That script checks and refuses: it
# dies with "the Discovery Index is not running" and the command to fix it,
# which is right when a service is down for a reason someone should look at, and
# tiresome on a laptop that was rebooted. This one starts what is missing and
# then hands over to `worker.sh` for the checks, so the two do not drift -- every
# refusal in there is a failure that actually happened, and none of them are
# copied here.
#
# It also reaches into /libvox-db to start the one PostgreSQL server, because
# both databases this module needs live there: `libvox_index`, which it writes,
# and `libvox_tenant`, which holds the outbox the worker listens on. A worker
# without that server is a worker with nothing to hear. Starting it is said out
# loud rather than done quietly.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

DB_COMPOSE="../libvox-db/docker-compose.yml"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# `-f <file>` is two arguments, and ${2:+-f "$2"} unquoted splits a path with
# a space in it into three. An array carries the pair intact.
running() {
    local -a compose=(docker compose)
    [ -n "${2:-}" ] && compose+=(-f "$2")
    "${compose[@]}" ps "$1" --status running --quiet 2>/dev/null | grep -q .
}

# --- the database ------------------------------------------------------------

# One server now, in /libvox-db, holding the index this module writes and the
# outbox it listens on. This used to be two functions against two servers, and
# the difference between them was which compose file they named.
start_database() {
    if running postgres "$DB_COMPOSE"; then
        ok 'database already up (5435)'
    else
        [ -f "$DB_COMPOSE" ] || die \
"The one PostgreSQL server lives in /libvox-db, and that module is not beside
this one. It holds the index this module writes and the outbox its worker
listens on. Start it there:
  cd ../libvox-db && ./db.sh"

        docker compose -f "$DB_COMPOSE" up -d postgres >/dev/null 2>&1
        ok 'database started (5435)'
    fi

    # Healthy, not merely running. The window between "container up" and
    # "accepting connections" is where a migrate run lands on a closed socket,
    # which reads as a broken database rather than an early one.
    for _ in $(seq 1 40); do
        docker compose -f "$DB_COMPOSE" exec -T postgres \
            pg_isready -U libvox -d libvox_index >/dev/null 2>&1 && return 0
        sleep 1
    done
    die 'The database never became ready. cd ../libvox-db && ./db.sh logs'
}

# Migrate only when there is something to apply.
#
# `migrate` is idempotent and would be safe to run every time, but it builds the
# image first, and paying a build on every start to be told "schema already up
# to date" is how a startup script becomes something people skip.
applied_migrations() {
    docker compose -f "$DB_COMPOSE" exec -T postgres \
        psql -U libvox -d libvox_index -tAc \
        "SELECT count(*) FROM schema_migrations" 2>/dev/null | tr -d '\r' || true
}

migrate_if_behind() {
    local applied wanted
    applied=$(applied_migrations)
    wanted=$(ls -1 migrations/*.sql 2>/dev/null | wc -l)

    if [ "${applied:-0}" = "$wanted" ] && [ "$wanted" -gt 0 ]; then
        ok "schema current (${applied} migrations)"
        return 0
    fi

    # Built first, and this is not caution. The image bakes `migrations/` in, so
    # `compose run` against whatever image happened to be there applies the
    # migrations that existed when it was built -- and the runner, seeing every
    # file it has, reports "schema already up to date". That is the dangerous
    # shape: a reassuring sentence about an incomplete schema. Observed with
    # 004 and 005 on disk, three applied, and an import that died on a missing
    # `series` column hours later. ./core.sh already builds here for a
    # different symptom of the same cause.
    say "Building the image"
    docker compose build sync

    say "Migrating the index (${applied:-0} applied, ${wanted} on disk)"
    docker compose run --rm sync migrate

    # Asked again rather than trusted. Every way this can go wrong -- a stale
    # image, a migration that failed halfway, a role that cannot create a
    # column -- ends with the schema short and the command exiting zero.
    applied=$(applied_migrations)
    if [ "${applied:-0}" != "$wanted" ]; then
        die "The index is still behind: ${applied:-0} applied, ${wanted} on disk.
Nothing that follows will work, and an import will die on a missing column.
  docker compose run --rm sync migrate     # for the error itself"
    fi
    ok "schema current (${applied} migrations)"
}

# --- the thing that cost an hour ---------------------------------------------

# A bridge left behind by a previous daemon, sharing a subnet with a live one.
#
# The stale interface is DOWN, the route picks it anyway, and the host gets
# EHOSTUNREACH -- while the container is perfectly healthy and answers from
# inside itself. Seen after Docker's data-root was repointed: the published
# ports accepted connections and reset them, every host-side test skipped, and
# nothing in any container log said a word.
#
# A warning rather than a refusal: the containers talk to each other over the
# compose network and are unaffected. It is the host -- the test suites,
# ./core.sh, a browser -- that cannot get in.
check_shadowed_bridges() {
    command -v ip >/dev/null 2>&1 || return 0

    local live stale=""
    live=$(ip -br addr show type bridge 2>/dev/null | awk '$2=="UP" {print $3}' | sort -u)
    [ -n "$live" ] || return 0

    while read -r name state subnet; do
        [ "$state" = "DOWN" ] || continue
        [ -n "$subnet" ] || continue
        grep -qxF "$subnet" <<<"$live" && stale="${stale} ${name}"
    done < <(ip -br addr show type bridge 2>/dev/null | awk '{print $1, $2, $3}')

    [ -n "$stale" ] || return 0

    warn "a stale bridge shadows a live subnet:${stale}"
    printf '    the host will get "no route to host" for those containers while\n'
    printf '    they answer normally from inside. To clear it:\n'
    for br in $stale; do printf '      sudo ip link delete %s\n' "$br"; done
}

# --- commands ----------------------------------------------------------------

cmd_up() {
    say 'Services'
    start_database
    migrate_if_behind
    check_shadowed_bridges

    # Handed over rather than reimplemented. worker.sh owns the crypto key, the
    # key matching the portal's, the outbox constraint, the rebuild, and the
    # proof that the container stayed up.
    ./worker.sh up
}

cmd_down() {
    say 'Stopping'
    docker compose --profile worker down
    ok 'worker stopped'
    printf '  The database was left running — /libvox-db owns it, and two other\n'
    printf '  modules are reading it.\n\n'
}

cmd_status() {
    say 'Services'
    running postgres "$DB_COMPOSE" && ok 'database up (5435)' || warn 'database down'
    docker compose --profile worker ps worker --status running --quiet 2>/dev/null | grep -q . \
        && ok 'worker running' || warn 'worker not running'
    check_shadowed_bridges
    ./worker.sh check
}

case "${1:-up}" in
    up|start) cmd_up ;;
    down)     cmd_down ;;
    status)   cmd_status ;;
    logs)     docker compose --profile worker logs -f worker ;;
    *)        sed -n '3,8p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
