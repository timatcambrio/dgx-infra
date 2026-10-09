# Search and the MCP server

Once [conversion](conversion.md) has written `kb/`, the `kb` command makes it searchable and
serves it to an AI assistant (Codex, ChatGPT desktop, Claude Code, Claude desktop) over MCP.
The assistant searches, reads whole sections, and gets a citation with each one naming the
converted file and the original page. This document explains how that works and how to
connect an assistant. Installing and running the service is covered in the [deployment
guide](deployment-dgx-guide.md) and in [Administration](administration.md).

The server runs over **stdio** (the developer path) or **streamable HTTP** (the shared
server on the LAN, behind Caddy with a bearer token). Document summaries
(`kb catalog --summarize`) are optional and not built yet.

## How a document gets cut up

A document is never searched as one blob, and never chunked without regard for its
structure. Two cuts happen, in order:

1. **Sections.** Every heading (`#`, `##`, or `###`) starts a new section that runs until
   the next heading of the same or a shallower level; a `####` heading or deeper does not
   start a new section, it just stays inside the one it's in. A section is what an
   assistant reads: it is never split across a search result.
2. **Chunks.** Inside a section, blocks (paragraphs, lists, tables, annotations) are grouped
   into runs of about 1,200 characters. A chunk is what search matches against; it is never
   shown as an answer on its own. A table shares a chunk with its section's heading when it
   is the first thing under it, otherwise it gets a chunk to itself, and is never split
   unless it is over 2,500 characters. A larger table is cut only between rows: each piece
   repeats the table's header row and the last row of the piece before it, so every piece
   reads as a table on its own, and a single row is never cut however long it is. A very
   long paragraph or list (over 2,500 characters) is split at its blank lines rather than
   mid-sentence. Notes such as [`> **Annotation**`](conversion.md#-annotation) or [`>
   **Boxed text:**`](conversion.md#-boxed-text) always stay attached to whatever came right
   before them.

## `kb search`

```bash
uv run kb search "per diem rates" --k 8 [--slug handbook] [--text-class clean] [--leg fused]
```

Runs the same retrieval the MCP server uses, from the command line, and prints one line per
hit, followed by an indented citation line:

```
0.0164  sec:handbook:5  pages 12–13  Employee Handbook › Handbook › Per Diem Rates  |  Uxbane...
    Employee Handbook, Employee Handbook › Handbook › Per Diem Rates (source: handbook.pdf, pages 12–13, dated UNCONFIRMED; blocks handbook:p012:b001…handbook:p013:b003)
```

Every search runs two independent legs over the indexed chunks and merges them:

- **Lexical** (Postgres full-text search) catches exact tokens (form numbers, codes,
  exact phrases) that an embedding model tends to blur together with similar-looking
  text.
- **Vector** (cosine similarity over `nomic-embed-text` embeddings) catches paraphrase:
  the right passage even when the question uses none of the document's own words.

Neither leg is reliable enough on its own, so results are merged with reciprocal
rank fusion (RRF): each leg contributes independently, and a hit that both legs agree on
outranks a hit either leg alone thought was best. `--leg lexical` or `--leg vector` runs
one leg in isolation, for debugging. A query made only of stop words (`"the of and"`) has
no lexical leg to run (`websearch_to_tsquery` parses it to nothing) and falls back to
vector-only results rather than erroring.

Every hit is a **section**, never a chunk: chunks are what search matches against
internally, but what comes back is always a whole readable section with its page range,
heading path, and a citation you can quote and go check against the original markdown.
(Chunk ids exist and `fetch` accepts one, but only as a way *down* from a section too large
to return whole; see [`kb serve`](#kb-serve-the-mcp-server) below.)

## `kb serve`: the MCP server

```bash
uv run kb serve --transport stdio    # the default; developer path, nothing on the network
uv run kb serve --transport http     # the shared server; needs an issued token (or --allow-anonymous, dev only)
```

Runs a read-only [MCP](https://modelcontextprotocol.io) server against the index, over
either:

- **stdio**: the assistant starts `kb serve` itself as a subprocess and talks to it over
  stdin/stdout, so there is nothing to bind or expose on the network. This is what
  `uv run kb serve --transport stdio` and the compose `dev` profile are for.
- **streamable HTTP**: one server, run once (by `kb-mcp` in the compose stack), that every
  user's assistant talks to over `https://<KB_PUBLIC_HOST>/mcp`. `kb-mcp` itself binds
  `0.0.0.0:8765` inside the compose network only; `caddy` terminates TLS and is reachable
  from the LAN, on 443. Every request under `/mcp*` needs `Authorization: Bearer <token>`
  where `<token>` is one issued by `kb token issue` (see
  [Tokens](administration.md#tokens-issuing-revoking-rotating)); a missing, wrong, revoked
  or expired token gets a 401 with no body. `/health` (`GET /health`, returning `{"ok":
  true, "documents": <count>, "embed_model": ...}`) needs no token, for monitoring. Starting
  `--transport http` with no usable token refuses to run (exit 2) unless you pass
  `--allow-anonymous`, which is for local experimentation only and logs a warning. Never
  pass it on a network anyone else can reach.

It exposes five tools, each read-only (`readOnlyHint: true`) and documented to the
assistant in its own instructions:

- `search(query, k=8, slug=None, text_class=None)`: find candidate sections for a
  question; returns ids, titles, snippets and citation urls, never full text.
- `fetch(id)`: read the whole section, page, chunk, or document named by an id from
  `search`, `list_documents`, or `get_outline`.
- `list_documents(text_class=None, title_contains=None)`: every indexed document,
  sorted by title, with its size and section count.
- `get_outline(id)`: the section-by-section table of contents for one document.
- `get_section(id, neighbours=0)`: like `fetch` on a section or page, but also pulls
  in `neighbours` sections before and after it, concatenated in reading order.

No reply contains more than `FETCH_MAX_CHARS` (200,000 by default) of text. That ceiling is
not only for a whole document, because a **section is not a bounded unit**. In a DOCX-derived
manual one heading can span thousands of blocks, and a section can exceed half a million
characters, more than the document path already declines to send. So `fetch` and
`get_section` answer an over-cap section, page or chunk the same way the document path
answers an over-cap document: `metadata.truncated: true`, and in place of the text, the
block range it declined plus the smaller ids that cover it: the chunk ids (and page ids
where the document has pages), with one line sampled from a dozen chunks as landmarks for
choosing between them. Fetching one of those chunk ids returns that chunk and nothing else.
An outline listing is bounded the same way: for a document with too many headings to list,
the deeper levels drop out and the reply says so rather than growing without limit. Nothing
is silently cut: every one of these replies says what it left out and which id returns it.

### Use it from Codex

```toml
# ~/.codex/config.toml: developer, local stdio
[mcp_servers.kb]
command = "uv"
args = ["run", "--directory", "/path/to/dgx-infra", "kb", "serve", "--transport", "stdio"]
startup_timeout_sec = 30
tool_timeout_sec = 60

# end user, the shared server on the LAN
[mcp_servers.kb]
url = "https://kb.internal.example/mcp"
bearer_token_env_var = "KB_TOKEN"
tool_timeout_sec = 60
```

Equivalent CLI: `codex mcp add kb -- uv run --directory /path/to/dgx-infra kb serve
--transport stdio`. For the HTTP form, `export KB_TOKEN=<your token>` first and use
`kb.internal.example` replaced with your own `KB_PUBLIC_HOST`.

### Use it from Claude Code

```bash
claude mcp add --transport stdio kb -- uv run --directory /path/to/dgx-infra kb serve --transport stdio
claude mcp add --transport http kb https://kb.internal.example/mcp --header "Authorization: Bearer $KB_TOKEN"
```

Both forms work today. Use `--transport stdio` for local development against a host venv;
use `--transport http` (with `KB_TOKEN` exported and `kb.internal.example` replaced with
your `KB_PUBLIC_HOST`) once `make compose-up` is running.

**Give `uv` its absolute path** in the stdio forms above: `$(command -v uv)`, e.g.
`/Users/you/.local/bin/uv` or `/opt/homebrew/bin/uv`. An MCP stdio server is a subprocess of
the assistant application and inherits *its* environment, not the login shell's, so a `uv`
under `~/.local/bin` (where the standalone installer puts it) or inside a conda environment
is often absent from its PATH, and the server fails to start. Starting the assistant from a
terminal hides the problem; starting it from Finder, the Dock or a desktop launcher does
not. The HTTP forms are unaffected, because nothing is launched as a subprocess there.

The project venv does not need `conda activate`, even when its base interpreter is a conda
environment: `.venv/bin/python` is a symlink straight to that interpreter. Only `uv` itself
has to be findable.

### What you should see

Once added, ask the assistant something the fixtures or your own documents can answer. It
should call `search`, get back a short list of section ids with snippets, call `fetch` (or
`get_section`) on the most promising one or two, and answer using that section's text,
citing the `citation` string it got back, not the question itself. If you watch `kb serve`'s
own stderr (redirected by your assistant's MCP client, not printed to your terminal
directly) you will see one JSON line per tool call: the tool name, its arguments, the
result ids, how long it took, and any error.

**A snippet is not evidence.** `search` returns a 300-character preview of the single best
match. That is enough to judge relevance, not enough to answer from, and never enough to tell
whether a table came out flattened or a note got separated from what it is about. Treat it
as a pointer, not an answer: the assistant should always `fetch` (or `get_section` with
`neighbours=1` when a section looks cut off) before quoting anything back to you.

## Checking a citation against the original: the static `kb/` server

Every result's `url` (and the `citation` string in `fetch`'s metadata) points at
`https://<KB_PUBLIC_HOST>/kb/<file>.md#dgx:block=<id>`, the same converted markdown file
`kb search`/`kb serve` indexed, served read-only by `kb-static` behind Caddy. Opening it in
a browser is the way to check what the assistant told you against the actual converted
text. (The `#dgx:block=...` fragment does nothing in a browser today; it exists so a future
viewer can jump straight to the block, and so the URL is unique per citation.)

**`/kb/*` needs the same bearer token `/mcp*` does**, and a bare browser has no way to add
an `Authorization` header to a request. Three practical options: a browser extension that
adds a fixed header to requests for your `KB_PUBLIC_HOST` origin (e.g. "ModHeader" or
similar); `curl -H "Authorization: Bearer $KB_TOKEN" https://<host>/kb/<file>.md -o file.md`
and open the saved file locally; or, if your organisation's Caddy is set up with a client
TLS certificate instead of `tls internal` (see [TLS](administration.md#tls)), some browsers
can be configured to present it automatically and you drop the header requirement for that
one origin. That is a Caddy/client-cert configuration choice, not something this repo sets
up for you.

## The "flattened table" caveat

Some source tables (merged cells, unusual borders, tables inside scanned images) don't
survive conversion as a clean grid. The converter says so, either with an `INCOMPLETE`
marker or by leaving the table as plain text (see [Reading the converted
markdown](conversion.md#reading-the-converted-markdown), and the per-document [LAYOUT
NOTES](conversion.md#layout-notes) and [EVIDENCE NOTES](conversion.md#evidence-notes) in
`pipeline report`). If an assistant's answer depends on a specific cell, check the citation
against the original before trusting it, using the [static `kb/`
server](#checking-a-citation-against-the-original-the-static-kb-server). Retrieval does not
repair a bad conversion; it only finds and returns what conversion already wrote down.

## What is deliberately not built

A web UI (users bring their own assistant), email intake, answer generation or reranking,
OAuth or any public/internet-facing endpoint, multi-tenancy or per-user document
permissions, and document summaries (`kb catalog --summarize`, optional and gated on
`models.yaml`).
