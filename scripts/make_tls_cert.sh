#!/bin/sh
# Make a self-signed certificate for the TLS_CERT/TLS_KEY path, with the right names on it.
#
# WHY THIS EXISTS. The stack has two TLS paths and only one of them had ever been run.
# Unset TLS_CERT/TLS_KEY gives `tls internal`, Caddy's own local CA -- the default, and
# what the remote test of 2026-10-01 used. Set, they are HOST paths bind-mounted into the
# caddy container and used instead (compose/caddy-entrypoint.sh picks the shape of the
# `tls` line from whether both are set). That second path is the one the client will
# actually use, because they issue from their own internal CA, and nothing had exercised
# it: a missing file, a key the container cannot read, or a certificate without the right
# names all fail at container start or at the handshake, none of which `make check` sees.
#
# THE NAMES ARE THE PART THAT GOES WRONG. compose/Caddyfile serves ONE site block for
# three names -- $KB_PUBLIC_HOST, localhost and 127.0.0.1 -- and the global `default_sni`
# names $KB_PUBLIC_HOST for clients that send no SNI at all (a client dialling by IP;
# see tests/test_compose_tls.py for the handshake failure that taught us). With `tls
# internal` Caddy issues a certificate per name on demand and the question never arises.
# With one supplied certificate, every name a client might dial has to be ON it or that
# client fails to verify -- so this script puts all of them there, including the host's
# own IP as an IP SAN, which a DNS SAN does not cover.
#
#   sh scripts/make_tls_cert.sh <public-host> [extra-name-or-ip ...]
#
# e.g.  sh scripts/make_tls_cert.sh ec2-1-2-3-4.compute-1.amazonaws.com 1.2.3.4
#
# Writes cert.pem (the certificate, also the CA bundle a client verifies against, since it
# is self-signed) and key.pem into ./tls/, prints the two .env lines, and prints nothing
# secret -- the key is a file, never stdout.
#
# This is a TEST certificate. It is self-signed, so every client must be given cert.pem to
# trust, exactly as `tls internal` requires its root CA to be trusted. It stands in for the
# client's internal CA in order to exercise the code path, and it is not a substitute for
# one in production.

set -eu

PUBLIC_HOST=${1:-}
[ -n "$PUBLIC_HOST" ] || {
    printf 'usage: %s <public-host> [extra-name-or-ip ...]\n' "$0" >&2
    printf '  <public-host> must be the SAME value as KB_PUBLIC_HOST in .env -- the address\n' >&2
    printf '  clients dial. A certificate for a name nobody dials fails verification.\n' >&2
    exit 2
}
shift

# Every name must be bare: no scheme, port, path or space. Measured 2026-10-13: given
# `https://<ip>`, openssl wrote a new key, then refused the name and wrote no certificate,
# leaving a pair Caddy will not load. Refused here, before anything is written.
for name in "$PUBLIC_HOST" "$@"; do
    case $name in
        *[!A-Za-z0-9.-]*|'')
            printf '%s is not a bare host name or IP address. Give the name alone, with no\n' "'$name'" >&2
            printf 'https://, port or trailing /, e.g. 10.20.30.40 or dgx01.example.org.\n' >&2
            printf 'Nothing was written.\n' >&2
            exit 2 ;;
    esac
done

command -v openssl >/dev/null 2>&1 || {
    printf 'no openssl on this host. Every Ubuntu and macOS image ships one; if this is a\n' >&2
    printf 'minimal image, generate the pair in a container instead:\n' >&2
    printf '  docker run --rm -v "$PWD/tls:/out" -w /out alpine:3 \\\n' >&2
    printf '    sh -c "apk add --no-cache openssl >/dev/null && openssl req -x509 ..."\n' >&2
    exit 2
}

HERE=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
OUT="$HERE/tls"
mkdir -p "$OUT"

