# Deploying on the DGX, end to end

Fourteen steps, in order, most saying what they prove and where it has gone wrong before.
Everything here has been run: the full stack came up on a fresh GPU host on 2026-10-06 and
the steps below are that procedure with the cloud-specific parts removed and the
document-conversion step added, since on the DGX the documents are on the box.

**What this assumes.** One Linux host with NVIDIA GPUs and a working driver, a login user
with `sudo`, a host name clients can resolve, and inbound TCP 443 permitted from the
networks the users sit on. Nothing else is required of the network. What IT needs to know
about the installation is a separate document; this one is the operator's.

**Container-first.** The only host packages installed are Docker, its compose plugin and
the NVIDIA Container Toolkit, by one script in step 3. Everything after that runs in
containers: converting documents, applying the database schema, indexing, issuing
credentials, serving. There is no Python on the host, and no step below asks for one. The probe in step 13 runs from a machine with `uv` and a
checkout of this repository; that machine is not the host.

---

## 0. Before you start

| | |
|---|---|
| `nvidia-smi` works and lists at least one GPU | step 1 refuses otherwise; the driver is not ours to install |
| ~150 GB free on the filesystem holding `/var/lib/docker` | images, the database volume and the model volume all land there |
| A host name clients will dial, resolvable by them | the certificate is issued for a name, and a client dialling a bare address fails verification unless the certificate carries that address |
| Inbound 443/tcp from the user networks | the only port anything in the stack publishes |
| The login user can `sudo` | two commands need it, the bootstrap script and one `install -d`; step 5's repair `chown` is a conditional third |

Two things you will want before step 7 and cannot produce yourself: a certificate and key
from the organisation's own CA for that host name, and the list of people who get access.
Step 7 says what to do if the certificate is not ready yet.

## 1. Check the host

```bash
nvidia-smi -L
df -h /var/lib/docker
```

**Expect** at least one GPU listed. If `nvidia-smi` is missing or lists none, stop here and
have the driver installed. `scripts/bootstrap_host.sh` deliberately does not install a
kernel driver (its header says why), and the stack is configured in step 4 to refuse to
start without a usable GPU rather than index slowly and silently on the CPU.

## 2. Get the code

```bash
git clone https://github.com/timatcambrio/dgx-infra.git
cd dgx-infra
```

If the host cannot reach GitHub, copy a clone over instead, by `git archive`, `scp`, or a
tarball of the working tree. Nothing in the deployment reads git history; the files are
what matter.

Clone the content repository beside it. It holds the converted markdown, and the
deployment's copy of it is the one the server serves:

```bash
git clone https://github.com/timatcambrio/dgx-knowledge.git ~/dgx-knowledge
```

Its `kb/` directory is deliberately not in version control, so a fresh clone contains no
documents. Step 11 fills it.

## 3. Bootstrap the host

```bash
sh scripts/bootstrap_host.sh
```

Installs Docker Engine, the compose plugin and the NVIDIA Container Toolkit, each only if
missing, then **verifies GPU passthrough from inside a throwaway container**. That last
step is the point: a host whose own `nvidia-smi` works while a container's does not is a
half-configured state nothing on the host reveals.

It fetches from Docker's and NVIDIA's apt repositories, and pulls one small CUDA image for
the check. If the host reaches a proxy or an internal mirror rather than the internet
directly, the usual apt and Docker proxy configuration applies; the script uses plain apt and HTTPS
downloads.

If it says it added you to the `docker` group, **log out and back in** before continuing.
Group membership is read at login, and every `make` target below would otherwise need
`sudo`.

**Expect** it to end at `== Ready`. It is safe to re-run, with or without `--check`.

## 4. The environment file

```bash
cp .env.example .env
```

Set these (the example file has them commented out; remove the `#`):

