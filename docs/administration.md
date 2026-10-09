# Administration

Reference for running the service once it is installed: starting it, reindexing, managing
credentials and certificates, and checking that a deployment works. To install it on the
DGX, follow the [deployment guide](deployment-dgx-guide.md) step by step; its
[Day to day](deployment-dgx-guide.md#day-to-day) table lists the routine tasks.

## Setting up the stack on a fresh host

The deployment is container-first: `docker compose` and nothing else. Getting a bare Linux
host to that point needs three host packages (Docker Engine, the compose plugin, and the
NVIDIA Container Toolkit). `scripts/bootstrap_host.sh` installs them, then verifies
GPU passthrough from inside a throwaway container. It deliberately does **not** install
the NVIDIA kernel driver (see the script's header for why): `nvidia-smi` must already
work, which on a cloud host is the image's job.

```bash
sh scripts/bootstrap_host.sh          # install what is missing, then verify
sh scripts/bootstrap_host.sh --check  # verify only
```

It is idempotent and safe to re-run. Set `KB_GPU=on` in `.env` on a host that must use its
GPUs, so that a missing one stops the stack instead of letting it index slowly and
silently on the CPU; `make gpu-check` reports which way the decision goes without starting
anything.

That covers host preparation. For the full deployment in order (the two directories
whose ownership requirements are opposite, the certificate, the credentials, converting the
documents in a container, indexing, and the end-to-end probe), follow
[`docs/deployment-dgx.md`](deployment-dgx.md).

## Starting the stack

The service runs under Docker Compose: Postgres (`db`), the embedding server (`ollama`),
the MCP server (`kb-mcp`), a static file server for the converted markdown (`kb-static`)
and Caddy in front of them on 443. On a host that already has Docker, the order is:

```bash
cp .env.example .env               # then set KB_PATH (absolute), KB_PUBLIC_HOST, KB_URL_BASE
sudo install -d -o 10001 -g 10001 -m 700 /srv/kb    # the token store's directory, once
make token ARGS="issue you@example.com"             # prints your bearer token ONCE
make compose-up                    # builds and starts db, ollama, kb-mcp, kb-static, caddy
docker compose -f compose/docker-compose.yml exec ollama ollama pull nomic-embed-text
make compose-index ARGS=--init     # applies the database schema (first run only)
make compose-index                 # walks kb/, embeds it, loads it into Postgres
```

The `ollama pull` line downloads the embedding model. It is a separate, manual, one-time
step: `make compose-up` does not do it, because a model download should never happen
without someone asking for it.

Issue the first token **before** `make compose-up`, not after: `kb serve` refuses to start
an unauthenticated HTTP server, so with an empty store `kb-mcp` exits 2 and Compose keeps
restarting it (Caddy answering 502 meanwhile) until a credential exists. It recovers on its
own once one does, but issuing the token first avoids the restart loop. `make token` builds
what it needs, so it works before anything is up.

`KB_PATH` must be an absolute path here: the compose stack bind-mounts it, and a relative
path would be resolved against the `compose/` directory rather than this one. `make
compose-up` checks and refuses otherwise.

`KB_URL_BASE` is the **site root**, not the `/kb` path: `https://kb.internal.example`,
with no `/kb` on the end. Both halves of a citation url already supply that segment (the
indexer stores each document as `kb/<file>.md`, and Caddy routes `/kb/*`), so a value
ending in `/kb` makes every citation url a 404. `make compose-up` refuses one.

`install -d` is the one host command this deployment needs, and it is needed once. It
creates the token store's directory owned by the uid the containers run as (10001), so
`make token` can write the store and `kb-mcp` can read it. Without it the store ends up
owned by whoever ran the command, mode 0600, and the server (which is not root) cannot
read a single record: it exits 2, the container restarts in a loop, and the only symptom
visible from outside is Caddy answering 502. `make compose-up` checks for this before
starting anything and prints the command to run.

Then add the server to an assistant using `https://<KB_PUBLIC_HOST>/mcp` and the token
`make token` printed: see [Use it from Codex](search-and-mcp.md#use-it-from-codex) and
[Use it from Claude Code](search-and-mcp.md#use-it-from-claude-code).

## GPUs

Docker gives a container a GPU only when asked, so `make compose-up` requests
one for you. Before starting anything it checks whether this host has NVIDIA GPUs and
whether Docker can pass them through, and if both are true it reserves all of them for
`ollama`; otherwise it starts on the CPU. Either way it prints which it chose. Run the
check on its own with:

```bash
make gpu-check
```

Set `KB_GPU` in `.env` to override the choice. `off` keeps embedding on the CPU. `on`
makes the GPUs a requirement, so a deployment that is meant to have them refuses to start
instead of silently running many times slower. The default, `auto`, is the detection just
described. Passing GPUs through needs the NVIDIA Container Toolkit installed on the host;
without it the check finds nothing to use and says so.

The GPU speeds up indexing, and only the first run over a document set is slow. An embedding
model fits on a single GPU, so a second and third card do not divide that work further; they
help with a larger embedding model and with serving several requests at once.

## Indexing: `kb index`

On the compose stack run it as `make compose-index`, passing flags with `ARGS`
(`make compose-index ARGS=--force`). From a development venv it is `uv run kb index`.

Walks every `kb/*.md` file, embeds it with the local `ollama` model named by `EMBED_MODEL`
(needs `ollama pull nomic-embed-text` and `ollama serve` reachable at `OLLAMA_BASE_URL`),
and loads it into Postgres. It is idempotent and safe to re-run: a document whose markdown
file has not changed since the last run is left alone, so running it again after adding one
new document only embeds that one document.

It prints one summary line:

```
3 unchanged, 1 reindexed, 0 deleted, 0 errors
```

`unchanged`: files whose bytes match the last indexed copy, skipped. `reindexed`: files
that were new or had changed, parsed and reloaded. `deleted`: documents that were indexed
before but whose file is now gone, removed from the database. `errors`: files that failed
to parse (bad frontmatter, a sidecar that does not match, a missing block); the file is
reported and skipped, and whatever was indexed for it before is left in place rather than
being silently dropped. A run with any errors exits non-zero.

Two flags change what counts as "changed":

- `--force` reindexes every document regardless of whether its file changed. Use it after
  editing `retrieval/chunk.py`'s constants or anything else that changes how a document is
  cut, without touching the source files themselves.
- `--reindex-all` additionally **empties** the index first (documents, blocks, sections and
  chunks, but not the roles or the schema) and updates the recorded embedding model. Use it
  after changing `EMBED_MODEL` or `EMBED_DIM` in `.env`: mixing vectors from two different
  models in the same table would make search meaningless, so `kb index` refuses to run
  and names both the old and new model until you pass this flag.

## What the `kb` keys in `.env` mean

`.env.example` documents every key `kb` reads, each with a one-line comment. The two to get
right: `DATABASE_URL_INDEX` (the writer role `kb index` uses) and
`DATABASE_URL` (the read-only role `kb serve`/`kb search` use). `docker compose --profile
dev up -d db` creates a local Postgres with both roles already set up, matching the defaults
in `.env.example`.

## Removing documents

When a source document is deleted, `make inventory` marks its entry `MISSING`. `make prune`
lists what would be removed; `make prune ARGS=--yes` removes the entries and their `kb/`
files, and the next `kb index` drops the matching database rows. See
[Things that stop and ask](conversion.md#things-that-stop-and-ask).

## Tokens: issuing, revoking, rotating

One token per user. `kb token` manages them; nothing here restarts the server.

**On the compose stack, use `make token`.** The deployment target has Docker and
nothing else, so `uv run` is not available there:

```bash
make token ARGS="issue alice@example.com"        # prints the token ONCE
make token ARGS='issue bob@example.com --note "bob laptop" --expires-in-days 90'
make token ARGS=list                             # ids, owners, status (never the secrets)
make token ARGS="list --all"                     # include revoked and expired
make token ARGS="revoke 1979317c8685"            # withdraw one person's access, now
```

That runs the `kb-token` one-off container, built from the same image as the server and so
running as the same uid, which keeps the store it writes readable by `kb-mcp`
without widening mode 0600 or chowning anything. It deliberately does not depend on
Postgres: tokens are stored in a file so that authentication survives the database being down,
and revoking one has to work during that outage.

From a development host venv the same commands are:

```bash
uv run kb token issue alice@example.com          # prints the token ONCE
uv run kb token list
uv run kb token revoke 1979317c8685
```

Either way the store keeps whatever owner and mode it already had: `kb token` writes a new
file and renames it over the old one, and it keeps the previous file's ownership,
so a one-time `chown` is not undone by the next `issue` or `revoke`.

**Revoking a token affects only that user.** The records are stored in `KB_TOKENS_FILE`
(default: `tokens.json` beside `.env`, gitignored, mode 0600) and the server re-reads that
file as it changes, so a revocation is in force within `KB_TOKEN_CACHE_SECONDS` (default 5)
on both `/mcp*` and `/kb/*`, with no restart and nothing required of any other user. This
is why the token store exists: with tokens in `.env`, withdrawing one credential meant
restarting the server, which disconnected everybody, so in practice it was never done.

**On the compose stack, also set `KB_TOKENS_DIR`**, the directory holding that file.
It is what gets bind-mounted into `kb-mcp`, and it has to be the directory rather than the
file: a single-file bind mount binds the host file's inode, and `kb token` replaces the
inode on every write, so the container would lose the file on the first issue or revoke.
`make compose-up` checks that the two settings agree before starting anything, because the
failure is otherwise silent: `kb token revoke` would report success and change nothing the
server could see.

**That directory has to be owned by uid 10001**, the uid the containers run as. The
`install -d` line in [Starting the stack](#starting-the-stack) creates it that way, and
`make compose-up` checks both the directory and the store before starting anything.

**The secret is shown once and is not stored.** Only a sha256 of it is, so the file is not
a credential: read access to it does not yield anyone's token. If a token is lost, issue
another and revoke the old one; there is no recovery, by design.

**Rotating**, if you want to rotate on a calendar rather than on an incident: issue the
replacement, give it to its owner, then revoke the old one. Both work in between, so there
is no flag day. `--expires-in-days` closes that window for you. Note that this rotates the
*server's* record. The secret still sits in a config file on the user's machine, which the
server cannot fix. The options there are to rotate on incident, push the credential as a
managed setting, or move to OAuth.

**Audit log.** Every authentication decision is one JSON line on `kb-mcp`'s stderr,
beside the tool-call log: `{"event": "auth.ok", "path": "/mcp", "token": "...", "user":
"alice@example.com", "source": "file"}`, or `auth.denied` with no user. Because each user
has their own token, the log records *who* made a call, which a shared token could not.

**`KB_TOKENS` still works, but do not use it.** Tokens listed there are accepted for
compatibility with an existing deployment, but they are plaintext in `.env`, they name no
owner (so the audit log says `(KB_TOKENS)`), and revoking one still means editing `.env`
and restarting, which disconnects every user. `kb serve` warns on startup when it is set,
and `kb token list` says how many are in play. To migrate: issue a token per user, hand them
out, then remove `KB_TOKENS` from `.env` and restart once.

### How `/kb/*` is checked

`kb-mcp` checks `/mcp*` itself. `/kb/*` is served by `kb-static`, which is a plain
`file_server` with no auth, so Caddy enforces the token in front of it by asking
`kb-mcp` (`forward_auth` to an internal `/auth/check`, which answers 204 or 401) rather
than matching the token itself, so one live store covers both routes and a revocation
takes effect on `/kb/*` as quickly as on `/mcp*`. `/auth/check` is
reachable only inside the compose network; the Caddyfile answers 404 for every path it
does not route.

## TLS

`compose/Caddyfile` defaults to `tls internal`: Caddy generates its own local CA and a leaf
certificate for `KB_PUBLIC_HOST` the first time it starts, and terminates HTTPS with it.
Nothing else has to be configured for the stack to serve valid-looking HTTPS, but every
user's machine has to be told to trust that CA once, or their assistant/browser will reject
the connection as self-signed. Fetch the CA certificate from the running container and
install it as a trusted root using your OS's normal process for that:

```bash
docker compose -f compose/docker-compose.yml cp caddy:/data/caddy/pki/authorities/local/root.crt ./dgx-kb-ca.crt
```

**Clients have to connect by the name in `KB_PUBLIC_HOST`.** TLS SNI carries host names
only, so a client dialling the stack by bare IP sends none. With no name, Caddy would fail
the handshake outright (`tlsv1 alert internal error`, no certificate offered), before any
HTTP and so before `KB_PUBLIC_HOST` or the token were involved. The Caddyfile therefore sets
`default_sni` to `KB_PUBLIC_HOST`, so an SNI-less client is served that certificate:
dialling by IP works when `KB_PUBLIC_HOST` **is** that IP, and otherwise clients must use
the name (DNS, or a `hosts` entry), since a certificate for `kb.internal.example` will not
validate against `https://10.0.0.5`.

Alternatively, set `TLS_CERT` and `TLS_KEY` in `.env` to the paths of a certificate/key
pair issued by a CA your users' machines already trust (an internal corporate CA, or a
client-issued cert). `compose/caddy-entrypoint.sh` uses that pair instead of `tls
internal` whenever both are set, and no user-side trust step is needed.

**You must put the right names on a supplied certificate.** With `tls internal` Caddy issues
a certificate per name on demand, so the set of names it answers for is open. With one
supplied pair that set is fixed when the certificate is made, and a client dialling a name
that is not on it fails verification. That is a different failure, at a different layer,
from the SNI one above. The Caddyfile serves one site block for `KB_PUBLIC_HOST`,
`localhost` and `127.0.0.1`, so all of those (plus the host's own IP, as an IP SAN, which a
DNS SAN does not cover) belong on it. To test this path without an internal CA,
`scripts/make_tls_cert.sh <public-host> [extra-name-or-ip ...]` writes a self-signed pair
with exactly those names on it and prints the two `.env` lines. That certificate is for
testing the path, not for production. Both options work without any code change.

## Checking a deployment end to end

`make check` tests the code. It cannot test the certificate, the reverse proxy, the
`/kb/*` forward_auth hop or the live token store, because none of those exist in a test
process. `scripts/http_probe.py` drives a *running* stack over TLS at its public address,
the way a client on the LAN reaches it:

```bash
uv run python scripts/http_probe.py \
    --base-url https://kb.internal.example \
    --ca ./dgx-kb-ca.crt \
    --tokens-file /srv/kb/tokens.json
```

It checks what is open and what is closed (`/health` without a token, `/mcp` and `/kb/*`
with and without one, `/auth/check` unreachable from outside), drives MCP over streamable
HTTP through `initialize` → `search` → `fetch`, follows the citation url it gets back to
confirm the cited document is fetchable, and, given `--tokens-file` so it can reach the
store, revokes a throwaway token and confirms that closes **both** `/mcp*` and
`/kb/*` within `KB_TOKEN_CACHE_SECONDS` while another user's token keeps working. It
issues its own throwaway credentials, revokes them on the way out, and never prints a
secret. Exit status is 0 only if every check passed.

Without `--tokens-file` it runs the read-only checks from any client machine, taking two
issued tokens as `KB_PROBE_TOKEN` and `KB_PROBE_TOKEN_2`, and skips the revocation checks.
