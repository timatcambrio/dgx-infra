#!/bin/sh
# Collect everything needed to diagnose a deployment that does not answer, in one run, as
# text that can be sent to whoever is helping. Run from the dgx-infra directory:
#
#   sh scripts/diagnose.sh 2>&1 | tee diagnose.txt
#
# It changes nothing: it reads files, asks Docker and the database what they hold, and makes
# a few requests to the service from this host. It needs no sudo.
#
# It prints NOTHING SECRET. From .env it prints only the settings that say where things are
# and how people connect; passwords and tokens are reported as set or unset, never their
# value. From the private key it prints only its first line (the PEM header, which says
# what kind of file it is) and a fingerprint of the PUBLIC key derived from it, which is
# what the certificate carries anyway. Per-request auth lines are left out of the service
# log, because they carry users' email addresses.
#
# Why it exists: on 2026-10-13 a deployment's HTTPS proxy restarted in a loop with no access
# to the host, and every useful fact (the proxy's own log line, whether the certificate and
# key match, what the paths in .env point at) needed a separate round trip to ask for.

set -u

ENV_FILE=${KB_ENV_FILE:-.env}
COMPOSE="docker compose -f compose/docker-compose.yml --env-file $ENV_FILE"

section() { printf '\n==== %s\n' "$1"; }
# A short fingerprint of stdin, or nothing when stdin is empty. Empty must stay empty: the
# hash of no input is the same for every file, and two of them would compare as a match.
fingerprint() {
    data=$(cat)
    [ -n "$data" ] || return 0
    if command -v sha256sum >/dev/null 2>&1; then printf '%s\n' "$data" | sha256sum | cut -c1-16
    else printf '%s\n' "$data" | shasum -a 256 | cut -c1-16; fi
}
say() { printf '%s\n' "$*"; }
# The value of KEY in .env, with surrounding quotes and a Windows line ending removed (the
# same way Compose reads it), or empty.
env_get() {
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" 2>/dev/null \
        | tail -n 1 | tr -d '\r' | sed -e 's/^"\(.*\)"$/\1/' -e "s/^'\(.*\)'$/\1/"
}

if [ ! -f compose/docker-compose.yml ]; then
    say "Run this from the dgx-infra directory (no compose/docker-compose.yml here: $(pwd))."
    exit 2
fi

section "When and where"
say "time (UTC):  $(date -u +%Y-%m-%dT%H:%M:%SZ)"
say "host:        $(hostname) ($(hostname -I 2>/dev/null | tr ' ' '\n' | grep -v '^172\.1[7-9]\.' | tr '\n' ' '))"
say "user:        $(id -un), groups: $(id -Gn)"
say "directory:   $(pwd)"
[ -r /etc/os-release ] && say "os:          $(. /etc/os-release && echo "$PRETTY_NAME"), $(uname -m)"

section "The code"
if command -v git >/dev/null 2>&1 && [ -d .git ]; then
    say "commit:      $(git log -1 --format='%h %cs %s' 2>/dev/null)"
    changed=$(git status --short 2>/dev/null)
    if [ -z "$changed" ]; then say "local edits: none"
    else say "local edits:"; printf '%s\n' "$changed" | head -n 20 | sed 's/^/  /'; fi
else
    say "not a git checkout (copied without .git?)"
fi
# A script with Windows line endings fails at its first line inside the container
# (`set: Illegal option -`); a file missing here is mounted as an empty directory.
for f in compose/caddy-entrypoint.sh compose/Caddyfile compose/gpu-detect.sh scripts/make_tls_cert.sh; do
    if [ ! -f "$f" ]; then say "$f: MISSING"
    elif grep -q "$(printf '\r')" "$f"; then say "$f: Windows line endings (CRLF)"
    else say "$f: ok"; fi
done

section "Docker"
say "docker:      $(command -v docker || echo 'not found')"
docker version --format 'engine:      {{.Server.Version}}' 2>&1 | head -n 1
docker compose version 2>&1 | head -n 1 | sed 's/^/compose:     /'
say "security:    $(docker info --format '{{.SecurityOptions}}' 2>&1 | head -n 1)"

section ".env (locations and connection settings only)"
if [ ! -f "$ENV_FILE" ]; then
    say "$ENV_FILE: MISSING"
