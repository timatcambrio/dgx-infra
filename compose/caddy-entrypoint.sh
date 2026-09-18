#!/bin/sh
# Entrypoint for the `caddy` service (brief §7.2): builds the `/kb/*` bearer-token
# matcher from $KB_TOKENS before starting Caddy proper.
#
# Caddyfile syntax has no loop construct and no way to turn a comma-separated env var
# into a set of alternatives at load time -- `{$VAR}` substitution is a literal string
# swap, not a template language. So this script does the one piece of templating needed:
# turn "t1,t2,t3" into a regular expression "^Bearer (t1|t2|t3)$" and substitute it for
# the __KB_TOKEN_PATTERN__ placeholder in /etc/caddy/Caddyfile, in place, before `caddy
# run` reads it. Everything else in the Caddyfile is static.
#
# If KB_TOKENS is empty, the pattern matches nothing (a regex that cannot match any
# string) rather than nothing at all -- an empty alternation in Caddy's regex engine (RE2)
# is invalid, so /kb/* would 401 every request, which is the safe failure mode: no token
# configured means no one gets in, matching kb-mcp's own --allow-anonymous refusal.
set -eu

# The Caddyfile is bind-mounted read-only (docker-compose.yml), so it is copied to a
# writable location before `sed -i` touches it -- `sed -i` renames a temp file over the
# target, which a read-only mount refuses.
cp /etc/caddy/Caddyfile /tmp/Caddyfile
CADDYFILE=/tmp/Caddyfile

escape_regex() {
	# Escape RE2 metacharacters in one token. Tokens are operator-chosen secrets, not
	# attacker input, but this keeps a token containing e.g. "." or "+" from being
	# misinterpreted as regex syntax.
	printf '%s' "$1" | sed -e 's/[.^$*+?()[\]{}|\\]/\\&/g'
}

pattern='\x00NO-TOKENS-CONFIGURED\x00'  # unmatchable (HTTP headers cannot carry NUL bytes) -- the empty-KB_TOKENS case
if [ -n "${KB_TOKENS:-}" ]; then
	escaped=""
	old_ifs=$IFS
	IFS=','
	for token in $KB_TOKENS; do
		[ -n "$token" ] || continue
		e=$(escape_regex "$token")
		if [ -z "$escaped" ]; then
			escaped="$e"
		else
			escaped="$escaped|$e"
		fi
	done
	IFS=$old_ifs
	if [ -n "$escaped" ]; then
		pattern="$escaped"
	fi
fi

sed -i "s#__KB_TOKEN_PATTERN__#^Bearer ($pattern)\$#" "$CADDYFILE"

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
