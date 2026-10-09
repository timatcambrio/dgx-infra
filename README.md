# dgx-infra: document conversion and search

This repo turns a folder of an organisation's documents into a search service that AI
assistants can use. It runs on one host, the DGX, under Docker Compose, and serves only the
local network.

It has two commands that share one `kb/` folder:

- `pipeline` converts PDF, Word (`.docx` and legacy `.doc`) and CSV files into markdown in
  `kb/`, and reports which documents converted completely and which did not.
- `kb` indexes `kb/` and serves it over MCP to the assistants people already use (Codex,
  ChatGPT desktop, Claude Code, Claude desktop). An assistant searches, reads the matching
  sections, and gets a citation for each one naming the converted file and the original
  page.

The converted markdown is kept in the companion `dgx-knowledge` repo, cloned beside this
one. The source documents are kept outside both repos and are only ever read.

## How it works

Conversion uses no model. PDFs are rebuilt from the positions, sizes and ruling lines of
the characters on each page, so it runs offline and gives the same output every time. What
that cannot recover, such as scanned pages and pictures of tables, is measured, listed in a
report and marked in the converted file, so a gap in the knowledge base is visible.

Search combines full-text matching, for exact codes and form numbers, with an embedding
model running on the DGX's own GPUs, for questions worded differently from the document.
Results are whole sections of a document rather than fragments.

The service is read-only. Each person gets their own credential, which can be withdrawn
without restarting anything or affecting anyone else, and the log records whose credential
each request used. Users reach it on port 443 only.

It does not do OCR, generate answers, or provide a web interface; users ask questions
through their own assistant.

## Documentation

| To | Read |
| --- | --- |
| Install it on the DGX | [Deploying on the DGX](docs/deployment-dgx-guide.md) |
| Explain to IT and security what it installs and runs | [IT brief](docs/deployment-dgx-it-brief.md) |
| Convert documents and read the report and the converted markdown | [Document conversion](docs/conversion.md) |
| Understand search and the MCP tools, or connect an assistant | [Search and the MCP server](docs/search-and-mcp.md) |
| Reindex, manage credentials and certificates, or check a deployment | [Administration](docs/administration.md) |
| Measure whether conversion and search find the right text | [Evaluation](docs/evaluation.md) |
| Work on the code | [Development](docs/development.md) |

## License

Copyright Cambrio LLC 2026. All rights reserved. See [LICENSE](LICENSE).
