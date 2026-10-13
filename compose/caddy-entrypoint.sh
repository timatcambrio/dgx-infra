#!/bin/sh
# Entrypoint for the `caddy` service: fills in the two parts of the Caddyfile that
# cannot be written with `{$VAR}` substitution -- the `tls` line, whose *form* differs
# between the TLS paths and which must be absent altogether for plain HTTP, and the
# scheme in front of each site name.
#
# It used to also build a bearer-token matcher for /kb/* out of $KB_TOKENS. That is gone:
# /kb/* now asks kb-mcp's /auth/check per request (see compose/Caddyfile), so the token
# list is kept in exactly one place and revoking a token no longer needs this container
# restarted. $KB_TOKENS is not read here at all any more.
set -eu

# The Caddyfile is bind-mounted read-only (docker-compose.yml), so the filled-in copy is
# written to a writable location. Plain `sed src > out`, not `sed -i`: `-i` renames a temp
# file over the target, which a read-only mount refuses, and its argument differs between
# busybox and BSD sed. The two KB_CADDYFILE_* overrides exist only so
# tests/test_compose_http_mode.py can run this script outside the container; nothing in
# the stack sets them.
SOURCE=${KB_CADDYFILE_SRC:-/etc/caddy/Caddyfile}
CADDYFILE=${KB_CADDYFILE_OUT:-/tmp/Caddyfile}

# Plain HTTP (KB_TLS=off): every site name gets `http://`, which makes Caddy serve it on
# port 80 with no certificate, and the `tls` line is deleted, because Caddy refuses a
# `tls` directive in a site it serves over HTTP. A certificate set alongside it is refused
# here too, rather than silently ignored: someone set it expecting HTTPS.
if [ "${KB_TLS:-on}" = "off" ]; then
	if [ -n "${TLS_CERT:-}" ] || [ -n "${TLS_KEY:-}" ]; then
		echo "KB_TLS=off serves plain HTTP, but TLS_CERT/TLS_KEY are set. Remove one or the other from .env." >&2
		exit 2
	fi
	sed -e "s#__SCHEME__#http://#g" -e "/__TLS_DIRECTIVE__/d" "$SOURCE" >"$CADDYFILE"
else
	# TLS: a client-issued cert/key pair (both TLS_CERT and TLS_KEY set, and
	# bind-mounted by docker-compose.yml into /etc/caddy/tls/) takes precedence;
	# otherwise Caddy's own local CA (`tls internal`, the dev/default path).
	if [ -n "${TLS_CERT:-}" ] && [ -n "${TLS_KEY:-}" ]; then
		tls_directive="/etc/caddy/tls/cert.pem /etc/caddy/tls/key.pem"
	else
		tls_directive="internal"
	fi
	sed -e "s#__SCHEME__##g" -e "s#__TLS_DIRECTIVE__#$tls_directive#" "$SOURCE" >"$CADDYFILE"
fi

exec caddy run --config "$CADDYFILE" --adapter caddyfile
