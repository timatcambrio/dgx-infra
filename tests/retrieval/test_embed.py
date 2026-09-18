"""`retrieval.embed` (brief §6.3) against a fake ollama via `httpx.MockTransport`."""

from __future__ import annotations

import json

import httpx
import pytest

from fake_ollama import fixed_dim_handler

from retrieval.embed import EmbedError, embed_documents, embed_query

EMBED_DIM = 8
BASE_URL = "http://fake-ollama"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_embed_documents_uses_search_document_prefix() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        vectors = [[0.0] * EMBED_DIM for _ in body["input"]]
        return httpx.Response(200, json={"embeddings": vectors})

    vectors = embed_documents(
        ["alpha", "beta"],
        base_url=BASE_URL,
        model="nomic-embed-text",
        embed_dim=EMBED_DIM,
        client=_client(handler),
    )
    assert len(vectors) == 2
    assert len(requests) == 1
    body = json.loads(requests[0].content)
    assert body["input"] == ["search_document: alpha", "search_document: beta"]
    assert body["model"] == "nomic-embed-text"
    assert body["truncate"] is True
    assert requests[0].url.path == "/api/embed"


def test_embed_query_uses_search_query_prefix() -> None:
    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        body = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.0] * EMBED_DIM for _ in body["input"]]})

    vector = embed_query(
        "a question",
        base_url=BASE_URL,
        model="nomic-embed-text",
        embed_dim=EMBED_DIM,
        client=_client(handler),
    )
    assert len(vector) == EMBED_DIM
    body = json.loads(requests[0].content)
    assert body["input"] == ["search_query: a question"]


def test_batching_is_at_most_32_per_call() -> None:
    call_sizes = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        call_sizes.append(len(body["input"]))
        return httpx.Response(200, json={"embeddings": [[0.0] * EMBED_DIM for _ in body["input"]]})

    texts = [f"text-{i}" for i in range(75)]
    vectors = embed_documents(
        texts, base_url=BASE_URL, model="m", embed_dim=EMBED_DIM, client=_client(handler)
    )
    assert len(vectors) == 75
    assert call_sizes == [32, 32, 11]


def test_dimension_mismatch_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        return httpx.Response(200, json={"embeddings": [[0.0] * (EMBED_DIM - 1) for _ in body["input"]]})

    with pytest.raises(EmbedError, match="dimension mismatch"):
        embed_documents(
            ["a"], base_url=BASE_URL, model="bad-model", embed_dim=EMBED_DIM, client=_client(handler)
        )


def test_retries_twice_then_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    sleeps = []
    monkeypatch.setattr("retrieval.embed.time.sleep", lambda s: sleeps.append(s))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(500, text="boom")

    with pytest.raises(EmbedError):
        embed_documents(
            ["a"], base_url=BASE_URL, model="m", embed_dim=EMBED_DIM, client=_client(handler)
        )
    assert len(calls) == 3  # original attempt + 2 retries
    assert sleeps == [2.0, 2.0]


def test_fake_ollama_helper_is_deterministic() -> None:
    handler = fixed_dim_handler(EMBED_DIM)
    v1 = embed_query("same text", base_url=BASE_URL, model="m", embed_dim=EMBED_DIM, client=_client(handler))
    v2 = embed_query("same text", base_url=BASE_URL, model="m", embed_dim=EMBED_DIM, client=_client(handler))
    assert v1 == v2
    v3 = embed_query("different text", base_url=BASE_URL, model="m", embed_dim=EMBED_DIM, client=_client(handler))
    assert v3 != v1