```bash
KB_PATH=/home/<you>/dgx-knowledge
KB_PUBLIC_HOST=<the name clients dial>
KB_URL_BASE=https://<the name clients dial>
KB_TOKENS_FILE=/srv/kb/tokens.json
KB_TOKENS_DIR=/srv/kb
KB_GPU=on
EMBED_MODEL=nomic-embed-text
```

Set real passwords for `POSTGRES_PASSWORD`, `KB_INDEX_PASSWORD` and `KB_READ_PASSWORD`
too. The values in `.env.example` are development defaults, and the file is not in version
control.

Four of these have each broken a deployment, so read them rather than
copying:

- **`KB_PATH` must be absolute.** Compose resolves a relative bind-mount source against
  `compose/`, not the repository root, so a relative value silently mounts nothing. It is
  the content repository's **root**, with `kb/` inside it.
- **`KB_PUBLIC_HOST` is the address clients actually dial.** The server checks the `Host`
  header, and a mismatch is refused with an error that reads like a fault in the client.
- **`KB_URL_BASE` is the site root, with no `/kb`.** Each document is stored as
  `kb/<file>.md` and the proxy routes `/kb/*`, so both halves already supply that segment;
  a value ending in `/kb` makes every citation a dead link. `make compose-up` refuses such
  a value outright.
- **`KB_TOKENS_DIR` must be `KB_TOKENS_FILE`'s parent.** The stack mounts the directory,
  because issuing or revoking a credential replaces the file and a single-file mount would
  bind the old copy. `make compose-up` checks this.

Pin `EMBED_MODEL` to an explicit tag once you know which tags the model
publishes. A bare name means the latest published build, and months from now the same name
can hand you different weights. Every vector in the index would then come from a
different build than the one embedding queries. The indexer records the model's digest and
refuses to add to an index whose digest has changed, so this cannot happen silently, but a
pinned tag is better than a backstop.

## 5. The two directories, which have opposite requirements

This is the step that cost the most time on the last deployment, because the two
directories a deployment needs want different things and nothing said so.

**The content tree belongs to you.** Create it before any container starts:

```bash
mkdir -p /home/<you>/dgx-knowledge/kb
```

Docker creates a missing bind-mount source **as root**. The static file server mounts
`${KB_PATH}/kb`, so a `make compose-up` that runs first leaves you a root-owned directory
you cannot write and need `sudo` to clear. Both of the stack's mounts of this tree are
read-only; nothing in the stack writes it. What the containers need is only that they can
**read** it as uid 10001: world-readable files, traversable directories, which `cp`, `git`
and `rsync` give you by default. To repair it:

```bash
sudo chown -R "$(id -un)":"$(id -gn)" /home/<you>/dgx-knowledge
chmod -R a+rX /home/<you>/dgx-knowledge
```

A file in there that uid 10001 cannot read is served as a 404 with nothing naming the
permission, while the index holds it perfectly well, so the index looks right and the
citation is dead. The end-to-end probe in step 13 is the only thing in the stack that
catches this.

**The credential store belongs to uid 10001.** The opposite case, and the one host command
the container-first design leaves:

```bash
sudo install -d -o 10001 -g 10001 -m 700 /srv/kb
```

`install -d` creates the directory and sets ownership and mode in one command, which
`mkdir -p` cannot. uid 10001 is the unprivileged user inside the images; it does not need
to exist as a named account on the host, since ownership is numeric. The store is written
mode 0600, so that uid must own both the directory and the file or the server cannot read
it at all: it exits and the container restarts in a loop behind a proxy error that names
nothing. `make compose-up` checks for this and prints the command if it is missing.

## 6. Confirm the GPU decision before starting anything

```bash
make gpu-check
```

**Expect** `GPU: reserving all NVIDIA GPUs for ollama`. Anything else means stop: with
`KB_GPU=on` the stack would refuse to start anyway, and this asks the same question without
bringing containers up.

## 7. The certificate

Two supported paths, and the choice is the organisation's, not ours.

