"""Embeddings: ollama, `nomic-embed-text` (brief §6.3).

`POST {OLLAMA_BASE_URL}/api/embed` with `{"model", "input": [...], "truncate": true}`,
batch <= 32, response `{"embeddings": [[float...], ...]}` one vector per input in order.

Prefixes are mandatory: `embed_documents` sends `"search_document: " + t`; `embed_query`
sends `"search_query: " + q`. A dimension mismatch is a hard error naming the model. A
failed HTTP call is retried twice with a 2s sleep, then raised; a chunk is never skipped.

`client` is injectable (an `httpx.Client` wrapping an `httpx.MockTransport` in tests) so no
test ever talks to a real ollama.
"""

from __future__ import annotations

import time
from typing import Optional

import httpx

BATCH_SIZE = 32
RETRIES = 2
RETRY_SLEEP_SECONDS = 2.0

DOCUMENT_PREFIX = "search_document: "
QUERY_PREFIX = "search_query: "


class EmbedError(RuntimeError):
    """Embedding request failed, or the response did not match `EMBED_DIM`."""


def _batched(items: list[str], n: int) -> list[list[str]]:
    return [items[i : i + n] for i in range(0, len(items), n)]


def _post_batch(
    client: httpx.Client, base_url: str, model: str, texts: list[str], embed_dim: int
) -> list[list[float]]:
    url = f"{base_url.rstrip('/')}/api/embed"
    payload = {"model": model, "input": texts, "truncate": True}

    attempt = 0
    data = None
    last_exc: Optional[Exception] = None
    while attempt <= RETRIES:
        try:
            resp = client.post(url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            break
        except Exception as exc:  # httpx errors, bad JSON, non-2xx
            last_exc = exc
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


def _run(
    texts: list[str],
    prefix: str,
    *,
    base_url: str,
    model: str,
    embed_dim: int,
    client: Optional[httpx.Client] = None,
) -> list[list[float]]:
    own_client = client is None
    client = client or httpx.Client(timeout=60.0)
    try:
        out: list[list[float]] = []
        for batch in _batched(texts, BATCH_SIZE):
            prefixed = [prefix + t for t in batch]
            out.extend(_post_batch(client, base_url, model, prefixed, embed_dim))
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
) -> list[list[float]]:
    return _run(texts, DOCUMENT_PREFIX, base_url=base_url, model=model, embed_dim=embed_dim, client=client)


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
