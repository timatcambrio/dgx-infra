# Switching to plain HTTP with no credentials

This guide changes a service that is already installed and running with HTTPS and a
credential for each person into one that runs on plain HTTP with no credentials. After
the switch, users install nothing and configure nothing but the service's address.

Nothing is reinstalled. The documents, the converted text and the search index are kept,
and nothing is converted or indexed again. The switch can be undone with one command
([Switching back](#switching-back)).

For a new installation, choose these settings in
[step 6 of the deployment guide](deployment-dgx-guide.md#6-choose-how-people-connect)
instead.

## What changes

| | Before | After |
|---|---|---|
| Address users connect to | `https://<KB_HOST>/mcp`, port 443 | `http://<KB_HOST>/mcp`, port 80 |
| Certificate on each user's computer | trusted once | not needed |
| Credential in each user's settings | required | not needed |
| Who can search and read the documents | people with a credential | anyone who can reach the DGX on the network |
| What the log records for each request | the person's email | `anonymous` |
| Withdrawing one person's access | one command | not possible; access is the network's to control |
| Traffic between users and the DGX | encrypted | unencrypted |

The last three rows are what the switch gives up. Whether to accept that is a decision for
your organisation; the IT brief describes both options.

## Before you start

- Port 80 must be open from the user network to the DGX. Until now only 443 was
  needed. Check with whoever manages the network; if port 80 is closed, users will be
  unable to connect after the switch.
- Change every user's settings at the same time. The old `https://` address stops
  working the moment the DGX restarts. [Users' computers](#users-computers) says what each
  person changes.
- About 15 minutes on the DGX, logged in as `kbadmin`, either at the DGX's keyboard or with
  `ssh kbadmin@<KB_HOST>`. `<KB_HOST>` is the host name users connect to, the same one
  used when the service was installed.

## On the DGX

### 1. Update the code

```bash
cd ~/dgx-infra
git pull
```

If the code was copied to the DGX from a Mac rather than cloned there, update it from the
Mac instead, as in [Get the latest code](troubleshooting.md#1-get-the-latest-code).

**Expect** the update to download and finish without errors. Then confirm this version has
the two settings the switch uses:

```bash
grep -c -E '^# (KB_TLS|KB_AUTH)=off' .env.example
```

**Expect** `2`. If it says `0`, the update did not arrive; contact the Cambrio team.

### 2. Keep a copy of the current settings

```bash
cp .env .env.before-http
```

This copy is what [Switching back](#switching-back) restores.

### 3. Change the settings

Open the settings file:

```bash
nano .env
```

Make three changes, then save (Control-O, Return) and exit (Control-X):

1. Add these two lines at the end:

   ```bash
   KB_TLS=off
   KB_AUTH=off
   ```

2. Delete the two lines that start with `TLS_CERT=` and `TLS_KEY=`. The certificate files
   themselves stay in `tls/`, in case you switch back.
3. If there is a line that starts with `KB_URL_BASE=`, delete it. This version no longer
   uses it.

Check the result:

```bash
grep -E '^(KB_TLS|KB_AUTH|TLS_CERT|TLS_KEY|KB_URL_BASE)=' .env
```

**Expect** exactly these two lines and nothing else:

```
KB_TLS=off
KB_AUTH=off
```

### 4. Restart the service

```bash
make compose-up
```

**Expect** the GPU message, then the proxy and the search server restarted while the
database and the model server keep running. The first time after this update you will also
see `kb-static` stopped and removed: that container served the converted documents as
files, and this version no longer has it.

If it stops before restarting anything with a message that `TLS_CERT/TLS_KEY are set`,
one of those lines is still in `.env`; go back to step 3.

Confirm the service now answers on port 80:

```bash
docker compose -f compose/docker-compose.yml --env-file .env ps --format '{{.Service}} {{.Ports}}'
```

**Expect** four lines, with `caddy` showing `0.0.0.0:80->80/tcp` and nothing on 443.

### 5. Bring the index up to date

```bash
make compose-index
```

**Expect** every document reported as unchanged, and `0 reindexed`. This is the usual step
after updating the code; the switch itself does not change the index.

### 6. Test the service

Runs the automated check against the live service, with no certificate and no credential:

```bash
docker compose -f compose/docker-compose.yml --env-file .env --profile tools run --rm \
  --entrypoint "uv run --no-sync python" \
  -v "$PWD/scripts:/app/scripts:ro" \
  kb-token scripts/http_probe.py --base-url http://<KB_HOST> --no-auth
```

**Expect** `13/13 checks passed` as the last line.

## Users' computers

Each person changes their Codex settings file. Nothing needs installing, and new users
only ever need this step.

Open the settings file. On a Mac, in Terminal:

```bash
nano ~/.codex/config.toml
```

On Windows, in PowerShell:

```powershell
notepad "$HOME\.codex\config.toml"
```

Find the lines that start with `[mcp_servers.kb]`. Replace them, and the
`[mcp_servers.kb.http_headers]` block with the credential beneath them, with exactly this:

```toml
[mcp_servers.kb]
url = "http://<KB_HOST>/mcp"
startup_timeout_sec = 30
tool_timeout_sec = 60
```

Save, then quit the ChatGPT app fully and reopen it. On a Mac: ChatGPT menu, Quit ChatGPT.
On Windows: also its icon in the system tray, if it has one.

**Expect** Codex to answer a question from the documents, with a citation, as before.

A settings file that still has the credential block keeps working, and those requests are
logged under that person's name, so a user who has not removed it yet is not a problem.
Only the old `https://` address stops working.

### Removing the certificate (optional)

The certificate users trusted for HTTPS is no longer used. Removing it is optional. On a
Mac:

```bash
sudo security delete-certificate -c "<KB_HOST>" /Library/Keychains/System.keychain
```

If this says `Unable to delete certificate matching`, the certificate is either already
gone or was added more than once. To tell which, list the copies:

```bash
security find-certificate -a -c "<KB_HOST>" -Z /Library/Keychains/System.keychain | grep SHA-1
```

No output means it is gone. Otherwise delete each copy by the value after `SHA-1 hash:`:

```bash
sudo security delete-certificate -Z <SHA-1 hash> /Library/Keychains/System.keychain
```

On Windows:

```powershell
certutil -user -delstore Root "<KB_HOST>"
```

## The credentials already issued

They are no longer needed, and nothing has to be done about them. The credential store is
kept as it was, so switching back gives everyone their access again with no new
credentials. To withdraw them all anyway, list them and withdraw each one, as in
[step 15 of the deployment guide](deployment-dgx-guide.md#15-give-each-person-a-credential):

```bash
make token ARGS=list
```

## Switching back

Restores the settings saved in [step 2](#2-keep-a-copy-of-the-current-settings), back to
HTTPS with credentials:

```bash
cd ~/dgx-infra
cp .env.before-http .env
make compose-up
```

**Expect** `caddy` back on 443. Run the check from
[step 13 of the deployment guide](deployment-dgx-guide.md#13-test-the-service-from-the-dgx)
and **expect** `19/19 checks passed`.

Each user then puts back their previous settings: the `https://` address and their
credential block. A user who removed the certificate trusts it again, as in
[step 14 of the deployment guide](deployment-dgx-guide.md#14-check-from-a-second-computer);
the certificate itself has not changed.

## When something does not work

| Symptom | Likely cause |
|---|---|
| `make compose-up` stops, naming `TLS_CERT/TLS_KEY` | one of those lines is still in `.env` ([step 3](#3-change-the-settings)) |
| `make compose-up` stops, naming `KB_TLS` or `KB_AUTH` | a value other than `off` was typed; it must be exactly `off` |
| Users' connections are refused | their settings still say `https://`, or port 80 is closed between them and the DGX |
| An error about the host name | the address in the user's settings is not `<KB_HOST>` exactly |
| The check in step 6 reports `/mcp` answering 401 | `KB_AUTH=off` is missing from `.env`, or step 4 was not run after adding it |
| The check in step 6 fails to connect at all | the service is not on port 80; repeat step 4 and look at its output |

For anything else, see
[When something does not work](deployment-dgx-guide.md#when-something-does-not-work) in the
deployment guide.