**A certificate from the organisation's CA.** Preferred, because every machine in the
organisation already trusts that CA and no user has to install anything. Put the pair on
the host and name it in `.env`:

```bash
TLS_CERT=/etc/ssl/dgx-kb/cert.pem
TLS_KEY=/etc/ssl/dgx-kb/key.pem
```

**Get the names on a supplied certificate right.** The proxy serves one site
for `KB_PUBLIC_HOST`, `localhost` and `127.0.0.1`, so all of those belong on it, plus the
host's own address as an IP entry if anyone will dial it that way. A DNS entry does not
cover an address. Only a self-signed stand-in has been tested through this path; the code
path is the same, but a real CA's certificate has not been through it.

**The proxy's own CA**, if a certificate is not available yet. Leave `TLS_CERT` and
`TLS_KEY` unset and the proxy generates its own CA and certificate on first start. The cost
is that each user's machine has to be told to trust that CA once, which does not scale and
tends to become permanent. Fetch it with:

```bash
docker compose -f compose/docker-compose.yml cp \
  caddy:/data/caddy/pki/authorities/local/root.crt ./dgx-kb-ca.crt
```

## 8. Issue the first credentials, before the stack is up

```bash
make token ARGS="issue you@example.com"
```

A one-off container built from the same image as the server, so the store it writes is
owned by the uid that reads it. **The secret prints once and is not recoverable**, so keep
this one for step 13. There is no dependency on the database: credentials live in a file
precisely so that authentication keeps working, and stays repairable, when the database
does not.

Issue a second one for the probe, so step 13 can show that withdrawing one person's access
leaves another's working:

```bash
make token ARGS="issue probe@example.com"
```

## 9. Bring the stack up and pull the embedding model

```bash
make compose-up
```

**Expect** the GPU verdict again, then five containers: the database, the model server, the
MCP server, the static file server, and the proxy. Only the proxy publishes a port.

The model is a separate, deliberate step. Nothing in the stack pulls a model by itself:

```bash
docker compose -f compose/docker-compose.yml --env-file .env \
  exec ollama ollama pull nomic-embed-text
```

About 270 MB, once, from the model registry. It lands in a named volume and survives
restarts. If the host cannot reach that registry, the model can in principle be loaded from a
file instead; that has not been exercised.

## 10. Confirm the GPU reached the container

```bash
docker compose -f compose/docker-compose.yml --env-file .env exec ollama nvidia-smi -L
```

**Expect** the cards listed. The toolkit injects the driver into the container, so
`nvidia-smi` exists there only because the reservation resolved, which is what makes this
a test and not a formality.

Whether the **model** uses a card is a separate claim and cannot be checked yet. An
embedding model has nothing to generate, so there is no way to warm it by hand; step 12
checks it right after indexing loads it.

## 11. Convert the documents

The documents go somewhere outside both repositories, and the pipeline treats that
directory as strictly read-only: it never writes, moves, renames or deletes anything under
it. Say where they are and where the markdown goes, then run four commands. Verified
2026-10-06 against this image, with no host Python, no database and no model server
involved:

```bash
SRC=/path/to/the/documents
OUT=/home/<you>/dgx-knowledge

convert() {
  docker compose -f compose/docker-compose.yml --env-file .env run --rm --no-deps \
    --user "$(id -u):$(id -g)" -e UV_CACHE_DIR=/tmp/uv-cache-"$(id -u)" \
    -v "$SRC:/sources:ro" -v "$OUT:/out:rw" \
    -e SOURCE_DIR=/sources -e KB_PATH=/out \
    kb-mcp pipeline "$@"
}

convert inventory   # find the documents and record what came from where
convert triage      # measure how much readable text each PDF has
convert convert     # write the markdown into kb/
convert report      # print what happened, and what to spot-check
```

Four things about that command's structure, each measured rather than assumed:

- **`--no-deps` matters.** Without it Compose starts the database and the model server for
  a job that uses neither.
