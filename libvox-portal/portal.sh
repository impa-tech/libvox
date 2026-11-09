#!/usr/bin/env bash
#
# Build and run /libvox-portal in its container.
#
# The reason this exists rather than a line in the README: `docker compose build`
# alone does nothing visible. The Dockerfile bakes the source in with `COPY . .`,
# and compose reuses a container that is already up -- so a rebuilt image sits
# there while the old code keeps serving, and the symptom is a bug you already
# fixed still happening. `up` here always does both.
#
#   ./portal.sh              build, restart, wait until it answers
#   ./portal.sh down         stop the portal (the database is /libvox-db's)
#   ./portal.sh logs         follow
#   ./portal.sh shell        a shell inside the container
#   ./portal.sh migrate      run migrations as the owner role
#   ./portal.sh seed         create the two roles and the first operator account
#   ./portal.sh artisan ...  any artisan command inside the container
#
# The database is not here. /libvox-db owns the one PostgreSQL server and has to
# be up first; this script checks and says so rather than letting the container
# fail against a closed socket.
#
# For iterating on code, `php artisan serve` on the host is faster and needs no
# build at all: the .env already points at this same database.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PORT=8090
URL="http://localhost:${PORT}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

require_env() {
    [ -f .env ] || die "No .env. Copy .env.example and run: php artisan key:generate"

    # compose declares both with `:?`, so a missing one fails at container start
    # with an error that names the variable and not what to do about it.
    grep -qE '^APP_KEY=.+' .env \
        || die "APP_KEY is empty. Run: php artisan key:generate"
    grep -qE '^LIBVOX_CRYPTO_KEY=.+' .env \
        || die "LIBVOX_CRYPTO_KEY is empty. It encrypts stored ILS secrets:
  echo \"LIBVOX_CRYPTO_KEY=\$(openssl rand -base64 32)\" >> .env
Keep it: credentials stored under one key cannot be read back with another,
and /libvox-sync must be given the same value."
}

wait_for_http() {
    printf 'waiting for %s ' "$URL"
    for _ in $(seq 1 60); do
        if curl -fsS -o /dev/null "${URL}/admin/login" 2>/dev/null; then
            printf ' up\n'
            return 0
        fi
        printf '.'
        sleep 1
    done
    printf '\n'
    say 'It never answered. The last lines of the log:'
    docker compose logs --tail 30 portal
    die "Giving up. Port ${PORT} may be taken -- 8080 and 8081 are Koha Testing Docker's."
}

# The database is /libvox-db's now, and compose cannot wait on a service in
# another project -- `depends_on: service_healthy` does not cross projects. So
# the check is here, and it names what is down instead of letting the container
# come up and fail against a closed socket.
require_database() {
    docker compose -f ../libvox-db/docker-compose.yml ps --status running postgres \
        2>/dev/null | grep -q postgres && return 0

    printf '\033[31mThe database is not running.\033[0m\n' >&2
    printf '  The Tenant DB this panel writes lives on it:\n' >&2
    printf '      cd ../libvox-db && ./db.sh\n' >&2
    exit 1
}

# How many people can log in. Asked of the database rather than of artisan,
# which would mean building and running a container to answer a scalar.
operator_count() {
    docker compose -f ../libvox-db/docker-compose.yml exec -T postgres \
        psql -U libvox -d libvox_tenant -tAc "SELECT count(*) FROM users" \
        2>/dev/null | tr -d '\r'
}

cmd_up() {
    require_env

    say 'Building the image'
    docker compose build portal

    require_database

    say 'Starting'
    # --force-recreate is the whole point: without it compose keeps the running
    # container, and the image just built is never used.
    docker compose up -d --force-recreate portal

    wait_for_http

    printf '\n  Operator panel   %s/admin\n' "$URL"
    printf '  Library panel    %s/app\n\n' "$URL"

    # It used to print `operator@libvox.local / password` unconditionally, which
    # was true of every database this script had ever met and false of a fresh
    # one: migrations create the tables and seed nothing, so the panel came up
    # advertising an account that did not exist. Found the first time the
    # database was rebuilt from empty.
    if [ "$(operator_count)" = "0" ]; then
        printf '  \033[33mNobody can log in yet.\033[0m The schema is there and the tables are empty:\n'
        printf '      ./portal.sh seed\n\n'
        printf '  That creates the two roles and one operator account.\n\n'
    else
        printf '  Seeded sign-in   operator@libvox.local / password\n'
        printf '  A library user has no seeded account: create one at /admin/users\n\n'
        printf '  That password is a development default. Override it before seeding\n'
        printf '  anywhere the panel is reachable by anyone else -- see the README.\n\n'
    fi
}

# Deliberately not part of `up`, though both seeders are idempotent and it would
# work. Seeding creates an account whose password is `password`, and doing that
# unasked on every start is exactly what the warning under it tells you not to
# do. Asking for it is the consent.
# Built first, and this is not caution -- ./core.sh and ./sync.sh both learned
# it the same way. `compose run` reuses whatever image is there, so a migration
# added since the last build is invisible to it: /libvox-sync once applied three
# of five and reported "schema already up to date", and an import died hours
# later on a column the fourth would have created.
#
# It also matters for the order deploy/up.sh uses. Migrating before starting is
# only safe if the migration runs on the image being deployed.
cmd_migrate() {
    require_env
    require_database
    say 'Building the image'
    docker compose build portal
    say 'Migrating the Tenant DB'
    docker compose run --rm portal php artisan migrate --database=pgsql_admin --force
}

cmd_seed() {
    require_env
    require_database
    say 'Seeding roles and the first operator'
    docker compose run --rm portal php artisan db:seed --force
    printf '\n  Sign in at %s/admin as operator@libvox.local / password\n' "$URL"
    printf '  Change it before anyone else can reach this panel.\n\n'
}

case "${1:-up}" in
    up|rebuild)  cmd_up ;;
    down)        docker compose stop portal ;;
    logs)        docker compose logs -f portal ;;
    shell)       docker compose run --rm --entrypoint sh portal ;;
    migrate)     cmd_migrate ;;
    seed)        cmd_seed ;;
    artisan)     shift; require_env; docker compose run --rm portal php artisan "$@" ;;
    *)           sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
