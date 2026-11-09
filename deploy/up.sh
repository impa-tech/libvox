#!/usr/bin/env bash
#
# Bring the whole of LibVox up, in the order that works.
#
#   ./deploy/up.sh           check, then start everything
#   ./deploy/up.sh check     only the checks; changes nothing, pulls nothing
#   ./deploy/up.sh down      stop every container, keeping every database
#
#   On a deploy host -- one with deploy/host.env -- `up` pulls first.
#   LIBVOX_PULL=1 forces that anywhere; LIBVOX_PULL=0 refuses it anywhere.
#
# **Why this exists.** The order was prose in the root README, and half of this
# week's failures were order: 184 tests failing because the portal's suite had
# not run first, and an incomplete schema because `migrate` ran inside an image
# built before the migration existed. A sequence somebody has to remember is a
# sequence that eventually runs out of order.
#
# It reimplements nothing. Every module already has a script that builds,
# restarts, waits and names what it depends on; this calls them in order with
# `set -e`, so a failed migration stops the deploy instead of leaving half a
# stack up. Same principle as sync.sh handing over to worker.sh: one place per
# check, so the two cannot drift.
#
# **And it verifies the .env files first**, which is the honest compensation for
# keeping secrets out of git. They are the one part of the system a `git clone`
# does not reconstruct, so a missing key should be named here rather than
# discovered by a container that will not start.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
ROOT="$PWD"

