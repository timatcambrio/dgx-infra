# Deploying on the DGX

This guide installs the document search service on the DGX, tests it with a few documents,
then loads the full collection. Each step says what a command does, then gives the
command.

## Before you start

You need:

| | |
|---|---|
| The DGX, with `nvidia-smi` working | the GPU driver is already installed |
| A login on the DGX that can use `sudo` | |
| About 150 GB free on the disk that has `/var/lib/docker` | for images, the database and the model |
| Internet access from the DGX | to download software and the embedding model |
| The DGX's host name and IP address on the user network | see [Finding the host name and address](#finding-the-host-name-and-address) |
| Inbound TCP port 443 to the DGX from the user network | the only port the service uses |
| A second computer on the user network (Mac or Windows) with the ChatGPT desktop app (Codex), able to connect to the DGX over SSH | used in [step 14](#14-check-from-a-second-computer) to connect the way a user does |
| A handful of the documents to be searched (PDF, Word `.docx`, or CSV) | for the first test run |

### How to read the commands

Text in angle brackets, like `<KB_HOST>`, is a value you replace before running the
command. Everything else runs as written. The values are:

| Placeholder | Replace with | Example |
|---|---|---|
| `<YOU>` | your login name on the DGX (run `whoami`) | `jsmith` |
| `<KB_HOST>` | the host name users will connect to | `dgx01.local` |
| `<DGX_IP>` | the DGX's IP address on the user network | `10.20.30.40` |

Run every command in a terminal on the DGX, from the `dgx-infra` directory, unless the
step says otherwise. You can work at the DGX's own keyboard and screen or connect to it
over SSH; the commands are the same.

### Finding the host name and address

On the DGX:

```bash
hostname -f
hostname -I
```

`hostname -f` prints the machine's full name. `hostname -I` prints its IP addresses; ignore
any starting with `172.17.` (that is Docker's internal network). IT can confirm which name
and address users' computers can reach.

To check the name works from the second computer, run this there (Mac Terminal or Windows
PowerShell):

```bash
nslookup <KB_HOST>
```

**Expect** an address listed. If the name does not resolve, use the IP address as
`<KB_HOST>` everywhere in this guide.

## 1. Check the host

Confirms the GPUs are visible and there is enough disk space.

```bash
nvidia-smi -L
df -h /var/lib/docker
```

**Expect** at least one GPU listed. If none are, stop: the driver needs fixing before
anything else.

## 2. Get the code

Downloads the deployment code and the repository for the converted documents.

```bash
cd ~
git clone https://github.com/timatcambrio/dgx-infra.git
git clone https://github.com/timatcambrio/dgx-knowledge.git
cd ~/dgx-infra
```

## 3. Bootstrap the host

Installs Docker, its compose plugin and the NVIDIA Container Toolkit (whichever are
missing), adds you to the `docker` group, then starts a small test container to confirm it
can see the GPUs.

```bash
sh scripts/bootstrap_host.sh
```

If it says it added you to the `docker` group, log out and back in, then run the check
below. Group membership only takes effect at login.

`--check` installs nothing. It confirms that the driver sees the GPUs, Docker and the
toolkit are installed, you are in the `docker` group, and a container can see the GPUs:

```bash
cd ~/dgx-infra
sh scripts/bootstrap_host.sh --check
```

**Expect** the last line to be `== Ready`.

## 4. Create the settings file

Copies the example settings and generates three database passwords.

```bash
cp .env.example .env
openssl rand -hex 24
openssl rand -hex 24
openssl rand -hex 24
```

Open `.env` in a text editor (`nano .env`). For each line below, find the line with the
same name, remove the `#` in front of it if there is one, and set the value. Replace the
placeholders; keep the rest exactly as shown.

```bash
SOURCE_DIR=/home/<YOU>/kb-sources
KB_PATH=/home/<YOU>/dgx-knowledge
KB_PUBLIC_HOST=<KB_HOST>
KB_URL_BASE=https://<KB_HOST>
KB_TOKENS_FILE=/srv/kb/tokens.json
KB_TOKENS_DIR=/srv/kb
KB_GPU=on
EMBED_MODEL=nomic-embed-text:v1.5
POSTGRES_PASSWORD=<first password from openssl>
KB_INDEX_PASSWORD=<second password from openssl>
KB_READ_PASSWORD=<third password from openssl>
```

`SOURCE_DIR` is where the original documents go. `KB_PATH` is where the converted text
goes. The service never changes anything in `SOURCE_DIR`.

`EMBED_MODEL` pins a fixed version of the embedding model. As of 2026-10-07,
`nomic-embed-text:v1.5` and `nomic-embed-text:latest` are the same build (digest
`0a109f422b47`, 274 MB), which is the build the tested deployment used. Pinning the version
keeps a later download from changing the model under the same name.

Four of these have each broken a deployment, so read them rather than copying:

- `KB_PATH` must be absolute. Compose resolves a relative bind-mount source against
  `compose/`, not the repository root, so a relative value silently mounts nothing. It is
  the content repository's root, with `kb/` inside it.
- `KB_PUBLIC_HOST` is the address clients actually dial. The server checks the `Host`
  header, and a mismatch is refused with an error that reads like a fault in the client.
- `KB_URL_BASE` is the site root, with no `/kb`. Each document is stored as
  `kb/<file>.md` and the proxy routes `/kb/*`, so both halves already supply that segment;
  a value ending in `/kb` makes every citation a dead link. `make compose-up` refuses such
  a value outright.
- `KB_TOKENS_DIR` must be `KB_TOKENS_FILE`'s parent. The stack mounts the directory,
  because issuing or revoking a credential replaces the file and a single-file mount would
  bind the old copy. `make compose-up` checks this.

## 5. Create the directories

Creates the folder for the original documents, the folder for the converted text, and the
credential store.

```bash
mkdir -p ~/kb-sources
mkdir -p ~/dgx-knowledge/kb
sudo install -d -o 10001 -g 10001 -m 700 /srv/kb
```

The first two belong to you. The third belongs to user 10001, which is the account the
service runs as inside its containers; it does not need to exist on the DGX itself.

Create the first two before starting anything else in this guide. If a container starts
first, Docker creates them owned by `root`, and you would then need `sudo` to fix them.

## 6. Create the certificate

Creates the HTTPS certificate and key for the DGX. Users' computers are told to trust this
certificate in [step 14](#14-check-from-a-second-computer).

```bash
sh scripts/make_tls_cert.sh <KB_HOST> <DGX_IP>
```

It writes `tls/cert.pem` and `tls/key.pem`, and prints two lines. Add them to `.env`:

```bash
TLS_CERT=/home/<YOU>/dgx-infra/tls/cert.pem
TLS_KEY=/home/<YOU>/dgx-infra/tls/key.pem
```

The certificate is valid for 825 days.

## 7. Confirm the GPU setting

Checks that the service will use the GPUs, without starting anything.

```bash
make gpu-check
```

**Expect** `GPU: reserving all NVIDIA GPUs for ollama`. If it says anything else, stop and
rerun [step 3](#3-bootstrap-the-host) with `--check`.

## 8. Issue your credential

Creates the access credential you will use from the second computer. The service will not
start until at least one credential exists.

```bash
make token ARGS="issue <your email>"
```

The secret prints once and cannot be shown again. Copy it somewhere safe now; you need it
in [step 14](#14-check-from-a-second-computer).

## 9. Start the service and download the model

Builds and starts the five containers: the database, the model server, the search server,
the document server and the HTTPS proxy.

```bash
make compose-up
```

**Expect** the GPU message from step 7 again, then all five containers started.

Downloads the embedding model (about 270 MB, once):

```bash
docker compose -f compose/docker-compose.yml --env-file .env \
  exec ollama ollama pull nomic-embed-text:v1.5
```

**Expect** it to end with `success`.

## 10. Confirm the GPUs reached the model server

```bash
docker compose -f compose/docker-compose.yml --env-file .env exec ollama nvidia-smi -L
```

**Expect** the same GPUs as in step 1.

## 11. Convert the test documents

Copy a handful of documents into `~/kb-sources`, then convert them to text.

The block below runs as written, with nothing to replace. Each option in it is there
because testing showed it was needed, so paste it unchanged.

Paste this first part once. It defines a `convert` command that lasts until you log out;
if you log out, paste it again.

```bash
cd ~/dgx-infra
SRC="$HOME/kb-sources"
OUT="$HOME/dgx-knowledge"

convert() {
  docker compose -f compose/docker-compose.yml --env-file .env run --rm --no-deps \
    --user "$(id -u):$(id -g)" -e UV_CACHE_DIR=/tmp/uv-cache-"$(id -u)" \
    -v "$SRC:/sources:ro" -v "$OUT:/out:rw" \
    -e SOURCE_DIR=/sources -e KB_PATH=/out \
    kb-mcp pipeline "$@"
}
```

Then run these one at a time, and read each one's output before running the next:

```bash
convert inventory
```

Lists the documents found and records each one.

```bash
convert triage
```

Measures how much readable text each PDF has.

```bash
convert convert
```

Writes the converted text into `~/dgx-knowledge/kb/`.

```bash
convert report
```

Summarises the result: which documents converted cleanly, which are mostly images, which
have tables that may have lost their layout, and which could not be converted. A document
that converted badly will be searched badly, so check this before going on.

The converter handles PDF, Word `.docx`, and CSV. It does not handle older Word `.doc`
files; `report` lists any it skipped. Save those as `.docx` and they will convert.

## 12. Index the documents

Sets up the database (first time only):

```bash
make compose-index ARGS=--init
```

Indexes everything in `~/dgx-knowledge/kb/`:

```bash
make compose-index
```

Then, straight away, confirm the model ran on a GPU:

```bash
docker compose -f compose/docker-compose.yml --env-file .env exec ollama ollama ps
```

**Expect** `nomic-embed-text:v1.5` listed with `100% GPU`. If it says `100% CPU`, the
service works but is not using the GPU; note it and contact the Cambrio team. If the list
is empty, the model has already unloaded: run `make compose-index` again and repeat the
check.

## 13. Test the service from the DGX

Runs the full automated check against the live service: the certificate, access with and
without a credential, a search, opening a document, and withdrawing a credential. It
creates two temporary credentials for the test and deletes them when it finishes.

```bash
docker compose -f compose/docker-compose.yml --env-file .env --profile tools run --rm \
  --entrypoint "uv run --no-sync python" \
  -v "$PWD/scripts:/app/scripts:ro" \
  -v "$PWD/tls:/tls:ro" \
  kb-token scripts/http_probe.py --base-url https://<KB_HOST> \
  --ca /tls/cert.pem --tokens-file /etc/kb/tokens.json
```

**Expect** `23/23 checks passed` as the last line.

## 14. Check from a second computer

These commands run on the second computer, not on the DGX. Follow the [Mac](#on-a-mac)
or the [Windows](#on-windows) version.

### On a Mac

Run these in Terminal.

Copy the certificate from the DGX:

```bash
scp <YOU>@<KB_HOST>:dgx-infra/tls/cert.pem ~/dgx-kb.crt
```

Store your credential from [step 8](#8-issue-your-credential) for the checks below. The
command waits for you to paste it and press Return, and does not show what you paste:

```bash
read -rs KB_TOKEN && export KB_TOKEN
```

Check the connection using the certificate:

```bash
curl -sS --cacert ~/dgx-kb.crt -o /dev/null -w 'health=%{http_code}\n' https://<KB_HOST>/health
curl -sS --cacert ~/dgx-kb.crt -o /dev/null -w 'no-token=%{http_code}\n' -X POST https://<KB_HOST>/mcp
curl -sS --cacert ~/dgx-kb.crt -o /dev/null -w 'with-token=%{http_code}\n' -X POST \
  -H "Authorization: Bearer $KB_TOKEN" \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}' \
  https://<KB_HOST>/mcp
```

**Expect** `health=200`, `no-token=401`, `with-token=200`.

Trust the certificate. This asks for an administrator password:

```bash
sudo security add-trusted-cert -d -r trustRoot -k /Library/Keychains/System.keychain ~/dgx-kb.crt
```

Open the Codex settings file:

```bash
mkdir -p ~/.codex
nano ~/.codex/config.toml
```

Add these lines, with your credential in place of the placeholder, then save (Control-O,
Return) and exit (Control-X):

```toml
[mcp_servers.kb]
url = "https://<KB_HOST>/mcp"
startup_timeout_sec = 30
tool_timeout_sec = 60

[mcp_servers.kb.http_headers]
Authorization = "Bearer <your credential from step 8>"
```

Quit the ChatGPT app fully (ChatGPT menu, Quit ChatGPT) and reopen it. Then go to
[Ask a question](#ask-a-question).

### On Windows

Run these in PowerShell. Type `curl.exe`, not `curl`: in PowerShell, `curl` is a different
command.

Copy the certificate from the DGX:

```powershell
scp <YOU>@<KB_HOST>:dgx-infra/tls/cert.pem "$HOME\dgx-kb.crt"
```

Trust the certificate for your Windows account. Windows shows a security warning asking
whether to install it; choose Yes:

```powershell
certutil -user -addstore Root "$HOME\dgx-kb.crt"
```

**Expect** `CertUtil: -addstore command completed successfully.`

Store your credential from [step 8](#8-issue-your-credential) for the checks below. The
first command waits for you to paste it and press Return, and shows only asterisks:

```powershell
$secret = Read-Host -AsSecureString "Credential"
$env:KB_TOKEN = [System.Net.NetworkCredential]::new('', $secret).Password
```

Write the test request to a file, so PowerShell does not alter its quotes:

```powershell
Set-Content -Path "$HOME\kb-init.json" -Encoding ascii -Value '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
```

Check the connection:

```powershell
curl.exe -sS --ssl-no-revoke -o NUL -w "health=%{http_code}\n" https://<KB_HOST>/health
curl.exe -sS --ssl-no-revoke -o NUL -w "no-token=%{http_code}\n" -X POST https://<KB_HOST>/mcp
curl.exe -sS --ssl-no-revoke -o NUL -w "with-token=%{http_code}\n" -X POST -H "Authorization: Bearer $env:KB_TOKEN" -H "Content-Type: application/json" -H "Accept: application/json, text/event-stream" --data-binary "@$HOME\kb-init.json" https://<KB_HOST>/mcp
```

**Expect** `health=200`, `no-token=401`, `with-token=200`. These checks use the certificate
you just trusted, so they also confirm Windows trusts it.

Open the Codex settings file (Notepad asks whether to create it; choose Yes):

```powershell
New-Item -ItemType Directory -Force "$HOME\.codex" | Out-Null
notepad "$HOME\.codex\config.toml"
```

Add these lines, with your credential in place of the placeholder, and save:

```toml
[mcp_servers.kb]
url = "https://<KB_HOST>/mcp"
startup_timeout_sec = 30
tool_timeout_sec = 60

[mcp_servers.kb.http_headers]
Authorization = "Bearer <your credential from step 8>"
```

Quit the ChatGPT app fully (including its icon in the system tray, if it has one) and
reopen it.

### Ask a question

In Codex, ask something one of the test documents can answer, for example: "Search the
knowledge base for <a topic from a test document> and cite the source."

**Expect** an answer that cites one of the test documents.

## 15. Give each person a credential

One credential per person, so one person's access can be withdrawn without affecting
anyone else.

```bash
make token ARGS="issue <their email>"
```

The secret prints once. Each person trusts the certificate and adds their credential to their
own Codex settings file, as in [step 14](#14-check-from-a-second-computer).

List who has access (this shows ids, never secrets):

```bash
make token ARGS=list
```

Withdraw someone's access, using the id from the list:

```bash
make token ARGS="revoke <token-id>"
```

This takes effect within five seconds, with no restart.

## 16. Load the full document collection

Once the test works, copy the full collection into `~/kb-sources` on the DGX. Remove any
test documents that are not part of the collection.

Paste the first part of [step 11](#11-convert-the-test-documents) again if you have logged
out since, then run these one at a time:

```bash
convert inventory
```

```bash
convert prune
```

Lists the converted files whose original is no longer in `~/kb-sources`. If the list is
right, delete them:

```bash
convert prune --yes
```

```bash
convert convert
```

```bash
convert report
```

Then index again. This adds new documents and removes ones no longer present:

```bash
make compose-index
```

Repeat this step whenever documents are added or removed.

## Day to day

| Task | Command |
|---|---|
| Add or remove documents | change `~/kb-sources`, then [step 16](#16-load-the-full-document-collection) |
| Add or remove a person | [step 15](#15-give-each-person-a-credential) |
| Restart the service | `make compose-down`, then `make compose-up` |
| Check it is running | `curl -sS --cacert tls/cert.pem https://<KB_HOST>/health` |
| Back up | `/srv/kb`, `.env`, the `tls/` directory, and `~/dgx-knowledge` |
| Update the code | `git pull`, `make compose-up`, then `make compose-index` |

## When something does not work

| Symptom | Likely cause |
|---|---|
| The service answers 502 | the search server cannot read the credential store; check `/srv/kb` was created as in [step 5](#5-create-the-directories), and that a credential exists ([step 8](#8-issue-your-credential)) |
| Every document link returns 404 | `KB_URL_BASE` ends in `/kb` ([step 4](#4-create-the-settings-file)), or the converted files are not readable |
| A connection fails before any reply, with a certificate error | the computer does not trust the certificate, or it dialled a name not on it ([step 6](#6-create-the-certificate), [step 14](#14-check-from-a-second-computer)) |
| An error about the host name | `KB_PUBLIC_HOST` is not the name the computer dialled ([step 4](#4-create-the-settings-file)) |
| `Cannot write to the output directory` during conversion | the `convert` command was changed; paste the one in [step 11](#11-convert-the-test-documents) again |
| A credential stops working | it was withdrawn; check `make token ARGS=list` |
