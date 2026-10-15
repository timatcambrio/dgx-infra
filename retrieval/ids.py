"""The four id forms used across the retrieval package and the MCP tools.

```
doc:<slug>
sec:<slug>:<section_index>
page:<slug>:p<NNN>              # NNN zero-padded to 3
chunk:<slug>:<chunk_index>
```

`search` returns section ids; `fetch` accepts all four. Slugs match conversion's guarantee
(`pipeline.manifest.slug_for`): one or more `[a-z0-9][a-z0-9-]*` parts joined by `__`,
one part per source folder, so `gsa/travel.pdf` is `gsa__travel-pdf`.
"""

from __future__ import annotations

import re
from typing import Literal

IdKind = Literal["doc", "sec", "page", "chunk"]

# The folder separator `__` was missing until 2026-10-15: every document in a SOURCE_DIR
# subfolder was indexed and searchable, but no id naming it would parse.
_SLUG_PART = r"[a-z0-9][a-z0-9-]*"
_SLUG = rf"{_SLUG_PART}(?:__{_SLUG_PART})*"
_DOC_RE = re.compile(rf"^doc:({_SLUG})$")
_SEC_RE = re.compile(rf"^sec:({_SLUG}):(\d+)$")
_PAGE_RE = re.compile(rf"^page:({_SLUG}):p(\d{{3}})$")
_CHUNK_RE = re.compile(rf"^chunk:({_SLUG}):(\d+)$")


def doc_id(slug: str) -> str:
    return f"doc:{slug}"


def sec_id(slug: str, section_index: int) -> str:
    return f"sec:{slug}:{section_index}"


def page_id(slug: str, page: int) -> str:
    return f"page:{slug}:p{page:03d}"


def chunk_id(slug: str, chunk_index: int) -> str:
    return f"chunk:{slug}:{chunk_index}"


def parse_id(s: str) -> tuple[IdKind, str, int | None]:
    """Parse any of the four id forms. Raises `ValueError` on anything else."""
    if m := _DOC_RE.match(s):
        return "doc", m.group(1), None
    if m := _SEC_RE.match(s):
        return "sec", m.group(1), int(m.group(2))
    if m := _PAGE_RE.match(s):
        return "page", m.group(1), int(m.group(2))
    if m := _CHUNK_RE.match(s):
        return "chunk", m.group(1), int(m.group(2))
    raise ValueError(f"not a recognised kb id: {s!r}")
