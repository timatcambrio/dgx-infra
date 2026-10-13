# When the service does not answer

Use this page when [step 13 of the guide](deployment-dgx-guide.md#13-test-the-service-from-the-dgx)
or a check from another computer fails, for example with:

- `FAIL` on `the stack answers at all` or `TLS handshake completes` from step 13;
- `Connection refused` or a timeout from `curl`;
- a container listed as `Restarting`.

You run one script on the DGX and send us its output. The table in section 3 lists the
causes we have seen and how to fix each, so you may be able to fix the problem without
waiting for us.

`Connection refused` on port 80 is expected with HTTPS. Over HTTPS the service listens only
on port 443, so an address typed without `https://` (which `curl` sends to port 80) is
refused. With plain HTTP (`KB_TLS=off`) it is the reverse: port 80 only, and `http://`.

## 1. Get the latest code

Update the code on the DGX the same way you first put it there.

If the DGX can reach GitHub, on the DGX:

```bash
cd ~/dgx-infra && git pull
```

If you copied the code from a Mac, run these on that Mac, from the directory that contains
`dgx-infra`:

```bash
git -C dgx-infra pull
```

```bash
rsync -av --filter=':- .gitignore' --exclude .git dgx-infra/ kbadmin@<DGX_IP>:dgx-infra/
```

`rsync` copies only what changed and skips every file Git ignores, so the DGX's own `.env`,
certificate and credentials stay as they are. Do not use `scp -r` for an update: if the Mac
has its own `.env` or certificate, `scp` copies them over the DGX's.

## 2. Run the diagnostic script

On the DGX, from the `dgx-infra` directory:

```bash
sh scripts/diagnose.sh 2>&1 | tee diagnose.txt
```

It changes nothing, needs no `sudo`, and takes under a minute. Send us `diagnose.txt`.

The output has the host name and IP addresses, file paths, the version of the code, the
settings from `.env` that say where files are and how people connect, container status,
recent errors from the proxy and the search server, the number of indexed documents, which
program uses ports 80 and 443, the result of requests to the service from the DGX itself,
and the GPUs.

It does not show passwords or credentials: for those it says only whether each is set. From
the private key it shows only the first line, which says what kind of file it is. Log lines
that record who used the service are removed.

## 3. Read the result

Look for the lines below in `diagnose.txt`. After any fix, run `make compose-up`, then the
script again.

Near the top, the script prints the mode: `mode: https on port 443` or
`mode: http on port 80`. With plain HTTP there is no certificate, so the certificate rows do
not apply.

| The output says | Cause | Fix |
|---|---|---|
| `certificate and key DO NOT MATCH` | The certificate and the key in `.env` come from different runs of the certificate script, or from different places | Make a new pair, as below |
| `is a DIRECTORY` | `TLS_CERT` or `TLS_KEY` was in `.env` before the file existed, and Docker created an empty directory in its place | `sudo rmdir` the path shown, then make a new pair, as below |
| `does not exist`, or `is a relative path` | The path in `TLS_CERT` or `TLS_KEY` is wrong | Set both to the full paths the certificate script printed, starting with `/` |
| `PEM inputs may have been switched` (in the proxy log) | `TLS_CERT` and `TLS_KEY` are the wrong way round | Swap the two values in `.env` |
| `UNREADABLE` | The file is not a PEM certificate or key, or the key has a passphrase, as can happen with a certificate from your own certificate authority | Supply the certificate and an unencrypted key in PEM format, or make a new pair, as below |
| `KB_PUBLIC_HOST ... is not a bare name or IP` | `KB_PUBLIC_HOST` in `.env` contains `https://`, a port or a `/` | Give the name or IP alone, for example `KB_PUBLIC_HOST=10.20.30.40` |
| `is not among the certificate's names` | The certificate was made for a different name than `KB_PUBLIC_HOST` | Make a new pair for the `KB_PUBLIC_HOST` value |
| `Windows line endings` | The code was copied through a Windows computer | Copy it again from a Mac or Linux clone, as in section 1 |
| `used by another container` or `used by a program outside Docker`, on the port marked `needed by this service` | Something else on the DGX already uses that port | Stop it, or send us the output and we will set the service up on a different port |
| `kb-mcp` listed as `Restarting` | The search server cannot start; its log section shows the error | Send us the output |
| `documents: 0`, or `relation "documents" does not exist` | Nothing has been indexed yet | Steps [11](deployment-dgx-guide.md#11-convert-the-test-documents) and [12](deployment-dgx-guide.md#12-index-the-documents) |
| `/health -> 200` for `localhost`, but the request to your address times out | The address does not reach this DGX, or a firewall blocks it | Check that `<KB_HOST>` resolves to this DGX's IP; the same section of the output shows what it resolves to |

### Making a new certificate pair

On the DGX, in `dgx-infra`, giving the same value as `KB_PUBLIC_HOST` in `.env`, without
`https://`:

```bash
sh scripts/make_tls_cert.sh <KB_HOST> <DGX_IP>
```

**Expect** it to end by printing a `TLS_CERT=...` line and a `TLS_KEY=...` line. Check that
`.env` has those two lines exactly, then:

```bash
make compose-up
```

Run `sh scripts/diagnose.sh` again and **expect** `certificate and key MATCH`. Then repeat
[step 13](deployment-dgx-guide.md#13-test-the-service-from-the-dgx). Every computer that
trusted the old certificate has to trust the new one: repeat the certificate part of
[step 14](deployment-dgx-guide.md#14-check-from-a-second-computer) on each.

## 4. Finish an installation that stopped at step 13

These steps finish the installation with plain HTTP and no credentials: users install no
certificate and configure only the service's address. [Step 6 of the
guide](deployment-dgx-guide.md#6-choose-how-people-connect) explains what this setting
gives up. To stay with HTTPS instead, fix what section 3 shows, then continue the guide from
step 13.

Port 80 has to be open from the users' network to the DGX. Ask whoever manages the network
before you start.

### On the DGX

Do sections 1 and 2 first, and send us `diagnose.txt` before going on. It records how the
DGX was set up before these changes.

Open the settings file:

```bash
nano .env
```

Add these two lines at the end:

```bash
KB_TLS=off
KB_AUTH=off
```

Delete any lines that start with `TLS_CERT=`, `TLS_KEY=` or `KB_URL_BASE=`. Save
(Control-O, Return) and exit (Control-X). Check the result:

```bash
grep -E '^(KB_TLS|KB_AUTH|TLS_CERT|TLS_KEY|KB_URL_BASE)=' .env
```

**Expect** exactly `KB_TLS=off` and `KB_AUTH=off`, and nothing else.

Restart the service:

```bash
make compose-up
```

**Expect** the GPU message, then the containers started or recreated. Confirm the proxy is
on port 80:

```bash
docker compose -f compose/docker-compose.yml --env-file .env ps --format '{{.Service}} {{.Status}} {{.Ports}}'
```

**Expect** four services, all `Up`, and `caddy` on `0.0.0.0:80->80/tcp`. If one says
`Restarting`, run section 2 again and send us the output.

Bring the index up to date:

```bash
make compose-index
```

**Expect** a summary of documents indexed or unchanged. If it says the schema is not applied
yet, run `make compose-index ARGS=--init`, then `make compose-index` again. If it finds no
documents, do [step 11](deployment-dgx-guide.md#11-convert-the-test-documents) first.

Run the automated check:

```bash
docker compose -f compose/docker-compose.yml --env-file .env --profile tools run --rm \
  --entrypoint "uv run --no-sync python" \
  -v "$PWD/scripts:/app/scripts:ro" \
  kb-token scripts/http_probe.py --base-url http://<KB_HOST> --no-auth
```

**Expect** `13/13 checks passed` as the last line.

### On one user's Mac

In Terminal:

```bash
curl -sS -o /dev/null -w 'health=%{http_code}\n' http://<KB_HOST>/health
```

**Expect** `health=200`. If the command fails to connect, port 80 is closed between this
Mac and the DGX.

Open the Codex settings file:

```bash
mkdir -p ~/.codex && nano ~/.codex/config.toml
```

Add these lines, then save and exit:

```toml
[mcp_servers.kb]
url = "http://<KB_HOST>/mcp"
startup_timeout_sec = 30
tool_timeout_sec = 60
```

Quit the ChatGPT app fully (ChatGPT menu, Quit ChatGPT) and reopen it. In Codex, ask
something one of the test documents can answer, for example: "Search the knowledge base for
*[a topic from a test document]* and cite the source."

**Expect** an answer that cites one of the test documents. Every other user adds the same
lines to their own settings file; Windows users follow the
[Windows part of step 14](deployment-dgx-guide.md#on-windows), skipping the certificate and
credential steps and using `http://` in the address.

### Load the documents and confirm

Load the full collection with
[step 16](deployment-dgx-guide.md#16-load-the-full-document-collection). Then, on the DGX,
run the script once more and send us the output:

```bash
sh scripts/diagnose.sh 2>&1 | tee diagnose-after.txt
```

**Expect** four services `Up`, port 80 used by this service's proxy, and the number of
documents you loaded.