# Docker creates a missing bind-mount source as an empty directory, owned by root. That is
# what `tls/cert.pem` becomes if the stack was started with TLS_CERT set before this script
# ever ran, and openssl cannot write over it.
for f in "$OUT/cert.pem" "$OUT/key.pem"; do
    if [ -d "$f" ]; then
        printf '%s is a directory, not a file. Docker creates one when the stack starts\n' "$f" >&2
        printf 'with TLS_CERT/TLS_KEY pointing at a file that does not exist yet. Remove it\n' >&2
        printf '(sudo rmdir %s) and run this again. Nothing was written.\n' "$f" >&2
        exit 2
    fi
done

# Every name the Caddyfile's site block answers for, plus whatever else was asked for.
# `localhost`/`127.0.0.1` are not padding: the deployment is probed from the host itself
# before anyone dials it by name, and scripts/http_probe.py is routinely pointed at
# https://localhost.
DNS_NAMES="$PUBLIC_HOST localhost"
IP_NAMES="127.0.0.1"
for extra in "$@"; do
    # An IP SAN and a DNS SAN are different fields and a client checks the one matching
    # what it dialled, so the two cannot be conflated.
    if printf '%s' "$extra" | grep -qE '^[0-9]+(\.[0-9]+){3}$'; then
        IP_NAMES="$IP_NAMES $extra"
    else
        DNS_NAMES="$DNS_NAMES $extra"
    fi
done

SAN=""
for n in $DNS_NAMES; do SAN="${SAN:+$SAN,}DNS:$n"; done
for n in $IP_NAMES;  do SAN="${SAN:+$SAN,}IP:$n"; done

printf 'subjectAltName = %s\n' "$SAN" >&2

# The pair is written under temporary names and moved into place only once both exist and
# match, so a failure part way leaves whatever pair was there before untouched. openssl
# writes the key first; writing it straight to key.pem is what produced the mismatch.
NEW_KEY="$OUT/.key.pem.new"
NEW_CERT="$OUT/.cert.pem.new"
ERR="$OUT/.openssl.err"
trap 'rm -f "$NEW_KEY" "$NEW_CERT" "$ERR"' EXIT

# -nodes: no passphrase. Caddy starts unattended and there is nobody to type one.
# 825 days is the CA/Browser-Forum maximum a public client will accept; irrelevant to a
# self-signed test cert but it costs nothing to stay inside what clients tolerate.
# openssl's stderr is kept rather than discarded: it is the only explanation of a failure.
if ! openssl req -x509 -newkey rsa:2048 -nodes -sha256 -days 825 \
    -keyout "$NEW_KEY" -out "$NEW_CERT" \
    -subj "/CN=$PUBLIC_HOST" \
    -addext "subjectAltName = $SAN" \
    -addext "basicConstraints = critical,CA:TRUE" \
    2>"$ERR"; then
    printf '\nopenssl could not make the certificate:\n\n' >&2
    cat "$ERR" >&2
    printf '\nThe existing files in %s were left as they were.\n' "$OUT" >&2
    exit 1
fi

cert_pub=$(openssl x509 -in "$NEW_CERT" -noout -pubkey 2>/dev/null) || cert_pub=""
key_pub=$(openssl pkey -in "$NEW_KEY" -pubout 2>/dev/null) || key_pub=""
if [ -z "$cert_pub" ] || [ "$cert_pub" != "$key_pub" ]; then
    printf 'openssl produced a certificate and key that do not match. The existing files in\n' >&2
    printf '%s were left as they were.\n' "$OUT" >&2
    exit 1
fi

# The caddy image runs as root, so 0600 is readable by it through the bind mount. Keep it
# narrow anyway: this is a private key on a host that may have other logins.
chmod 600 "$NEW_KEY"
chmod 644 "$NEW_CERT"
mv -f "$NEW_KEY" "$OUT/key.pem"
mv -f "$NEW_CERT" "$OUT/cert.pem"

printf '\nwrote %s\n' "$OUT/cert.pem" >&2
printf 'wrote %s (mode 0600)\n\n' "$OUT/key.pem" >&2
printf 'Add these two lines to .env, then `make compose-up`:\n\n' >&2
printf '  TLS_CERT=%s\n' "$OUT/cert.pem"
printf '  TLS_KEY=%s\n' "$OUT/key.pem"
printf '\nAnd give the probe the same certificate as its CA bundle:\n\n' >&2
printf '  --ca %s\n' "$OUT/cert.pem"
