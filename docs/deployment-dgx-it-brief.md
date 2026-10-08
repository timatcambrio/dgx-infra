# DGX Knowledge Hub

One Linux host, the DGX, runs a small set of containers under Docker Compose. They convert and
index the organisation's documents and answer searches over them. Indexing and the embedding
model both run on the host, on its own GPUs. Users connect to the service on 443/tcp only.

## Installed on the host

One script in the delivered code, `scripts/bootstrap_host.sh`, adds Docker's and NVIDIA's apt
repositories and installs:

| Package | From | Purpose |
|---|---|---|
| `docker-ce`, `docker-ce-cli`, `containerd.io` | Docker's apt repository | container runtime |
| `docker-buildx-plugin`, `docker-compose-plugin` | Docker's apt repository | building and running the containers |
| `nvidia-container-toolkit` | NVIDIA's apt repository | GPU access from containers |

It also makes these changes to the host:

- Adds the login user to the `docker` group. Membership in that group is equivalent to root.
- Registers the NVIDIA runtime in Docker's daemon configuration, if it is not already there,
  and restarts the Docker daemon.

The NVIDIA driver must already be installed (`nvidia-smi` works). Everything after this script
runs in containers.

## What runs

| Container | Image | Runs as | Publishes |
|---|---|---|---|
| Reverse proxy | `caddy:2` | image default | 443/tcp |
| Search server | built on the host from `python:3.12-slim` | uid 10001, non-root | nothing |
| Static file server | `caddy:2` | image default | nothing |
| Database | `pgvector/pgvector:pg16` | image default | nothing |
| Model server | `ollama/ollama` | image default | nothing |
| Credential tool | same image as the search server | uid 10001, non-root | nothing |

The first five are long-running services. The credential tool runs only when a credential is
issued or withdrawn, then exits. Write access to the credential store is limited to this
container.

Only the reverse proxy is reachable from the network; the rest are on Docker's internal
network. The documents directory is mounted read-only into the containers that read it.

## Ingress

Inbound 443/tcp from the networks the users are on. Users connect by a host name
that must resolve on those networks and match the certificate ([TLS](#tls)).

The reverse proxy answers three paths: a health check, the search interface, and the
converted documents. Everything else returns 404.

## Egress

Outbound fetches happen at install and update time:

| What | When |
|---|---|
| Docker's and NVIDIA's apt repositories | when the host is prepared |
| Five public container images: three that run, the build base image, and one for a GPU check | at install and on update |
| Python packages for the search server, pinned by a lockfile | when the image is built |
| The embedding model, about 270 MB | once; then stored in a local volume |

## TLS

TLS terminates at the reverse proxy, using a certificate issued by a private CA we generate for
this deployment. Each user's machine is set to trust that CA once. The certificate is issued for
the host name users dial.

## Access control

Each person gets their own bearer credential: 32 random bytes, shown once when issued. The host
stores only a SHA-256 hash of each, in one file in a directory readable only by the search
server's unprivileged user (mode `700`).

Both the search interface and the converted-documents route check every request against that
store. Withdrawing a credential takes effect within five seconds on both routes, with no
restart and no effect on other users. The health check needs no credential and returns only
service status.

Anyone with a credential can search and read every indexed document.

## Data

**On the host.** Source documents are read in place. Converted markdown
is written to a second directory. The search index, including the embedding vectors, is stored in
the database's Docker volume. All three are on the host's own storage.

**To the user's assistant.** A search returns document text to the user's assistant, Codex in
the ChatGPT desktop app. Codex sends that text to OpenAI for processing, under OpenAI's terms.

**Logs.** One log records each authentication decision and the person it identifies. The other
records each search tool call: the tool, its arguments including the search text, the document
identifiers returned, and timing. Neither records document text.
