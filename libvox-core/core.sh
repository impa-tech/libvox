#!/usr/bin/env bash
#
# Build and run /libvox-core in its container.
#
# The reason this exists rather than a line in the README is the one
# ./portal.sh already learned: `docker compose build` alone does nothing
# visible. The Dockerfile bakes the source in, and compose reuses a container
# that is already up -- so a rebuilt image sits there while the old code keeps
# serving. That is not hypothetical here: a stale Core answered
# check_availability with an empty list for twenty minutes while the fix sat in
# the image nobody was running. `up` always does both halves.
#
#   ./core.sh              build, restart, wait until it answers
#   ./core.sh down         stop it
#   ./core.sh logs         follow
#   ./core.sh shell        a shell inside the container
#   ./core.sh check [tenant] [term]
#                          prove Core's four routes against a real library
#
#   ./core.sh drive [tenant] [term]
#                          the same chain, through /libvox-mcp
#   ./core.sh ask [tenant] "question"
#   ./core.sh ask [tenant] --in <conversation-id> "another question"
#                          ask MARC.IA, which needs a model and the MCP layer.
#                          --in continues a conversation, which is what lets the
#                          second question know what the first was about.
#   ./core.sh bench [tenant] [runs]
#                          measure the configured model against one fixed
#                          question, 8 times by default. Prints the row it
#                          measured, ready for the table in README.md.
#   ./core.sh token [tenant]
#                          print a session bearer, and nothing else, so another
#                          MCP client can be pointed at the Core path. Valid one
#                          hour, and a Core restart ends it.
#   ./core.sh migrate      apply pending migrations to the conversation log
#   ./core.sh prune [--dry-run] [--older-than DAYS]
#                          forget conversations past the retention window,
#                          across every library. 90 days by default.
#   ./core.sh widget [tenant] [port]
#                          serve a page that pretends to be an OPAC, with this
#                          library's widget in it. Wraps demo/serve.py, which
#                          also runs on its own with --key and no database.
#
# `check` exercises Core and stops there: it calls the HTTP routes directly,
# passing tenant_id the way /libvox-mcp would after resolving it. `drive` puts
# the MCP layer in front -- the tool schemas, LibVoxTokenVerifier,
# require_tenant, the scope check -- and is the only thing here that covers the
# tenant boundary rather than one side of it.
#
# For iterating on code, `python -m libvox_core` on the host is faster and needs
# no build at all -- .env already points at the same two databases.

set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

PORT=8091
URL="http://127.0.0.1:${PORT}"

say() { printf '\n\033[1m%s\033[0m\n' "$*"; }
die() { printf '\033[31m%s\033[0m\n' "$*" >&2; exit 1; }

require_env() {
    [ -f .env ] || die "No .env. Copy .env.example and fill in LIBVOX_CRYPTO_KEY."

    # compose declares LIBVOX_CRYPTO_KEY with `:?`, so a missing one fails at
    # container start with a message that names the variable and not what to do
    # about it.
    grep -qE '^LIBVOX_CRYPTO_KEY=.+' .env || die \
"LIBVOX_CRYPTO_KEY is empty. It decrypts the ILS credentials /libvox-portal
stored, so it must be byte-identical to that module's:

  grep '^LIBVOX_CRYPTO_KEY=' ../libvox-portal/.env >> .env

Credentials stored under one key cannot be read back with another."
}

require_databases() {
    # One server, in /libvox-db, holding all three databases Core touches: the
    # registry it reads, the index it searches, and the conversation log it
    # writes. This used to check two compose files belonging to two other
    # modules, which is what the consolidation removed.
    docker compose -f ../libvox-db/docker-compose.yml ps --status running postgres \
        2>/dev/null | grep -q postgres && return 0

    printf '\033[31mThe database is not running.\033[0m\n' >&2
    printf '  The registry, the Discovery Index and the conversation log are all on it:\n' >&2
    printf '      cd ../libvox-db && ./db.sh\n' >&2
    exit 1
}

wait_for_http() {
    printf 'waiting for %s ' "$URL"
    for _ in $(seq 1 60); do
        if curl -fsS -o /dev/null "${URL}/healthz" 2>/dev/null; then
            printf ' up\n'
            return 0
        fi
        printf '.'
        sleep 1
    done
    printf '\n'
    say 'It never answered. The last lines of the log:'
    docker compose logs --tail 30 core
    die "Giving up. Something else may hold port ${PORT} -- including an earlier
Core started by hand, which is the failure that is hardest to see, because it
answers and answers with old code. Check with:  ss -lntp | grep ${PORT}"
}

