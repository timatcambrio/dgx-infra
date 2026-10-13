# When the service does not answer

Use this page when [step 13 of the guide](deployment-dgx-guide.md#13-test-the-service-from-the-dgx)
or a check from another computer fails, for example with:

- `could not work out which document to request` from step 13 (in versions before this
  page existed; the current check names the failure itself);
- `Connection refused` or a timeout from `curl`;
- a container listed as `Restarting`.

You run one script on the DGX. It collects everything needed to find the cause, and you
send us its output. The table below covers the causes we have seen, so you may be able to
fix it straight away.

Two of those messages mislead:

- **`could not work out which document to request`** almost always means the check could
  not reach the service at all, not that documents are missing.
- **`Connection refused` on port 80** is expected with HTTPS. Over HTTPS the service
  listens only on port 443, so an address typed without `https://` (which `curl` sends to
  port 80) is refused. Always include `https://` in front of the address. With plain HTTP
  (`KB_TLS=off`) it is the other way round: port 80 only, and `http://`.

## 1. Get the latest code

The script and a fix to the certificate script are in the latest version. Update the code
on the DGX the way you first put it there.

**If the DGX can reach GitHub**, on the DGX:

```bash
cd ~/dgx-infra && git pull
```

**If you copied the code from a Mac**, on that Mac, from the directory holding
`dgx-infra`:

```bash
git -C dgx-infra pull
```

```bash
rsync -av --filter=':- .gitignore' --exclude .git dgx-infra/ kbadmin@<DGX_IP>:dgx-infra/
```

`rsync` copies only what changed, and leaves out everything Git ignores, so the DGX's own
`.env`, certificate and credentials are not touched. Do not use `scp -r` for an update:
it would copy the Mac's versions of those files over the DGX's, if the Mac has any.

## 2. Run the diagnostic script

On the DGX, from the `dgx-infra` directory:

```bash
sh scripts/diagnose.sh 2>&1 | tee diagnose.txt
```

It changes nothing and needs no `sudo`. It takes under a minute. Send us `diagnose.txt`.

**What it contains.** Host name, IP addresses, file paths, the version of the code, the
non-secret settings from `.env`, container status, the last errors from the proxy and the
search server, the number of indexed documents, what is listening on ports 80 and 443, the
result of requests to the service from the DGX itself, and the GPUs.

**What it leaves out.** Passwords and credentials from `.env` are shown only as set or not
set. Nothing from the private key is shown except its first line, which says what kind of
file it is. Lines naming who used the service are removed from the log.

## 3. Read the result

Look for these lines in `diagnose.txt`. Each one names a cause and its fix. After any fix,
run `make compose-up`, then the script again. The script says near the top which mode the
service is in (`mode: https on port 443` or `mode: http on port 80`). With plain HTTP no
certificate is used, so the certificate rows do not apply.

| The output says | Cause | Fix |
|---|---|---|
| `certificate and key DO NOT MATCH` | The certificate and key are from different runs of step 6. An earlier version of the certificate script could leave them like this when given an address with `https://` in it. | Make a new pair, as below |
| `is a DIRECTORY` | `TLS_CERT` or `TLS_KEY` was in `.env` before the file existed, and Docker created an empty directory in its place | `sudo rmdir` the path it names, then make a new pair, as below |
| `does not exist`, or `is a relative path` | `TLS_CERT` or `TLS_KEY` in `.env` does not point at the file | Set both to the full path the certificate script printed, starting with `/` |
| `PEM inputs may have been switched` (in the proxy log) | `TLS_CERT` and `TLS_KEY` are the wrong way round | Swap the two values in `.env` |
| `UNREADABLE` | The file is not a PEM certificate or key, or the key has a passphrase. This happens with a certificate from your own certificate authority. | Supply an unencrypted PEM key and certificate, or make a new pair, as below |
| `KB_PUBLIC_HOST ... is not a bare name or IP` | `.env` has `https://`, a port or a `/` in `KB_PUBLIC_HOST` | Use the name or IP alone, e.g. `KB_PUBLIC_HOST=10.20.30.40`. `KB_URL_BASE` keeps its `https://`. |
| `is not among the certificate's names` | The certificate was made for a different name than `KB_PUBLIC_HOST` | Make a new pair with the `KB_PUBLIC_HOST` value |
| `Windows line endings` | The code passed through a Windows computer | Copy it again from a Mac or Linux clone, as in section 1 |
| A process other than `docker-proxy` on port 443 (80 with plain HTTP) | Another service on the DGX already uses the port | Stop that service, or tell us and we will move ours |
| `kb-mcp` shows `Restarting` | The search server cannot start. Its log section says why. | Send us the output |
| `documents: 0` | Nothing has been indexed yet | Steps [11](deployment-dgx-guide.md#11-convert-the-test-documents) and [12](deployment-dgx-guide.md#12-index-the-documents) |
| `https://localhost/health -> 200` but the line for your address times out | The address does not lead to this DGX, or a firewall blocks it | Check `<KB_HOST>` resolves to this DGX's IP, as shown in the same section |

### Making a new certificate pair

On the DGX, in `dgx-infra`, with the same value as `KB_PUBLIC_HOST` in `.env` (the name or
IP alone, no `https://`):

```bash
sh scripts/make_tls_cert.sh <KB_HOST> <DGX_IP>
```

**Expect** it to end by printing `TLS_CERT=...` and `TLS_KEY=...`. Check that `.env` has
those two lines exactly, then:

```bash
make compose-up
```

Run `sh scripts/diagnose.sh` again and **expect** `certificate and key MATCH`. Then repeat
[step 13](deployment-dgx-guide.md#13-test-the-service-from-the-dgx). Each computer that
trusted the old certificate must trust the new one: repeat the certificate steps of
[step 14](deployment-dgx-guide.md#14-check-from-a-second-computer).
