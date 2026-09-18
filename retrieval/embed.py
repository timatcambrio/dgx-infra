"""Embeddings: ollama, `nomic-embed-text` (brief §6.3).

`POST {OLLAMA_BASE_URL}/api/embed` with `{"model", "input": [...], "truncate": true}`,
batch <= 32 texts and <= `BATCH_MAX_CHARS` characters (a single longer text travels
alone), response `{"embeddings": [[float...], ...]}` one vector per input in order. The
character bound exists because embedding time grows with characters sent, not with the
number of texts: on the dev Mac (2026-09-18, ollama 0.21, nomic-embed-text on CPU) 32
texts of 2,500 characters took 63 s against the 60 s read timeout, while 32 texts of
~1,200 characters took about half. Row-split tables (chunk.py rule 1) produce runs of
full-size pieces, which is what exposed it.

Prefixes are mandatory: `embed_documents` sends `"search_document: " + t`; `embed_query`
sends `"search_query: " + q`. A dimension mismatch is a hard error naming the model.

Failures are sorted by what a retry can change. A connection error, a 5xx or an unreadable
body is transient: retried twice with a 2s sleep, then raised. A 4xx is a verdict on the
request and is raised at once, with ollama's own `error` text. One 4xx gets special
treatment: "the input length exceeds the context length". Ollama's `truncate: true` is
supposed to make that impossible and, as observed 2026-09-18 against ollama 0.21 with
nomic-embed-text, does not for some inputs (a dotted-leader table of contents of ~4k
characters was refused while 100k characters of plain words were accepted). The client
therefore guarantees fit itself: the offending text is shortened for embedding only,
by a fixed factor at a time, until the model accepts it, and every such event is reported
back to the caller so it is logged and counted. The stored chunk text is never altered
and the lexical search leg sees all of it.

`client` is injectable (an `httpx.Client` wrapping an `httpx.MockTransport` in tests) so no
test ever talks to a real ollama.
"""

from __future__ import annotations

import time
from typing import Optional

import httpx

BATCH_SIZE = 32
#: Characters per request: 32 texts at CHUNK_TARGET (1,200) is ~38k; about 17 s on the
#: dev Mac at the measured ~1 s per 2,400 characters, well inside the 60 s read timeout.
BATCH_MAX_CHARS = 40_000
RETRIES = 2
RETRY_SLEEP_SECONDS = 2.0
#: Context-overflow fallback: each step keeps this fraction of the text, down to the floor.
SHRINK_FACTOR = 0.75
MIN_EMBED_CHARS = 256
CONTEXT_ERROR_MARKER = "context length"

DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class EmbedError(RuntimeError):
    """Embedding request failed, or the response did not match `EMBED_DIM`."""


class _ContextOverflow(Exception):
    """ollama refused the input as longer than the model's context."""


#: One record per text that had to be shortened for embedding: (position in the input
#: list, original length in characters, length actually embedded).
Truncation = tuple[int, int, int]


def _error_text(resp: httpx.Response) -> str:
    try:
        return str(resp.json().get("error") or resp.text[:200])
    except ValueError:
        return resp.text[:200]


def _batched(items: list[str], n: int, max_chars: int = BATCH_MAX_CHARS) -> list[list[str]]:
    """Consecutive runs of at most `n` items and at most `max_chars` characters in total;
    an item longer than `max_chars` is a run of its own. Order is preserved."""
    batches: list[list[str]] = []
    cur: list[str] = []
    cur_chars = 0
    for item in items:
        if cur and (len(cur) >= n or cur_chars + len(item) > max_chars):
            batches.append(cur)
            cur, cur_chars = [], 0
        cur.append(item)
        cur_chars += len(item)
    if cur:
        batches.append(cur)
    return batches


