"""Stage 2: index and serve the `kb/` folder Stage 1 produces.

Read-only from `pipeline`'s point of view: this package may import `pipeline.frontmatter`
(to validate frontmatter and to render synthetic fixtures) and nothing else from
`pipeline/`. `pipeline/` never imports this package — see `tests/retrieval/test_layout.py`.

Everything here lives behind the `serve` extra (`uv sync --extra serve`). A bare `uv sync`
must install only the Stage 1 conversion CLI.
"""

from __future__ import annotations
