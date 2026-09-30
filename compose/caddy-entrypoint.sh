#!/bin/sh
# Entrypoint for the `caddy` service (brief §7.2): fills in the one Caddyfile directive
# that cannot be written with `{$VAR}` substitution -- the `tls` line, whose *shape*
# differs between the two TLS paths.
#
# It used to also build a bearer-token matcher for /kb/* out of $KB_TOKENS. That is gone:
# /kb/* now asks kb-mcp's /auth/check per request (see compose/Caddyfile), so the token
# list lives in exactly one place and revoking a token no longer needs this container
# restarted. $KB_TOKENS is not read here at all any more.
set -eu

# The Caddyfile is bind-mounted read-only (docker-compose.yml), so it is copied to a
# writable location before `sed -i` touches it -- `sed -i` renames a temp file over the
# target, which a read-only mount refuses.
cp /etc/caddy/Caddyfile /tmp/Caddyfile
CADDYFILE=/tmp/Caddyfile


# TLS (brief §7.4): a client-issued cert/key pair (both TLS_CERT and TLS_KEY set, and
# bind-mounted by docker-compose.yml into /etc/caddy/tls/) takes precedence; otherwise
# Caddy's own local CA (`tls internal`, the dev/default path).
if [ -n "${TLS_CERT:-}" ] && [ -n "${TLS_KEY:-}" ]; then
	tls_directive="/etc/caddy/tls/cert.pem /etc/caddy/tls/key.pem"
else
	tls_directive="internal"
fi
sed -i "s#__TLS_DIRECTIVE__#$tls_directive#" "$CADDYFILE"

exec caddy run --config "$CADDYFILE" --adapter caddyfile