def _post_batch(
    client: httpx.Client, base_url: str, model: str, texts: list[str], embed_dim: int
) -> list[list[float]]:
    """One `/api/embed` call. Transient failures are retried; a 4xx is raised at once."""
    url = f"{base_url.rstrip('/')}/api/embed"
    payload = {"model": model, "input": texts, "truncate": True}

    attempt = 0
    data = None
    while True:
        try:
            resp = client.post(url, json=payload)
            if 400 <= resp.status_code < 500:
                detail = _error_text(resp)
                if CONTEXT_ERROR_MARKER in detail:
                    raise _ContextOverflow(detail)
                raise EmbedError(
                    f"embedding request to {url} for model {model!r} was rejected "
                    f"(HTTP {resp.status_code}): {detail}"
                )
            resp.raise_for_status()
            data = resp.json()
            break
        except (_ContextOverflow, EmbedError):
            raise
        except Exception as exc:  # connection errors, 5xx, bad JSON
            attempt += 1
            if attempt > RETRIES:
                raise EmbedError(
                    f"embedding request to {url} for model {model!r} failed after "
                    f"{attempt} attempts: {exc}"
                ) from exc
            time.sleep(RETRY_SLEEP_SECONDS)

    assert data is not None  # loop above either sets data or raises
    vectors = data.get("embeddings")
    if not isinstance(vectors, list) or len(vectors) != len(texts):
        raise EmbedError(
            f"embedding response from model {model!r} did not contain {len(texts)} "
            f"vectors: {data!r}"
        )
    for v in vectors:
        if len(v) != embed_dim:
            raise EmbedError(
                f"embedding dimension mismatch for model {model!r}: expected {embed_dim}, "
                f"got {len(v)}"
            )
    return vectors


def _embed_batch_guaranteed(
    client: httpx.Client,
    base_url: str,
    model: str,
    texts: list[str],
    embed_dim: int,
    *,
    offset: int,
    truncations: list[Truncation],
) -> list[list[float]]:
    """`_post_batch`, but a context overflow is resolved rather than raised.

    On overflow the batch is embedded one text at a time; a text the model still refuses
    is shortened by `SHRINK_FACTOR` per step until accepted, and recorded in
    `truncations`. Below `MIN_EMBED_CHARS` it is an error: at that size the refusal is
    not about length.
    """
    try:
        return _post_batch(client, base_url, model, texts, embed_dim)
    except _ContextOverflow:
        pass

    out: list[list[float]] = []
    for position, text in enumerate(texts):
        kept = text
        while True:
            try:
                out.extend(_post_batch(client, base_url, model, [kept], embed_dim))
                break
            except _ContextOverflow as exc:
                shorter = int(len(kept) * SHRINK_FACTOR)
                if shorter < MIN_EMBED_CHARS:
                    raise EmbedError(
                        f"model {model!r} refuses text #{offset + position} even at "
                        f"{len(kept)} characters: {exc}"
                    ) from exc
                kept = kept[:shorter]
        if len(kept) != len(text):
            truncations.append((offset + position, len(text), len(kept)))
    return out


def _run(
    texts: list[str],
    prefix: str,
    *,
    base_url: str,
    model: str,
    embed_dim: int,
    client: Optional[httpx.Client] = None,
    truncations: Optional[list[Truncation]] = None,
) -> list[list[float]]:
    own_client = client is None
    client = client or httpx.Client(timeout=60.0)
    events: list[Truncation] = truncations if truncations is not None else []
    try:
        out: list[list[float]] = []
        offset = 0
        for batch in _batched([prefix + t for t in texts], BATCH_SIZE):
            out.extend(
                _embed_batch_guaranteed(
                    client, base_url, model, batch, embed_dim,
                    offset=offset, truncations=events,
                )
            )
            offset += len(batch)
        return out
    finally:
        if own_client:
            client.close()


def embed_documents(
    texts: list[str],
    *,
    base_url: str,
    model: str,
    embed_dim: int,
    client: Optional[httpx.Client] = None,
    truncations: Optional[list[Truncation]] = None,
) -> list[list[float]]:
    """Embed document chunks. If `truncations` is given, every text that had to be
    shortened for the model is appended to it as `(position, original_len, kept_len)`."""
    return _run(
        texts, DOCUMENT_PREFIX, base_url=base_url, model=model, embed_dim=embed_dim,
        client=client, truncations=truncations,
    )


def embed_query(
    query: str,
    *,
    base_url: str,
    model: str,
    embed_dim: int,
    client: Optional[httpx.Client] = None,
) -> list[float]:
    vectors = _run(
        [query], QUERY_PREFIX, base_url=base_url, model=model, embed_dim=embed_dim, client=client
    )
    return vectors[0]