# Which model is actually answering, read from the container and not from .env.
#
# compose interpolates `${LIBVOX_LLM_MODEL}` from the shell that invoked it, and
# falls back to a default of its own when nothing sets it. So a ./core.sh run
# from a terminal that happened to export one swaps the model out from under a
# deployment whose .env says something else, and afterwards both files read
# correctly. This session spent an evening comparing three models and the answer
# to "which one is this?" was always `docker exec`; now it is on screen.
show_model() {
    local pair running endpoint configured
    pair=$(docker compose exec -T core sh -c \
        'printf "%s\034%s" "$LIBVOX_LLM_MODEL" "$LIBVOX_LLM_BASE_URL"' 2>/dev/null | tr -d '\r')
    running="${pair%%$'\034'*}"
    endpoint="${pair##*$'\034'}"
    configured=$(grep -E '^LIBVOX_LLM_MODEL=.+' .env 2>/dev/null | head -1 | cut -d= -f2-)

    printf '  Model           %s\n' "${running:-unknown}"
    # The endpoint and never the key: this prints to a terminal with a
    # scrollback, and LIBVOX_LLM_API_KEY has no business in one.
    [ -n "$endpoint" ] && printf '  Endpoint        %s\n' "$endpoint"

    if [ -n "$running" ] && [ -n "$configured" ] && [ "$running" != "$configured" ]; then
        printf '\n\033[33m  ! .env names %s; the container is running %s.\033[0m\n' \
            "$configured" "$running"
        printf '    compose took it from the shell that started this, not from .env.\n'
    elif [ -n "$running" ] && [ -z "$configured" ]; then
        printf '\n\033[33m  ! No LIBVOX_LLM_MODEL in .env, so compose used its own default.\033[0m\n'
        printf '    Put the three model variables there; a rebuild should not be able\n'
        printf '    to change which model a library is answered by.\n'
    fi
}