else
    say "$ENV_FILE: $(ls -l "$ENV_FILE" | awk '{print $1, $3}')"
    # `sed -n l` shows each line as written: a trailing `$`, and `\r` for a Windows ending.
    grep -E '^[[:space:]]*(KB_PUBLIC_HOST|KB_URL_BASE|KB_TLS|KB_AUTH|TLS_CERT|TLS_KEY|KB_PATH|SOURCE_DIR|KB_TOKENS_FILE|KB_TOKENS_DIR|KB_GPU|EMBED_MODEL)[[:space:]]*=' "$ENV_FILE" \
        | sed -n 'l 0' | sed 's/^/  /'
    for k in POSTGRES_PASSWORD KB_INDEX_PASSWORD KB_READ_PASSWORD KB_TOKENS; do
        if [ -n "$(env_get "$k")" ]; then say "  $k is set (value not shown)"
        else say "  $k is not set"; fi
    done
fi

HOST=$(env_get KB_PUBLIC_HOST)
TLS_MODE=$(env_get KB_TLS); TLS_MODE=${TLS_MODE:-on}
CERT=$(env_get TLS_CERT)
KEY=$(env_get TLS_KEY)
case $HOST in
    '') say "  -> KB_PUBLIC_HOST is not set; the service answers only to localhost" ;;
    *[!A-Za-z0-9.-]*) say "  -> KB_PUBLIC_HOST '$HOST' is not a bare name or IP (no https://, port or /)" ;;
esac
if [ "$TLS_MODE" = off ]; then SCHEME=http; PORT=80; else SCHEME=https; PORT=443; fi
say "  -> mode: $SCHEME on port $PORT"

section "Certificate and key"
if [ "$TLS_MODE" = off ]; then
    say "plain HTTP: no certificate is used"
    [ -n "$CERT$KEY" ] && say "  -> TLS_CERT/TLS_KEY are set alongside KB_TLS=off; the proxy refuses to start like that"
elif [ -z "$CERT" ] && [ -z "$KEY" ]; then
    say "TLS_CERT/TLS_KEY not set: the proxy makes its own certificate (tls internal)"