- **The mounts are at new paths, and the two settings are overridden to match.** A `-v` at
  a container path the compose file already declares is **silently ignored**. The compose
  file's read-only mount wins, with no error and no warning (measured 2026-10-06). So
  conversion cannot be pointed at `/kb-repo`; it is given `/sources` and `/out`, and
  `SOURCE_DIR` and `KB_PATH` are set to those.
- **It runs as you, not as the image's user, and that is what keeps step 5 true.** This is
  the only part of the deployment that writes into the content tree, and uid 10001 cannot
  write a tree that belongs to you, which step 5 says it should. `--user` settles it in the
  right direction: the markdown comes out owned by you and mode 644, so uid 10001 can still
  read it and nothing about step 5 has to be relaxed. The cache override is **required**
  alongside it: the image's own cache directory belongs to uid 10001, and `uv` refuses to
  start without a writable one (`failed to open file .../CACHEDIR.TAG: Permission denied`).
  Confirmed on a Linux host 2026-10-07: output owned by the login user, files 644,
  directories traversable, and still editable afterwards by the operator. Dropping
  `--user` there fails at the first command, with a message naming the directory and the
  uid it was running as.
  Granting the group instead, with `chgrp -R 10001 "$OUT"` and `g+rwX`, would also get
  the write done and is the worse answer: it leaves the output owned by a uid you cannot edit
  as, in a tree the rest of the deployment expects to be yours. It is recorded here as the
  fallback if `--user` ever turns out not to suit a host, not as a second supported route.
- **What this image can convert**: PDF through geometry reconstruction, Word `.docx`, and
  CSV. It carries no LibreOffice, so legacy `.doc` and `.dot` do not convert here, and no
  ML runtime, so the layout-model escalation for a stubborn PDF is not available either.
  Both are deliberate, the image being the server and kept small on purpose, and both are
  reportable rather than silent: `report` names what did not convert.

Read `report` before moving on. It says which documents are clean, which are mostly
picture, and which have tables that geometry may have flattened. A document that converted
badly will be found and returned badly; retrieval does not repair a conversion.

Commit `corpus.yaml` in the content repository if you want the record of what was converted
to survive the host. `kb/` itself is not in version control.

## 12. Apply the schema, index, and confirm the model used the card

First run only, which creates the schema and the two database roles:

```bash
make compose-index ARGS=--init
```

Then index what is in `kb/`:

```bash
make compose-index
```

On a fresh database, skipping `--init` ends in an error naming a missing table. The
`--init` run also records the embedding model and its dimension, which every later run is
checked against, so a model mismatch is caught here.

Indexing has just loaded the model and it stays resident for a few minutes, so ask
immediately:

```bash
docker compose -f compose/docker-compose.yml --env-file .env exec ollama ollama ps
```

**Expect** the model listed with a processor of `100% GPU`. **`100% CPU` means the
reservation resolved and the model still did not use the card**. The stack is up and the
GPU path is not proven. Record which it said. If the listing is empty the keep-alive
window has passed; re-run the index and ask again.

`kb index` indexes what is on disk and **deletes the documents that are no longer there**,
so re-running it after adding or removing files is all that adding or removing documents
takes. No `--init`, no `--reindex-all`.

## 13. Probe the running deployment

`make check` proves the code and cannot prove this: no test process has a certificate, a
reverse proxy or a live credential store. `scripts/http_probe.py` drives the **running**
stack over TLS at its public address.

Run it the way a client reaches it: from a machine on the user network, not from the host.
An in-network run passes while saying nothing about whether a real client trusts the
certificate, which is the question that matters on the supplied-certificate path.

```bash
read -rs KB_PROBE_TOKEN && export KB_PROBE_TOKEN
read -rs KB_PROBE_TOKEN_2 && export KB_PROBE_TOKEN_2
uv run python scripts/http_probe.py --base-url https://<the name clients dial>
```