# How many migrations the conversation log has, against how many are on disk.
#
# Asked before serving, because the alternative already happened twice. Code
# shipped ahead of its schema and the container came up perfectly: the model
# answered, the tools ran, and the *insert* failed -- so a reader waited through
# a full round trip to be told the library's systems were unavailable, and the
# reason was `column "provider_extra" does not exist` in a log nobody was
# tailing.
#
# ./sync.sh does the same for the index, and reached it the same way.
migrate_if_behind() {
    local applied wanted
    applied=$(docker compose -f ../libvox-db/docker-compose.yml exec -T postgres \
        psql -U libvox -d libvox_conversations -tAc \
        "SELECT count(*) FROM schema_migrations" 2>/dev/null | tr -d '\r' || true)
    wanted=$(ls -1 migrations/*.sql 2>/dev/null | wc -l)

    if [ "${applied:-0}" = "$wanted" ] && [ "$wanted" -gt 0 ]; then
        return 0
    fi

    say "Migrating the conversation log (${applied:-0} applied, ${wanted} on disk)"
    cmd_migrate

    # Asked again rather than trusted, for the reason ./sync.sh states: every
    # way this goes wrong ends with the schema short and the command exiting
    # zero.
    applied=$(docker compose -f ../libvox-db/docker-compose.yml exec -T postgres \
        psql -U libvox -d libvox_conversations -tAc \
        "SELECT count(*) FROM schema_migrations" 2>/dev/null | tr -d '\r' || true)
    [ "${applied:-0}" = "$wanted" ] || die \
"The conversation log is still behind: ${applied:-0} applied, ${wanted} on disk.
Core would answer and then fail to store, which a reader sees as an outage.
  ./core.sh migrate     # for the error itself"
}

cmd_up() {
    require_env
    require_databases

    say 'Building the image'
    # Context is the repository root: Core imports /libvox-sync and
    # /libvox-driver-koha, and a build context cannot reach outside itself.
    docker compose build core

    say 'Starting'
    # --force-recreate is the whole point. Without it compose keeps the running
    # container and the image just built is never used.
    # Before serving, not after: the image is built, so `migrate` runs on the
    # code about to answer.
    migrate_if_behind

    docker compose up -d --force-recreate core

    wait_for_http

    cat <<EOF

  Health          ${URL}/healthz
  Internal API    ${URL}/internal/v1/...   (loopback only, service token required)
EOF
    show_model
    cat <<EOF

  The internal API takes tenant_id from the request body, so anything that can
  reach it can read any library's data. It is published to 127.0.0.1 on purpose.

  Point the MCP layer at it:
      export LIBVOX_MCP_BACKEND=core LIBVOX_CORE_URL=${URL}

EOF
}

# Proves the chain the way it actually breaks: a session bound to a real
# library, a search answered from the index, availability answered live, and an
# invented id refused before it reaches anyone's server.
# --- the session, which check, drive, ask and token all need ----------------
#
# One copy, because there were three and `token` would have made a fourth. The
# tenant never travels as an argument past this point: it names a widget key
# here, the key becomes a session, and from then on the credential *is* the
# library. That is propagation rule 7, and it is why no tool downstream accepts
# a tenant.

service_token() {
    grep -E '^LIBVOX_CORE_SERVICE_TOKEN=.+' .env 2>/dev/null | cut -d= -f2- || true
}

psql_tenant() {
    docker compose -f ../libvox-db/docker-compose.yml exec -T postgres \
        psql -U libvox -d libvox_tenant -tAc "$1" 2>/dev/null | tr -d '\r'
}

# Prints `<json-quoted key> <origin>` for a library's web channel.
binding_for() {
    local tenant="$1" binding
    binding=$(psql_tenant \
        "SELECT to_json(binding_key)::text || ' ' || (allowed_origins->>0)
           FROM channel_bindings WHERE tenant_id = '${tenant}' AND channel = 'web'") \
        || die "could not reach the Tenant DB. Is /libvox-portal up?"

    if [ -n "$binding" ]; then printf '%s' "$binding"; return; fi

    # "No binding" has two causes and two different fixes, and one sentence for
    # both sent people to the channels tab of a library that was never
    # registered.
    if [ -z "$(psql_tenant "SELECT 1 FROM tenants WHERE tenant_id = '${tenant}'")" ]; then
        die "no library called ${tenant}. Register it at http://localhost:8090/admin/tenants"
    fi
    die "${tenant} exists but has no web channel. Add one at http://localhost:8090/admin/tenants"
}

# The raw mint response, for callers that assert on more than the token.
mint_raw() {
    local tenant="$1" binding header=() svc
    svc=$(service_token)
    [ -n "${svc}" ] && header=(-H "x-libvox-service-token: ${svc}")
    # `|| return 1` rather than trusting set -e: `die` inside a command
    # substitution exits that subshell, and errexit does not reliably carry the
    # failure out of a function called this deep. Without it the curl below ran
    # with an empty binding_key and answered 422, burying the message that had
    # already said exactly what was wrong.
    binding=$(binding_for "$tenant") || return 1

    curl -fsS "${URL}/internal/v1/sessions" \
        -H 'content-type: application/json' "${header[@]}" \
        -d "{\"channel\":\"web\",\"binding_key\":${binding%% *},\"origin\":\"${binding##* }\"}" \
        || die "Core would not mint a session. Is it up?  ./core.sh up"
}

# Just the token, so it composes:  TOKEN=$(./core.sh token lib-demofranca)
mint_session() {
    command -v python3 >/dev/null || die "needs python3 to read the response"

    # Captured into a variable rather than inlined as an argument: `die` inside
    # a command substitution only exits the subshell, so an inlined mint_raw
    # failure reached python as an empty string and reported a JSON parse error
    # on top of the message that already said what was wrong.
    local body
    body=$(mint_raw "$1") || return 1

    python3 -c "
import json, sys
body = json.loads(sys.argv[1])
if not body.get('token'):
    raise SystemExit(
        'refused. Every refusal looks the same on purpose -- unknown key, '
        'disallowed origin, channel off, or a library that is not active.')
print(body['token'])
" "$body"
}

cmd_check() {
    command -v python3 >/dev/null || die "needs python3 to read the responses"

    local tenant="${1:-lib-local}" term="${2:-programming}"

    local svc token_header=()
    svc=$(service_token)
    [ -n "${svc}" ] && token_header=(-H "x-libvox-service-token: ${svc}")

    say "Resolving ${tenant}'s widget key and minting a session"
    local minted
    minted=$(mint_raw "$tenant") || exit 1
    python3 -c "
import json, sys
body = json.loads(sys.argv[1])
if not body.get('token'):
    print('  refused. Every refusal looks the same on purpose -- unknown key,')
    print('  disallowed origin, channel off, or a library that is not active.')
    print('  ${tenant} must be active at http://localhost:8090/admin/tenants')
    raise SystemExit(1)
print('  tenant', body['tenant_id'], '| widget', body['widget']['assistant_name'])
print('  the credential is not in the widget config:',
      body['token'][:8] not in json.dumps(body['widget']))
" "$minted"

    local token
    token=$(python3 -c 'import json,sys; print(json.loads(sys.argv[1])["token"])' "$minted")

    # The route /libvox-mcp calls on every single tool call, and the one this
    # check used to mint a credential for and then never exercise. No service
    # token here on purpose: it is the token verifier's own path.
    say 'Introspecting it, the way the MCP layer does before every tool call'
    curl -fsS "${URL}/internal/v1/sessions/introspect" \
        -H 'content-type: application/json' -d "{\"token\":\"${token}\"}" \
        | python3 -c "
import json, sys
body = json.load(sys.stdin)
print('  active', body['active'], '| tenant', body.get('tenant_id'),
      '| scopes', body.get('scopes'))
if 'catalog:read' not in (body.get('scopes') or []):
    print('  without catalog:read the MCP layer refuses every tool call')
    raise SystemExit(1)
"

    say "Searching for '${term}', from the index and never the ILS"
    local found
    found=$(curl -fsS "${URL}/internal/v1/search_catalog" \
        -H 'content-type: application/json' "${token_header[@]}" \
        -d "{\"tenant_id\":\"${tenant}\",\"query\":\"${term}\",\"author\":null,\"title\":null,\"limit\":3}")

    # Ids come back through a variable, not a temp file. A file would be one
    # more thing to clean up, and one more place a failed run leaves state.
    local ids
    ids=$(python3 -c "
import json, sys
body = json.loads(sys.argv[1])
print('  index', body['index_status'], '|', len(body['records']), 'record(s)', file=sys.stderr)
for r in body['records']:
    print('   ', r['biblio_id'], r['title'][:52], file=sys.stderr)
print(json.dumps([r['biblio_id'] for r in body['records']]))
" "$found")

    if [ "$ids" = "[]" ]; then
        say "Nothing matched '${term}' in ${tenant}. That is an answer, not a failure --"
        echo "  the term has to be in the language the library catalogued in. Try another:"
        echo "      ./core.sh check ${tenant} <term>"
        return 0
    fi

    say 'Availability, live -- with an invented id that must not reach the ILS'
    local probed
    probed=$(python3 -c "
import json, sys
print(json.dumps(json.loads(sys.argv[1]) + [999999]))
" "$ids")

    local availability
    availability=$(curl -fsS "${URL}/internal/v1/check_availability" \
        -H 'content-type: application/json' "${token_header[@]}" \
        -d "{\"tenant_id\":\"${tenant}\",\"biblio_ids\":${probed}}")

    local answered
    answered=$(python3 -c "
import json, sys
records = json.loads(sys.argv[1])['records']
for r in records:
    print('   ', r['biblio_id'], r['status'],
          f\"{r['copies_available']}/{r['copies_total']}\",
          ' / '.join(x for x in (r['library'], r.get('shelf')) if x),
          file=sys.stderr)
print('   999999 is absent above:',
      not any(r['biblio_id'] == 999999 for r in records),
      '(propagation rule 5)', file=sys.stderr)
print(len(records))
" "$availability")

    # Reporting success while every lookup failed is worse than the failure. An
    # empty list here is indistinguishable from 'the library holds none of
    # these', which is the confidently wrong answer this project exists to
    # avoid, so the script refuses to call it a pass.
    if [ "$answered" = "0" ]; then
        say 'Nothing came back, and the ids were in the index -- so the ILS refused.'
        cat <<EOF
  The likeliest cause when Core runs in a container: this library's stored
  base_url points at localhost, and inside a container localhost is the
  container. Confirm with:

      ./core.sh logs | grep availability

  Two ways out. Run Core on the host, which reaches a host-side Koha directly:

      python -m libvox_core

  Or change that library's catalog URL at http://localhost:8090/app to an
  address the container can reach. compose already wires the host gateway, so
  this resolves inside the container and needs nothing else:

      http://host.docker.internal:8081      # the docker0 gateway, 172.17.0.1

  That value then only works from a container, which is the trade. In a real
  deployment a library's ILS has a real hostname and neither arises.
EOF
        return 1
    fi

    say 'The chain holds.'
}

# Everything `check` does, with /libvox-mcp in front of it. Runs in that
# module's environment, because it needs the MCP SDK.
cmd_drive() {
    local tenant="${1:-lib-local}" term="${2:-programming}"

    local mcp_python="${LIBVOX_MCP_PYTHON:-$HOME/miniconda3/envs/libvox-mcp/bin/python}"
    [ -x "$mcp_python" ] || die \
"No Python at ${mcp_python}. This needs the MCP SDK, which lives in that
module's environment. Set LIBVOX_MCP_PYTHON, or create it:

  cd ../libvox-mcp && conda env create -f environment.yml"

    local svc
    svc=$(service_token)

    say "Minting a session for ${tenant}"
    local token
    token=$(mint_session "$tenant") || exit 1
    printf '  bound, and the model will never see it\n'

    LIBVOX_SESSION_TOKEN="$token" \
    LIBVOX_MCP_BACKEND=core \
    LIBVOX_CORE_URL="$URL" \
    LIBVOX_MCP_SERVICE_TOKEN="$svc" \
        "$mcp_python" _drive.py "$term"
}

# One question to MARC.IA, through everything: session, prompt, model, tools,
# and the conversation log. Needs /libvox-mcp running in streamable-http mode
# and LIBVOX_LLM_API_KEY in the environment if the model wants one.
cmd_ask() {
    local tenant="${1:-lib-local}"
    shift || true

    # --in comes before the question so the question can stay variadic and
    # unquoted-friendly. A conversation is not a session: continuing one mints
    # a fresh credential and picks up the stored history, which is the whole
    # reason the two have separate identifiers.
    local previous=""
    if [ "${1:-}" = "--in" ]; then
        previous="${2:?--in needs a conversation id}"
        shift 2
    fi

    local question="${*:-vocês têm alguma coisa do Dürrenmatt?}"

    local svc token_header=()
    svc=$(service_token)
    [ -n "${svc}" ] && token_header=(-H "x-libvox-service-token: ${svc}")

    # compose substitutes these from the invoking shell, so a rebuild from a
    # different terminal silently swaps the model out from under a running
    # deployment. Put them in .env, which compose reads on its own.
    grep -qE '^LIBVOX_LLM_BASE_URL=.+' .env 2>/dev/null || cat >&2 <<'WARN'

  warning: LIBVOX_LLM_BASE_URL is not in .env, so the container is using
  whatever the shell that last ran ./core.sh had -- or the local default.
  Put the three model variables in .env so a rebuild cannot change them.

WARN

    say "Minting a session for ${tenant}"
    local token
    token=$(mint_session "$tenant") || exit 1

    if [ -n "$previous" ]; then
        say "Asking, continuing ${previous}: ${question}"
    else
        say "Asking: ${question}"
    fi

    # -f is deliberately absent: on a 500 it makes curl print nothing, and the
    # next command then fails parsing an empty body -- which is a traceback
    # about JSON rather than about what broke. The status is checked instead.
    local body status
    body=$(curl -sS -w '\n%{http_code}' "${URL}/internal/v1/messages" \
        -H 'content-type: application/json' "${token_header[@]}" \
        -d "$(python3 -c "
import json, sys
body = {'session_id': sys.argv[1], 'text': sys.argv[2], 'capabilities': []}
if sys.argv[3]:
    body['conversation_id'] = sys.argv[3]
print(json.dumps(body))
" "$token" "$question" "$previous")")
    status="${body##*$'\n'}"
    body="${body%$'\n'*}"

    if [ "$status" != "200" ]; then
        say "Core answered ${status}. Its own log has the reason:"
        echo "      ./core.sh logs | tail -30"
        echo
        echo "  The three that account for most of them:"
        echo "    - /libvox-mcp is not running in streamable-http mode"
        echo "    - no model is reachable at LIBVOX_LLM_BASE_URL"
        echo "    - a database this container cannot reach on 127.0.0.1"
        return 1
    fi

    printf '%s' "$body" | python3 -c "
import json, sys
body = json.load(sys.stdin)
if not body.get('conversation_id'):
    raise SystemExit('  the session was refused')
print()
print(body['text'])
print()
calls = body.get('tool_calls') or []
if calls:
    print('  tools used:', ', '.join(
        c['name'] + (' (failed)' if c['failed'] else '') for c in calls))
if body.get('truncated'):
    print('  the loop hit its limit before finishing')
print()
print('  ./core.sh ask ${tenant} --in ' + body['conversation_id'] + ' \"...\"')
print('  continues this conversation, which is what lets the next question know')
print('  what this one was about.')
print()
"
}

# Measure one model against one question, the same way every time.
#
# This existed as a bash loop retyped once per model, feeding a table in the
# README that nobody else could regenerate and that risked counting differently
# each run. The value of the measurement is the question being identical, so the
# question is a constant here and not an argument with a default: a default is
# something a caller overrides without noticing, and the result is a row that
# does not belong in the table.
#
# "Opus posthumous está disponível?" against lib-local, where the record exists
# and a copy is on the shelf. It is also the question that produced the Kafka
# invention and then the Faulkner one, so a model that answers it well has
# cleared the bar that mattered. The three rows already in the README were
# measured with it.
#
# Four counts, because they are the four ways the measured models failed
# differently:
#
#   found and checked   the hermes3:8b went 0 of 8 -- it searches once,
#                       describes the record, and never looks at the shelf
#   searches per answer the ornith-1.5:9b spent 3 to 8 guessing at the author;
#                       the 35B does one
#   median time         the cost, and the reason to pick a worse model
#   machinery leaked    the hermes3:8b sent <tool_call> and <SCRATCHPAD> to a
#                       reader's screen
#
# Wall time of the whole ask, session minting included. Not the purest number --
# it carries a mint and two database round trips the model is not responsible
# for -- but it is the number the table already holds, and a column measured two
# ways is worse than one measured imperfectly.
BENCH_QUESTION="Opus posthumous está disponível?"

# The other half, and it went unmeasured for a week.
#
# Every question above is a hit: the record is there, found on the first round,
# availability on the second, done. A *miss* is a different path and it is the
# one that broke -- a reader asking for chess books was handed the agent's plan
# instead of an answer, in English, because the loop had spent its rounds and the
# final call asked nothing in particular.
#
# Nothing here holds this, and nothing plausibly will: it is a real title in a
# language the catalogue does not use, so it cannot start matching by accident
# the way a made-up word might once somebody imports a book about it.
BENCH_MISS="tem algum livro sobre culinária japonesa?"
BENCH_TENANT="lib-local"
BENCH_RUNS=8

# Mirrors _MACHINERY in agent/loop.py. Duplicated on purpose: this runs on the
# host, where libvox_core is not importable under the system python, and more to
# the point it should see what a reader would see rather than trust the code
# under test. A hit here means either the model leaked something new or _spoken
# has a gap -- and both are findings.
BENCH_MACHINERY='<tool_call </tool_call <function= </function <|tool_call <|python_tag|> <think </think <reasoning </reasoning SCRATCHPAD'

# The model the container is *running*, which is not always the one .env names:
# compose interpolates ${LIBVOX_LLM_MODEL} from the invoking shell. show_model
# already warns about the divergence -- but the first version of this printed a
# table row naming .env while the numbers came from the container, which is a
# row that would have been pasted into the README attributing one model's
# measurement to another. Found on the first real run.
bench_model() {
    docker compose exec -T core sh -c 'printf "%s" "$LIBVOX_LLM_MODEL"' \
        2>/dev/null | tr -d '\r'
}

# One question, `runs` times, and one row. Called twice: a hit and a miss, which
# are different paths through the loop and fail differently.
bench_one() {
    local label="$1" question="$2" tenant="$3" runs="$4"

    local svc token_header=()
    svc=$(service_token)
    [ -n "${svc}" ] && token_header=(-H "x-libvox-service-token: ${svc}")

    local results
    results=$(mktemp)
    # shellcheck disable=SC2064
    trap "rm -f '${results}'" RETURN

    local i token started ended body status
    for ((i = 1; i <= runs; i++)); do
        printf '  %s run %d/%d ' "$label" "$i" "$runs"

        token=$(mint_session "$tenant") || return 1

        started=$(date +%s%N)
        body=$(curl -sS -w '\n%{http_code}' "${URL}/internal/v1/messages" \
            -H 'content-type: application/json' "${token_header[@]}" \
            -d "$(python3 -c "
import json, sys
print(json.dumps({'session_id': sys.argv[1], 'text': sys.argv[2],
                  'capabilities': []}))
" "$token" "$question")")
        ended=$(date +%s%N)

        status="${body##*$'\n'}"
        body="${body%$'\n'*}"

        if [ "$status" != "200" ]; then
            printf '\n'
            die "Core answered ${status} on run ${i}. Its log has the reason:
      ./core.sh logs | tail -30"
        fi

        printf '%s\n' "$(python3 -c "
import json, sys
print(json.dumps({'ms': (int(sys.argv[1]) - int(sys.argv[2])) // 1000000,
                  'body': json.loads(sys.argv[3])}))
" "$ended" "$started" "$body")" >> "$results"

        # An endpoint that does not know the model answers 400, the loop returns
        # its unavailability sentence, and eight of those in five seconds look
        # like a measurement. Caught on the first run rather than the eighth,
        # and named: this is how hermes3:8b started, and how qwen3:1.7b spent an
        # afternoon looking like a broken agent.
        if [ "$i" = 1 ]; then
            python3 -c "
import json, sys
run = json.loads(open(sys.argv[1]).readline())
if not (run['body'].get('tool_calls') or []) and run['ms'] < 3000:
    raise SystemExit(1)
" "$results" || {
                printf '\n'
                say "Nothing to measure."
                printf '  The first answer came back in under three seconds having called no\n'
                printf '  tool, which is what an unreachable model looks like from here.\n\n'
                show_model
                printf '\n  The endpoint itself says why:\n'
                printf '      ./core.sh logs | grep -i "model endpoint failed" | tail -3\n\n'
                return 1
            }
        fi

        printf '\r'
    done

    printf '\n'
    python3 -c "
import json, statistics, sys

runs = [json.loads(line) for line in open(sys.argv[1])]
markers = sys.argv[2].split()

label = sys.argv[4]

# A miss cannot be 'found and checked' -- there is nothing to find, and scoring
# it that way would print 0/8 for a run that went perfectly. What a good miss
# looks like is one search and one sentence, so that is what is counted.
found, searches, leaked, plans = 0, [], 0, 0
for run in runs:
    calls = run['body'].get('tool_calls') or []
    names = [c['name'] for c in calls]
    text = (run['body'].get('text') or '')
    lowered = text.lower()

    if label == 'miss':
        # The outcome, not the route. Whether it varied the term once is the
        # searches column's job; scoring it here read 2/4 for four runs that
        # all answered correctly and identically.
        if text.strip() and not run['body'].get('truncated'):
            found += 1
    elif ('search_catalog' in names and 'check_availability' in names
            and not any(c['failed'] for c in calls)):
        found += 1

    searches.append(names.count('search_catalog'))
    if any(m.lower() in lowered for m in markers):
        leaked += 1

    # The failure this question exists for: the agent narrating what it would do
    # instead of answering. It reached a reader in English, citing a rule number
    # and promising a search it no longer had a round for.
    if run['body'].get('truncated') or ' i should ' in lowered or 'let me search' in lowered:
        plans += 1

times = sorted(r['ms'] / 1000 for r in runs)
median = statistics.median(times)
span = ('%d' % searches[0]) if min(searches) == max(searches) \
    else '%d - %d' % (min(searches), max(searches))

print('  runs                 %d' % len(runs))
print('  %-20s %d / %d'
      % ('answered plainly' if label == 'miss' else 'found and checked',
         found, len(runs)))
print('  searches per answer  %s' % span)
print('  median               %.1f s   (fastest %.1f, slowest %.1f)'
      % (median, times[0], times[-1]))
print('  machinery leaked     %d' % leaked)
if label == 'miss':
    print('  narrated a plan      %d' % plans)
print()
print('  The row, for the table in README.md:')
print()
print('  | \`%s\` (%s) | %d / %d | %s | %s s |'
      % (sys.argv[3] or 'the model', sys.argv[4], found, len(runs), span,
         ('%.1f' % median).replace('.', ',')))
print()
if leaked:
    print('  \033[33m! %d answer(s) carried tool-call machinery a reader would have seen.\033[0m'
          % leaked)
    print('    Either the model leaked a marker _spoken does not know, or _spoken')
    print('    has a gap. agent/loop.py:86 is the list.')
    print()
" "$results" "$BENCH_MACHINERY" "$(bench_model)" "$label"

    printf '  These %d conversations are in the log like any other, and the\n' "$runs"
    printf '  operator can read them at http://localhost:8090/admin/conversations\n\n'
}

cmd_bench() {
    require_env
    local tenant="${1:-$BENCH_TENANT}"
    local runs="${2:-$BENCH_RUNS}"

    command -v python3 >/dev/null || die "needs python3 to summarize"

    say "Benchmarking against ${tenant}, ${runs} run(s) each"
    show_model
    printf '\n  Hit             %s\n' "$BENCH_QUESTION"
    printf '  Miss            %s\n' "$BENCH_MISS"
    printf '  Measured        wall time of the whole ask, session minting included\n'

    bench_one hit  "$BENCH_QUESTION" "$tenant" "$runs" || return 1

    # The path that broke, and the one nothing measured. A miss should be one
    # search and one sentence -- not three searches, and not the plan the agent
    # would have carried out with a round it no longer had.
    bench_one miss "$BENCH_MISS" "$tenant" "$runs"
}

# Apply the conversation schema.
#
# There was no way to do this for a year of commits: 001 reached production
# because someone ran it by hand, and the only caller of the runner was the
# pytest fixture. A schema whose runner is the test suite drifts, and the drift
# is silent until a query names a column nobody created.
#
# Same throwaway container as `prune`, and the same admin DSN: creating a table
# needs an owner, and libvox_app is deliberately not one.
cmd_migrate() {
    require_env

    local dsn="${LIBVOX_CONVERSATION_ADMIN_DSN_DOCKER:-}"
    [ -n "$dsn" ] || dsn=$(grep -E '^LIBVOX_CONVERSATION_ADMIN_DSN_DOCKER=.+' .env 2>/dev/null | cut -d= -f2- || true)
    [ -n "$dsn" ] || dsn="postgresql://libvox:libvox@postgres:5432/libvox_conversations"

    # `-e NAME` with no value forwards what this shell holds, keeping the
    # password out of /proc/<pid>/cmdline.
    # Built first, and this is not caution. Run against the image that happened
    # to be there, `migrate` is an argument the old entrypoint does not
    # recognise -- so it falls through to the default and starts the web
    # server, which never exits. Observed exactly that: no output, no error, a
    # command that simply never returned.
    say 'Building the image'
    docker compose build core

    say 'Migrating the conversation log'
    LIBVOX_CONVERSATION_ADMIN_DSN="$dsn" \
        docker compose run --rm -T -e LIBVOX_CONVERSATION_ADMIN_DSN core migrate
}

# Forget conversations nobody has added to in a while.
#
#   ./core.sh prune --dry-run          count what would go
#   ./core.sh prune                    90 days, the default
#   ./core.sh prune --older-than 30
#
# TWO CONNECTIONS THIS MUST NOT USE
#
# Not the service's. `libvox_app` is subject to row-level security and an
# operator's job sets no tenant scope, so it sees no conversations, its DELETE
# matches nothing, and it commits cleanly. Measured on the dev database before
# the guard existed: 0 of 30 visible, 0 candidates. `prune` now refuses instead
# of reporting that it forgot nothing.
#
# And not the running container's. A long-lived web service should not hold a
# credential that reads across libraries; this one exists for the length of one
# command, in a container that is removed when it exits.
cmd_prune() {
    require_env

    local dsn="${LIBVOX_CONVERSATION_ADMIN_DSN_DOCKER:-}"
    [ -n "$dsn" ] || dsn=$(grep -E '^LIBVOX_CONVERSATION_ADMIN_DSN_DOCKER=.+' .env 2>/dev/null | cut -d= -f2- || true)
    [ -n "$dsn" ] || dsn="postgresql://libvox:libvox@postgres:5432/libvox_conversations"

    # `-e NAME` with no value forwards what this shell holds. Spelling it
    # `-e NAME=value` would put the password in /proc/<pid>/cmdline, which any
    # local process can read.
    LIBVOX_CONVERSATION_ADMIN_DSN="$dsn" \
        docker compose run --rm -e LIBVOX_CONVERSATION_ADMIN_DSN core prune "$@"
}

# The widget, in a page that pretends to be an OPAC.
#
#   ./core.sh widget lib-demofranca
#
# Serves demo/opac.html on 8093 with this library's real widget key pasted in,
# which is what the librarian's panel will generate. Served over http rather
# than opened from a file:// path on purpose: file:// sends `Origin: null`, the
# binding would be refused, and the refusal would look like a bug in the
# gateway rather than the Origin check doing its job.
cmd_widget() {
    local tenant="${1:-lib-local}" port="${2:-8093}"
    command -v python3 >/dev/null || die "needs python3 to serve the page"

    # demo/serve.py rather than a copy of it here: it prints the origin to
    # register, says whether the library already allows it, and needs none of
    # this module's environment.
    exec ./demo/serve.py --tenant "$tenant" --port "$port"
}

# Just the session token, for pointing another MCP client at the Core path.
#
#   TOKEN=$(./core.sh token lib-demofranca)
#   claude mcp add --transport http libvox http://127.0.0.1:8000/mcp \
#       --header "Authorization: Bearer ${TOKEN}"
#
# It expires in an hour and lives in the Core's memory, so a restart ends it.
# That is a demonstration, not a setup: sessions belong in a database before
# anything depends on one surviving.
#
# The token *is* the library. Nothing downstream takes a tenant argument, so
# one of these points at exactly one catalog and pointing at another means
# minting another.
cmd_token() {
    mint_session "${1:-lib-local}" || exit 1
}

case "${1:-up}" in
    up|rebuild)  cmd_up ;;
    down)        docker compose stop core ;;
    logs)        docker compose logs -f core ;;
    shell)       docker compose run --rm --entrypoint sh core ;;
    check)       shift; cmd_check "$@" ;;
    drive)       shift; cmd_drive "$@" ;;
    ask)         shift; cmd_ask "$@" ;;
    bench)       shift; cmd_bench "$@" ;;
    token)       shift; cmd_token "$@" ;;
    migrate)     cmd_migrate ;;
    prune)       shift; cmd_prune "$@" ;;
    widget)      shift; cmd_widget "$@" ;;
    *)           sed -n '3,41p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' ;;
esac