else
    for pair in "TLS_CERT $CERT" "TLS_KEY $KEY"; do
        name=${pair%% *}; path=${pair#* }
        if [ -z "$path" ]; then say "$name: not set (both must be set, or neither)"
        elif [ -d "$path" ]; then say "$name: $path is a DIRECTORY (Docker made it: the file did not exist when the stack started)"
        elif [ ! -e "$path" ]; then say "$name: $path does not exist"
        else
            case $path in /*) ;; *) say "$name: $path is a relative path; Compose resolves it from compose/, not from here" ;; esac
            say "$name: $path, $(ls -l "$path" | awk '{print $1, $3, $5 " bytes"}'), first line: $(head -n 1 "$path")"
        fi
    done
    if [ -f "$CERT" ] && [ -f "$KEY" ] && command -v openssl >/dev/null 2>&1; then
        cert_pub=$(openssl x509 -in "$CERT" -noout -pubkey 2>/dev/null | fingerprint)
        key_pub=$(openssl pkey -in "$KEY" -pubout 2>/dev/null | fingerprint)
        say "public key in certificate: ${cert_pub:-UNREADABLE (not a PEM certificate?)}"
        say "public key from key file:  ${key_pub:-UNREADABLE (not a PEM key, or has a passphrase?)}"
        if [ -z "$cert_pub" ] || [ -z "$key_pub" ]; then say "  -> cannot compare: the proxy will not load an unreadable file either"
        elif [ "$cert_pub" = "$key_pub" ]; then say "  -> certificate and key MATCH"
        else say "  -> certificate and key DO NOT MATCH: the proxy will refuse to start"; fi
        openssl x509 -in "$CERT" -noout -subject -startdate -enddate 2>&1 | sed 's/^/  /'
        openssl x509 -in "$CERT" -noout -ext subjectAltName 2>&1 | sed 's/^/  /'
        if [ -n "$HOST" ] && ! openssl x509 -in "$CERT" -noout -ext subjectAltName 2>/dev/null \
            | tr ',' '\n' | sed 's/.*://' | grep -qx "$HOST"; then
            say "  -> KB_PUBLIC_HOST '$HOST' is not among the certificate's names"
        fi
    fi
fi
[ -d tls ] && { say "tls/:"; ls -la tls | sed 's/^/  /'; }

section "Containers"
$COMPOSE --profile prod ps -a --format '{{.Service}}  {{.Status}}  {{.Ports}}' 2>&1
for svc in caddy kb-mcp; do
    id=$($COMPOSE --profile prod ps -a -q "$svc" 2>/dev/null | head -n 1)
    [ -n "$id" ] && docker inspect "$id" \
        --format "$svc: restarts={{.RestartCount}} exit={{.State.ExitCode}} error={{.State.Error}}" 2>&1
done

section "Proxy (caddy) log: warnings and errors among the last 200 lines"
# Routine startup lines are left out; what is left says why the proxy stopped, if it did.
$COMPOSE --profile prod logs --no-color --tail 200 caddy 2>&1 | grep -v '"level":"info"' \
    | grep -v 'not formatted\|admin endpoint disabled' | uniq -c | tail -n 15 | cut -c1-400

section "Search server (kb-mcp) log, last 25 lines, auth lines left out"
$COMPOSE --profile prod logs --no-color --tail 200 kb-mcp 2>&1 | grep -v '"auth\.' | tail -n 25 | cut -c1-400

section "Indexed documents"
$COMPOSE exec -T db psql -U postgres -d kb -tA \
    -c "select 'documents: ' || count(*) from documents" \
    -c "select 'chunks:    ' || count(*) from chunks" 2>&1 | head -n 4

section "Ports 80 and 443 on this host"
# Which program uses each port, in words. `ss -p` cannot name a process owned by another user
# without sudo, and Docker's port forwarder runs as root, so the owner is worked out from
# Docker instead: a published port belongs to the container that publishes it, and a port
# that is listening but published by no container belongs to something outside Docker.
caddy_id=$($COMPOSE --profile prod ps -a -q caddy 2>/dev/null | head -n 1)
caddy_name=$( [ -n "$caddy_id" ] && docker inspect -f '{{.Name}}' "$caddy_id" 2>/dev/null | sed 's#^/##')
for p in 80 443; do
    if [ "$p" = "$PORT" ]; then need="needed by this service"; else need="not needed in this mode"; fi
    holders=$(docker ps --filter "publish=$p" --format '{{.Names}}' 2>/dev/null | tr '\n' ' ' | sed 's/ $//')
    listening=""
    command -v ss >/dev/null 2>&1 && listening=$(ss -ltnH "sport = :$p" 2>/dev/null)
    if [ -n "$caddy_name" ] && printf '%s\n' $holders | grep -qx "$caddy_name"; then
        say "port $p: used by this service's proxy ($need)"
    elif [ -n "$holders" ]; then
        say "port $p: used by another container: $holders ($need)"
    elif [ -n "$listening" ]; then
        say "port $p: used by a program outside Docker ($need); sudo ss -ltnp 'sport = :$p' shows which"
    else
        say "port $p: free ($need)"
    fi
done

section "Requests from this host"
if command -v getent >/dev/null 2>&1 && [ -n "$HOST" ]; then
    say "$HOST resolves to: $(getent hosts "$HOST" | awk '{print $1}' | tr '\n' ' ')"
fi
# -k: these test whether anything answers, not whether the certificate is trusted, so a
# certificate problem does not hide a connection one. The verified request comes after.
for target in localhost ${HOST:+"$HOST"}; do
    out=$(curl -k -sS --max-time 5 -o /dev/null -w '%{http_code}' "$SCHEME://$target/health" 2>&1 | tr '\n' ' ')
    say "$SCHEME://$target/health -> $out"
done
if [ "$SCHEME" = https ] && [ -n "$HOST" ] && [ -f "$CERT" ]; then
    out=$(curl --cacert "$CERT" -sS --max-time 5 -o /dev/null -w '%{http_code}' "https://$HOST/health" 2>&1 | tr '\n' ' ')
    say "https://$HOST/health trusting TLS_CERT -> $out"
fi

section "GPUs"
if command -v nvidia-smi >/dev/null 2>&1; then
    nvidia-smi --query-gpu=index,name,memory.used,memory.total,compute_mode,mig.mode.current \
        --format=csv 2>&1
else
    say "nvidia-smi not found"
fi

section "Done"
say "Send the whole output. It contains no passwords, tokens or private key."
