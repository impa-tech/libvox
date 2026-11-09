#!/usr/bin/env bash
#
# Bring up the one PostgreSQL server and say what is in it.
#
#   ./db.sh                start it, wait until it answers, list the databases
#   ./db.sh psql <db>      a psql session against one of them
#   ./db.sh logs           follow
#   ./db.sh down           stop it, keeping the data
#   ./db.sh reset          stop it and DELETE the volume
#
# Run this before the other modules: /libvox-portal, /libvox-core and
# /libvox-sync all connect here and none of them starts a database any more.
#
# `docker compose up -d` alone would be enough to start it and is not enough to
# be useful. The listing at the end exists for the same reason ./core.sh prints
# its model: the question after "is it up?" is always "up with what in it?",
# and answering it on screen is cheaper than the psql invocation everyone was
# typing instead. It is also the fastest way to see that a schema is missing --
# a database at 8 MB has had no migration run against it.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PORT=5435
DATABASES=(
    libvox_tenant
    libvox_conversations
    libvox_index
    libvox_tenant_test
    libvox_conversations_test
    libvox_index_test
)

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

# As the superuser, because listing sizes and relation counts across databases
# is an owner's job and this script is an operator's tool. Nothing that serves
# a request ever connects this way -- see initdb/01-roles.sql for why that
# distinction is the one the isolation rests on.
#
# `-h 127.0.0.1` forces TCP, and that is load-bearing rather than stylistic.
# See wait_for_postgres.
psql_as_owner() {
    docker compose exec -T postgres \
        psql -h 127.0.0.1 -U libvox -d "$1" -tAc "$2" 2>/dev/null | tr -d '\r'
}

# Ready means "the real server answers over TCP", not "pg_isready said yes".
#
# On a fresh volume the postgres entrypoint runs the initdb scripts against a
# temporary server with `listen_addresses` empty, reachable only on the unix
# socket. `docker compose exec` is *inside* the container, so pg_isready there
# answers yes while 02-databases.sql has not run -- and the listing that follows
# then reports six databases as absent and advises `./db.sh reset`, which is
# destructive. Forcing TCP is what distinguishes the temporary server from the
# real one: the temporary one refuses the connection.
wait_for_postgres() {
    local waited=0
    printf '  waiting for postgres '
    until docker compose exec -T postgres \
        psql -h 127.0.0.1 -U libvox -d postgres -tAc 'SELECT 1' >/dev/null 2>&1; do
        [ "$waited" -ge 60 ] && { printf '\n'; die "postgres did not answer in 60s.  ./db.sh logs"; }
        printf '.'
        sleep 1
        waited=$((waited + 1))
    done
    printf ' ok\n'
}

# Size, and how many tables are in it. Size alone does not distinguish an empty
# database from a migrated one -- PostgreSQL charges about 8 MB for the
# catalogs either way -- and "0 tables" is the thing worth seeing.
show_databases() {
    local db size tables present missing=()

    # Asked once, of the catalog, rather than once per database. The previous
    # version ran a size query per name and read an empty answer as "absent" --
    # but an empty answer is also what a connection blip returns, and the advice
    # attached to it was `./db.sh reset`. A listing must never be able to
    # recommend deleting six databases because one query did not come back.
    present=$(psql_as_owner postgres \
        "SELECT string_agg(datname, ' ') FROM pg_database WHERE datname LIKE 'libvox%'")
    if [ -z "$present" ]; then
        printf '\n\033[31m  ! Could not read the list of databases.\033[0m\n'
        printf '    The server answered, so this is not "they are missing":\n'
        printf '        ./db.sh logs\n'
        return
    fi

    printf '\n  %-28s %10s %8s\n' "DATABASE" "SIZE" "TABLES"
    for db in "${DATABASES[@]}"; do
        if ! grep -qw "$db" <<<"$present"; then
            printf '  %-28s %10s\n' "$db" "absent"
            missing+=("$db")
            continue
        fi
        size=$(psql_as_owner postgres \
            "SELECT pg_size_pretty(pg_database_size('${db}'))")
        tables=$(psql_as_owner "$db" \
            "SELECT count(*) FROM information_schema.tables
              WHERE table_schema = 'public'")
        printf '  %-28s %10s %8s\n' "$db" "$size" "${tables:-?}"
    done

    if [ ${#missing[@]} -gt 0 ]; then
        printf '\n\033[31m  ! %d database(s) genuinely absent from pg_database.\033[0m\n' \
            "${#missing[@]}"
        printf '    initdb runs once, on an empty volume, so one created before a\n'
        printf '    database was added to 02-databases.sql never gets it.\n\n'
        printf '    Create the missing ones by hand -- same OWNER, REVOKE and GRANT as\n'
        printf '    initdb/02-databases.sql -- or, if there is nothing here worth keeping:\n'
        printf '        ./db.sh reset\n'
        return
    fi

    printf '\n  Each database has one writer and everyone else reads.\n'
    printf '  A database with 0 tables has had no migration run against it:\n'
    printf '      libvox_tenant         cd ../libvox-portal && php artisan migrate --database=pgsql_admin\n'
    printf '      libvox_conversations  cd ../libvox-core   && ./core.sh migrate\n'
    printf '      libvox_index          cd ../libvox-sync   && ./sync.sh\n'
}

cmd_up() {
    say "Starting the LibVox database server"
    docker compose up -d postgres
    wait_for_postgres
    printf '\n  Host            127.0.0.1:%s\n' "$PORT"
    printf '  In containers   postgres:5432   (network libvox_internal)\n'
    show_databases
    printf '\n'
}

cmd_reset() {
    # Named `reset` and not `down -v` because the destructive one should have
    # to be typed. For a volume that predates a change to initdb, creating the
    # missing database by hand is almost always the better cure -- this is for
    # when there is nothing here worth keeping.
    printf '\033[31mThis deletes every database on this server.\033[0m\n'
    printf 'Registered libraries, their encrypted ILS credentials, every stored\n'
    printf 'conversation, and every imported catalogue. Type the word: ' >&2
    local answer
    read -r answer
    [ "$answer" = "reset" ] || die "Nothing done."
    docker compose down -v
    say "Gone. ./db.sh brings back an empty server."
}

case "${1:-up}" in
    up|start)  cmd_up ;;
    down|stop) docker compose stop postgres ;;
    logs)      docker compose logs -f postgres ;;
    reset)     cmd_reset ;;
    psql)      docker compose exec postgres psql -U libvox -d "${2:?which database? one of: ${DATABASES[*]}}" ;;
    *)         sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