`read -rs` does not echo, so the secrets stay out of shell history. Add `--ca <file>` if
the certificate's issuer is not already trusted on that machine. Nothing about the
deployment is taken from the local checkout: the document to request comes from the
server's own listing, and a citation link is judged against `--base-url`.

It checks what is open and what is closed, drives the MCP protocol through a search and a
fetch, and **follows the citation link it gets back to confirm the cited document is
actually fetchable**, the check that catches step 5's silent permission failure.

The revocation checks have to write the credential store, which exists only on the host, so
that half runs there, through the one service whose mount is read-write:

```bash
docker compose -f compose/docker-compose.yml --env-file .env --profile tools run --rm \
  --entrypoint "uv run --no-sync python" \
  -v "$PWD/scripts:/app/scripts:ro" \
  kb-token scripts/http_probe.py --base-url https://<the name clients dial> \
  --tokens-file /etc/kb/tokens.json
```

Add `-v <cert dir>:/tls:ro --ca /tls/cert.pem` if the issuer is not trusted inside the
container. The image carries the server, not the toolkit, which is why the probe is mounted
in rather than already present. **Do not drop `--no-sync`** if you rewrite that line: a
bare `uv run` re-resolves dependencies at container start and fetches them, which is a
surprise on an on-premises host and a failure on one without that reach.

It issues its own throwaway pair, withdraws one, confirms that closes **both** routes
within the cache window while the other keeps working, and withdraws both on the way out.
No secret is printed.

**Expect** every check to pass and exit 0. The last full run was 23 of 23 from the host; a run from a client machine is 17 checks, because the
revocation checks need the host.

## 14. Give each person their credential

One per person, so that withdrawing one person's access costs nothing to anyone else:

```bash
make token ARGS="issue firstname.lastname@example.com"
make token ARGS=list
```

The secret prints once. Hand it over the way the organisation hands over any other
credential; it ends up in a configuration file on that person's machine, which is the
limit of this scheme, and the reason to ask whether the organisation has an identity
provider.

To withdraw access:

```bash
make token ARGS="revoke <token-id>"
```

`list` shows the ids; it never shows a secret. A withdrawal takes effect on both routes
within `KB_TOKEN_CACHE_SECONDS`, five seconds by default, with no restart and nothing
required of any other user.

---

## Day to day

| | |
|---|---|
| Add or remove documents | put them in, or take them out of, the source directory; re-run step 11, then `make compose-index` |
| Add or remove a person | `make token ARGS="issue ..."` / `ARGS="revoke ..."`; no restart |
| Restart the stack | `make compose-down` then `make compose-up`. Volumes are kept unless you pass `ARGS=-v` |
| Check it is healthy | `curl -sS https://<name>/health`, which needs no credential |
| What to back up | the credential store `/srv/kb`, the `.env` file, the certificate and key, and the content repository. The database and the model volume are rebuilt by steps 9 and 12 from those |
| Update the code | `git pull`, then `make compose-up` to rebuild, then `make compose-index` if the indexer changed |

## When something does not work

| Symptom | Where to look |
|---|---|
| The proxy answers 502 | the MCP server is not running. `docker compose ... logs kb-mcp`. The usual cause is the credential store's ownership (step 5) |
| Every citation link is a 404 | `KB_URL_BASE` has a `/kb` on the end, or the content tree is not readable by uid 10001 (steps 4 and 5) |
| A client fails before any HTTP | the certificate does not carry the name that client dialled (step 7) |
| A client gets an error about the host | `KB_PUBLIC_HOST` is not the name the client dials (step 4) |
| Indexing is slow and the GPU is idle | `ollama ps` says `100% CPU`. With `KB_GPU=on` the stack should have refused to start; `make gpu-check` says what it decides and why |
| A credential stops working within seconds | it was withdrawn. `make token ARGS=list` |
| `Cannot write to the output directory` | exactly what it says: the tree is not writable as the uid the run used. Almost always a dropped `--user` in step 11, and the message names the uid so you can tell |