say()  { printf '\n\033[1m%s\033[0m\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m!\033[0m %s\n' "$*"; }
die()  { printf '\n\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

MODULES=(libvox-core libvox-sync libvox-portal libvox-mcp libvox-driver-koha)

# The value of a key in an env file, or empty. Deliberately `.+` so a key that
# is present but blank reads the same as absent -- for a secret those are the
# same failure, and the caller should not have to know which it was.
#
# `|| :` because "empty" has to be an *answer* and not a failure. grep exits 1
# when it matches nothing, pipefail passes that on, and `set -e` then kills the
# script on the assignment -- so a caller reading a key that is legitimately
# absent got a silent exit instead of an empty string. It was latent in agree()
# from the start and only surfaced when check_vector_search read a key that is
# optional by design.
#
# `tail -1`, and it used to be `head -1`. A key written twice is the *last* one
# that reaches a container -- that is how compose reads a .env, and it is how a
# real host ended up serving a widget snippet nobody had configured. Reading
# the first occurrence made every check here agree with a value the container
# never saw. check_duplicate_keys below refuses that file outright; this is the
# same answer said twice, because a check that lies is worse than no check.
value_of() {
    { grep -E "^${2}=.+" "$1" 2>/dev/null || :; } | tail -1 | cut -d= -f2-
}

# --- 0. the same key written twice -------------------------------------------
#
# An editor appends, a paste lands at the bottom, and a file ends up saying two
# things. Nothing complains: compose takes the last one, the container starts,
# every panel works, and the value the operator is reading at the top of the
# file is not the value anything is using. It cost a real debugging session --
# `LIBVOX_WIDGET_URL` twice, the second one stale, and a snippet pointing at
# 127.0.0.1 on a public host.
#
# **Refused, not warned, but only when the two disagree.** The same value twice
# is untidy and unambiguous, so it says so and carries on. Two different values
# is a file that does not have an answer, and deploying it means deploying
# whichever line happens to be lower down.
check_duplicate_keys() {
    local module file label key lines values bad

    for module in "${MODULES[@]}" deploy; do
        file="${ROOT}/${module}/.env"
        label="${module}/.env"
        [ "$module" != deploy ] || { file="${ROOT}/deploy/host.env"; label="deploy/host.env"; }
        [ -f "$file" ] || continue

        bad=""
        while read -r key; do
            [ -n "$key" ] || continue

            lines=$(grep -nE "^${key}=" "$file" | cut -d: -f1 | tr '\n' ' ')

            # Quotes are syntax and not value: `KEY=""` and `KEY=` are the same
            # empty, and a check that called them a conflict would refuse a
            # deploy over punctuation. Found on the first run of this function.
            values=$({ grep -E "^${key}=" "$file" || :; } | cut -d= -f2- \
                | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'\$/\1/" \
                | sort -u | wc -l)

            if [ "$values" -gt 1 ]; then
                bad="${bad}
  ${key}   lines ${lines}"
            else
                warn "${label}: ${key} is written twice (lines ${lines}), both the same"
            fi
        done < <({ grep -oE '^[A-Z][A-Z0-9_]*=' "$file" || :; } | tr -d '=' | sort | uniq -d)

        [ -z "$bad" ] || die \
"${label} answers twice, with different values:
${bad}

The last one wins -- that is how compose reads this file, and how a container
ends up configured by a line nobody was looking at. Delete the ones you do not
mean."
    done
}

# --- a. keys the repository says you must supply -----------------------------
#
# The convention is already in the files and is readable: a key declared in
# .env.example **with no value** is the repository saying *you supply this*; one
# with a value is documenting a default that config.py also holds. Checked
# against reality before being relied on -- of the thirteen blank keys across
# the five examples, ten were filled in the real .env and the other three were
# Laravel's unused AWS_*, which is why those are gone from the example now.
check_required_keys() {
    local module example env key missing
    for module in "${MODULES[@]}"; do
        example="${ROOT}/${module}/.env.example"
        env="${ROOT}/${module}/.env"
        [ -f "$example" ] || continue

        [ -f "$env" ] || die \
"${module}/.env does not exist.

The repository ships ${module}/.env.example as the contract. Secrets live
outside git on purpose, so this file is yours to create:
  cp ${module}/.env.example ${module}/.env"

        missing=""
        while read -r key; do
            [ -n "$key" ] || continue
            [ -n "$(value_of "$env" "$key")" ] || missing="${missing} ${key}"
        done < <(grep -E '^[A-Z][A-Z0-9_]*=$' "$example" | cut -d= -f1)

        [ -z "$missing" ] || die \
"${module}/.env is missing a value for:${missing}

Those are declared empty in ${module}/.env.example, which is how this repository
says a value has no default and has to be supplied."

        ok "${module}/.env has every key it must"
    done
}

# --- b. the three that must agree across modules -----------------------------
#
# This is the part worth the most, because these fail silently. A crypto key
# that merely *differs* starts everything perfectly and then fails every import
# with a decryption error, hours later and one library at a time -- the error
# talks about pgcrypto, not about configuration.
#
# worker.sh already checks the first of these against the portal. This is that
# check generalized; worker.sh keeps its own because it also runs standalone.
agree() {
    local label="$1" a_mod="$2" a_key="$3" b_mod="$4" b_key="$5" a b
    a=$(value_of "${ROOT}/${a_mod}/.env" "$a_key")
    b=$(value_of "${ROOT}/${b_mod}/.env" "$b_key")

    # Absence is check_required_keys' job, not this one. Reporting it twice
    # would bury the mismatch under a message about the same file.
    [ -n "$a" ] && [ -n "$b" ] || return 0

    [ "$a" = "$b" ] || die \
"${label} do not match.

  ${a_mod}/.env    ${a_key}
  ${b_mod}/.env    ${b_key}

Nothing refuses to start over this. It breaks later, in whichever component
needed the other side to have the same value."

    ok "$label agree"
}

check_shared_secrets() {
    # Credentials are stored under this key and cannot be read back with
    # another. Three modules hold it: the portal writes, sync and core read.
    agree "crypto keys (portal, sync)" \
        libvox-portal LIBVOX_CRYPTO_KEY libvox-sync LIBVOX_CRYPTO_KEY
    agree "crypto keys (portal, core)" \
        libvox-portal LIBVOX_CRYPTO_KEY libvox-core LIBVOX_CRYPTO_KEY

    # The portal calls Core's internal API to test a library's catalogue
    # connection, and Core validates the caller with this.
    agree "service tokens (portal, core)" \
        libvox-portal LIBVOX_CORE_SERVICE_TOKEN libvox-core LIBVOX_CORE_SERVICE_TOKEN

    # /libvox-mcp reaches Core's internal API with the same token under its own
    # name. The root README documents this as a shell line that copies one into
    # the other, which demonstrates the relationship without checking it.
    agree "MCP token against Core's" \
        libvox-mcp LIBVOX_MCP_SERVICE_TOKEN libvox-core LIBVOX_CORE_SERVICE_TOKEN
}

# --- c. the one feature that is off by default -------------------------------
#
# Reported, never required. An index without vectors answers every search from
# the lexical arm, which is what this system did for its first year, so a
# deploy must not stop over it. But it must not stay quiet either: the whole
# failure mode is that everything comes up healthy and a reader asking in
# Portuguese about an English shelf is told the library has nothing.
#
# The half-configured cases are the ones worth naming, because both are silent
# and both are useless. Core alone embeds a query per question and matches
# nothing, since no record carries a vector. The worker alone fills a column
# nobody reads.
check_vector_search() {
    local core sync
    core=$(value_of "${ROOT}/libvox-core/.env" LIBVOX_EMBEDDING_MODEL)
    sync=$(value_of "${ROOT}/libvox-sync/.env" LIBVOX_EMBEDDING_MODEL)

    if [ -n "$core" ] && [ -n "$sync" ]; then
        ok "vector search on (${core})"
        return 0
    fi

    if [ -z "$core" ] && [ -z "$sync" ]; then
        warn "vector search off — searches are answered by the lexical index alone"
        printf '      To turn it on, see the block at the end of\n'
        printf '      libvox-core/.env.example and libvox-sync/.env.example.\n'
        return 0
    fi

    warn "vector search is half configured, which does nothing at all:"
    printf '      libvox-core/.env   LIBVOX_EMBEDDING_MODEL  %s\n' "${core:-(unset)}"
    printf '      libvox-sync/.env   LIBVOX_EMBEDDING_MODEL  %s\n' "${sync:-(unset)}"
    printf '      Core embeds the question, the worker embeds the records, and\n'
    printf '      neither is worth anything without the other.\n'
}

# --- d. the snippet a librarian pastes into her OPAC --------------------------
#
# `deploy/host.env` names the hostname Caddy serves the widget on, and the
# portal's LIBVOX_WIDGET_URL is what the Assistant screen prints into the one
# line a library installs. Nothing connects the two, and nothing fails when
# they disagree: the stack comes up, every panel works, and the line copies
# cleanly into an OPAC where it loads nothing at all. Found on a real host
# printing `http://127.0.0.1:8092/widget.js` to a librarian in a browser.
#
# Warned, not refused. A wrong snippet is expensive and silent, but it is not
# an outage, and a deploy that stops over it would be stopping the fix.
check_widget_url() {
    local domain configured
    domain="${LIBVOX_WIDGET_DOMAIN:-}"

    # A laptop reaches the widget on a port, and the panel works that out from
    # the request it is answering. Nothing to agree with.
    [ -n "$domain" ] || return 0

    configured=$(value_of "${ROOT}/libvox-portal/.env" LIBVOX_WIDGET_URL)

    case "$configured" in
        *"$domain"*)
            ok "the widget snippet points at ${domain}"
            return 0
            ;;
    esac

    warn "the snippet a librarian pastes will not name ${domain}:"
    printf '      deploy/host.env      LIBVOX_WIDGET_DOMAIN  %s\n' "$domain"
    printf '      libvox-portal/.env   LIBVOX_WIDGET_URL     %s\n' \
        "${configured:-(unset — the panel guesses from its own address)}"
    printf '      Set the second to https://%s and deploy again. Nothing\n' "$domain"
    printf '      breaks meanwhile except the one line a library installs.\n'
}

cmd_check() {
    # Again, harmlessly: `check` also runs on its own, and one of the checks
    # below reads this host's own answers.
    load_host_env

    say 'Checking configuration'
    # First, because every check below reads these files and a file that says
    # two things makes all of them unreliable.
    check_duplicate_keys
    check_required_keys
    check_shared_secrets
    check_vector_search
    check_widget_url
}

# --- the code being deployed -------------------------------------------------
#
# Everything below deploys the working tree, and the point of a *deploy* is the
# commit somebody pushed -- so `up` fetches it first rather than trusting that
# whoever is at the terminal remembered to. `check` does not, because it says it
# changes nothing, and `down` does not, because stopping yesterday's containers
# is what it is for.
#
# **Only on a deploy host, and `deploy/host.env` is how it knows.** That file is
# this machine's own answers -- its hostnames, its bind interface -- and it is
# absent on a laptop by definition. A workstation is where somebody is editing
# the tree on purpose, and refusing to start the stack over uncommitted work
# there is the check getting in the way of the person it was written for. It
# happened within a day of the pull being added.
#
# LIBVOX_PULL settles it either way: 1 pulls anywhere, 0 refuses to anywhere.
#
# **Fast-forward only, and never over a modified tracked file.** A deploy host
# that merges is a deploy host running a commit that exists nowhere else, which
# is the one thing this file's whole philosophy is against. Every refusal names
# what to do instead; none tries to be clever about it.
#
# **Untracked files warn and do not stop it**, which is a correction. The first
# thing this refused on was `deploy/host.env` -- a host's own configuration,
# with a `.example` beside it in git, which simply had no `.gitignore` to sit
# under. A deploy host accumulates files nobody committed, and almost none of
# them change what gets deployed. The ones that can are the ones a Dockerfile
# copies, so they are named rather than ignored.
pull() {
    local wanted="${LIBVOX_PULL:-}"

    if [ -z "$wanted" ]; then
        [ -f "${ROOT}/deploy/host.env" ] && wanted=1 || wanted=0
    fi

    if [ "$wanted" != 1 ]; then
        say 'Deploying the tree as it stands'
        if [ -n "${LIBVOX_PULL:-}" ]; then
            printf '  LIBVOX_PULL=0, so nothing was fetched.\n'
        else
            printf '  No deploy/host.env, so this is a workstation and the tree\n'
            printf '  is yours. LIBVOX_PULL=1 to fetch anyway.\n'
        fi
        return 0
    fi

    say 'Updating to what was pushed'

    git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die \
"${ROOT} is not a git repository.

Nothing to pull, and nothing this script can do about it. Deploy the tree as
it stands with:
  LIBVOX_PULL=0 ./deploy/up.sh"

    local branch upstream before after
    branch=$(git rev-parse --abbrev-ref HEAD)
    [ "$branch" != HEAD ] || die \
"HEAD is detached, so there is no branch to pull.

  git switch main        # or whichever branch this host follows"

    upstream=$(git rev-parse --abbrev-ref --symbolic-full-name '@{upstream}' 2>/dev/null) || die \
"${branch} tracks nothing, so there is nowhere to pull from.

  git branch --set-upstream-to=origin/${branch} ${branch}"

    [ -z "$(git status --porcelain --untracked-files=no)" ] || die \
"Tracked files have changes that are not committed:

$(git status --short --untracked-files=no | sed 's/^/  /')

A deploy runs what is in git, and a pull would either refuse or bury these.
Commit them, or put them aside with \`git stash\`, or deploy the tree as it
stands:
  LIBVOX_PULL=0 ./deploy/up.sh"

    # Named, not refused. Most are logs and dumps and change nothing; the ones
    # that matter are the ones a Dockerfile would copy into an image, and only
    # somebody looking at the list can tell which is which.
    local untracked
    untracked=$(git ls-files --others --exclude-standard)
    if [ -n "$untracked" ]; then
        warn "not in git, and not deployed unless a Dockerfile copies it:"
        printf '%s\n' "$untracked" | sed 's/^/      /'
    fi

    before=$(git rev-parse HEAD)
    git pull --ff-only
    after=$(git rev-parse HEAD)

    if [ "$before" = "$after" ]; then
        ok "already at $(git log --oneline -1 --format='%h %s' | cut -c1-60)"
        return 0
    fi

    ok "$(git rev-list --count "$before..$after") commit(s) from ${upstream}"

    # **The script just rewrote itself.** Bash reads a script by byte offset as
    # it runs, so a `git pull` that changes this file leaves the interpreter
    # reading the rest of a *different* file from wherever it happened to be --
    # which executes whatever text now sits at that offset. Re-exec is the fix,
    # and it has to happen here rather than at the end of the deploy: the point
    # is to run the new file's order, not the old one's.
    if ! git diff --quiet "$before" "$after" -- deploy/up.sh; then
        say 'deploy/up.sh itself changed — restarting with the new one'
        exec "${ROOT}/deploy/up.sh" "$@"
    fi
}

# --- the order ---------------------------------------------------------------

step() {
    local module="$1"; shift
    printf '\n\033[1m── %s  %s\033[0m\n' "$module" "$*"
    ( cd "${ROOT}/${module}" && "$@" )
}

# TLS, and only where somebody has said where.
#
# Started from the same evidence the widget URL is derived from: if the two
# hostnames are configured, this host is meant to be reached by name and wants a
# certificate. If they are not, it is a laptop, and a Caddy failing an ACME
# challenge every few minutes would be the only thing it accomplished.
start_proxy() {
    if [ -z "${LIBVOX_PORTAL_DOMAIN:-}" ] || [ -z "${LIBVOX_WIDGET_DOMAIN:-}" ]; then
        printf '\n  No proxy: LIBVOX_PORTAL_DOMAIN and LIBVOX_WIDGET_DOMAIN are unset,\n'
        printf '  so 8090 and 8092 are served directly. See deploy/README.md.\n'
        return 0
    fi

    printf '\n\033[1m── deploy/proxy  caddy\033[0m\n'
    ( cd "${ROOT}/deploy/proxy" && docker compose up -d )
    printf '  https://%s   the panels\n' "$LIBVOX_PORTAL_DOMAIN"
    printf '  https://%s   the widget\n' "$LIBVOX_WIDGET_DOMAIN"
}

# The two hostnames and the bind interface, from the file that holds this
# host's own answers. Read by both directions: `up` needs them to decide whether
# to start a proxy, and `down` needs them to find the one it started.
load_host_env() {
    # Inert today: nothing publishes an image, so `dev` is the only tag that
    # exists and the compose files build it. It is exported because all five
    # `image:` lines read it.
    # shellcheck disable=SC1091
    [ -f deploy/versions.env ] && . deploy/versions.env
    # Absent on a laptop, which is why nothing here is required and every
    # default reproduces the unproxied behaviour.
    # shellcheck disable=SC1091
    [ -f deploy/host.env ] && . deploy/host.env
    export LIBVOX_VERSION="${LIBVOX_VERSION:-dev}"
    export LIBVOX_BIND="${LIBVOX_BIND:-0.0.0.0}"
    export LIBVOX_PORTAL_DOMAIN="${LIBVOX_PORTAL_DOMAIN:-}"
    export LIBVOX_WIDGET_DOMAIN="${LIBVOX_WIDGET_DOMAIN:-}"
}

# Symmetric to start_proxy, and guarded by the same two variables -- the proxy's
# compose file demands both (`:?`), so an unproxied host cannot even be asked
# about a container it never started.
stop_proxy() {
    [ -n "${LIBVOX_PORTAL_DOMAIN:-}" ] && [ -n "${LIBVOX_WIDGET_DOMAIN:-}" ] || return 0

    printf '\n\033[1m── deploy/proxy  caddy\033[0m\n'
    ( cd "${ROOT}/deploy/proxy" && docker compose down )
}

cmd_up() {
    # Before the pull, which decides whether to run at all from this file's
    # presence, and before the checks, one of which reads a value out of it.
    load_host_env

    pull "$@"
    cmd_check

    say "Deploying ${LIBVOX_VERSION}"

    # The database first: everything connects to it, and it is what creates the
    # libvox_internal network the rest declare as external.
    step libvox-db ./db.sh

    # Schema before the service that needs it, in every case. All three migrate
    # paths build their image first, so "migrate then start" runs on the code
    # being deployed rather than on whatever image was lying around.
    step libvox-portal ./portal.sh migrate

    # The Discovery Index's own migrations, and the worker with them. It needs
    # the portal's database for the outbox it listens on, which the step above
    # is what creates -- not the portal *container*, which is why this can come
    # before it and now has to.
    #
    # It used to be last, and that left a window: the import screen reads
    # `discovery_tenant.import_started_at`, and between a portal built with that
    # query and the migration that adds the column, the screen is a 500. Seconds
    # on a deploy, and exactly the kind of order this file exists to hold.
    step libvox-sync ./sync.sh

    step libvox-portal ./portal.sh

    step libvox-core ./core.sh migrate
    step libvox-core ./core.sh

    step libvox-mcp ./mcp.sh

    start_proxy

    say 'Up.'
    printf '  Operator panel   http://localhost:8090/admin\n'
    printf '  Library panel    http://localhost:8090/app\n\n'
    printf '  Prove it end to end, which crosses all four images and all three databases:\n'
    printf '      cd libvox-core && ./core.sh bench lib-local 4\n\n'
}

cmd_down() {
    load_host_env
    say 'Stopping everything'

    # The reverse of cmd_up, and for the mirror of its reason: the database is
    # what everything else connects to, so it goes last.
    #
    # Each module's own `down` is what runs, rather than a `docker compose down`
    # written out five times here. They do not all mean the same thing -- the
    # database is stopped and kept, /libvox-sync takes its worker's containers
    # away, the portal stops one service and leaves the project -- and each of
    # those is a decision made where the compose file is. Same principle as the
    # order above: one place per behaviour, so the two cannot drift.
    #
    # **No `-v`, anywhere, ever.** The volume under /libvox-db holds every
    # registered library, its encrypted ILS credentials, every conversation and
    # every imported catalogue; the compose file there says so, and `./db.sh
    # reset` is the deliberate way to ask for that, with a word to type.
    stop_proxy

    step libvox-mcp ./mcp.sh down
    step libvox-core ./core.sh down
    step libvox-portal ./portal.sh down
    step libvox-sync ./sync.sh down
    step libvox-db ./db.sh down

    say 'Down.'
    printf '  Every database was kept. ./deploy/up.sh brings it all back.\n\n'
}

case "${1:-up}" in
    up)    cmd_up "$@" ;;
    check) cmd_check; printf '\n' ;;
    down)  cmd_down ;;
    *)     sed -n '3,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
