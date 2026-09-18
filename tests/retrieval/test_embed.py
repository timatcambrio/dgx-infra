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


def test_a_4xx_is_raised_at_once_with_ollamas_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    calls = []
    monkeypatch.setattr("retrieval.embed.time.sleep", lambda s: pytest.fail("must not sleep on a 4xx"))

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(404, json={"error": "model 'm' not found"})

    with pytest.raises(EmbedError, match="model 'm' not found"):
        embed_documents(["a"], base_url=BASE_URL, model="m", embed_dim=EMBED_DIM, client=_client(handler))
    assert len(calls) == 1


def _context_capped_handler(cap: int, requests: list):
    """Refuses any input longer than `cap` characters the way ollama does, else embeds."""
    from fake_ollama import deterministic_vector

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body["input"])
        if any(len(t) > cap for t in body["input"]):
            return httpx.Response(400, json={"error": "the input length exceeds the context length"})
        return httpx.Response(200, json={"embeddings": [deterministic_vector(t, EMBED_DIM) for t in body["input"]]})

    return handler


def test_context_overflow_shortens_only_the_offending_text_and_reports_it() -> None:
    requests: list = []
    texts = ["short one", "x" * 5000, "short two"]
    truncations: list = []
    vectors = embed_documents(
        texts, base_url=BASE_URL, model="m", embed_dim=EMBED_DIM,
        client=_client(_context_capped_handler(1000, requests)), truncations=truncations,
    )
    assert len(vectors) == 3
    assert len(truncations) == 1
    position, original, kept = truncations[0]
    assert position == 1 and original == 5000 + len("search_document: ") and kept <= 1000
    # The short texts were embedded whole; only the long one was cut.
    embedded_singly = [b[0] for b in requests if len(b) == 1]
    assert any(t == "search_document: short one" for t in embedded_singly)
    # The final, accepted request for the long text is within the cap.
    accepted_long = [b[0] for b in requests if len(b) == 1 and b[0].startswith("search_document: x") and len(b[0]) <= 1000]
    assert accepted_long and len(accepted_long[-1]) == kept


def test_context_overflow_is_deterministic() -> None:
    texts = ["y" * 3000]
    outs = []
    for _ in range(2):
        truncations: list = []
        outs.append((embed_documents(texts, base_url=BASE_URL, model="m", embed_dim=EMBED_DIM,
                     client=_client(_context_capped_handler(800, [])), truncations=truncations), truncations))
    assert outs[0] == outs[1]


def test_context_overflow_below_the_floor_is_an_error() -> None:
    with pytest.raises(EmbedError, match="refuses text #0"):
        embed_documents(["z" * 3000], base_url=BASE_URL, model="m", embed_dim=EMBED_DIM,
                        client=_client(_context_capped_handler(10, [])))


def test_index_summary_names_truncated_embeddings() -> None:
    from retrieval.index import IndexResult

    assert "truncated" not in IndexResult(unchanged=1).summary_line
    assert IndexResult(reindexed=2, embeddings_truncated=3).summary_line.endswith(
        "3 embeddings truncated (see stderr)"
    )


def test_a_batch_is_bounded_by_characters_as_well_as_count() -> None:
    """Embedding time grows with the characters sent, not the number of texts: on the dev
    Mac (2026-09-18, ollama 0.21, nomic-embed-text on CPU) 32 texts of 2,500 characters
    took 63 s against a 60 s read timeout, while 32 texts of ~1,200 characters took half
    that. A request therefore never carries more than BATCH_MAX_CHARS characters, except
    that a single text over the cap travels alone; vectors still come back in input order."""
    from retrieval.embed import BATCH_MAX_CHARS, BATCH_SIZE

    requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        requests.append(body["input"])
        return fixed_dim_handler(EMBED_DIM)(request)

    texts = [f"{i:04d}" + "t" * 2496 for i in range(64)] + ["big" * 40000] + ["tail"]
    vectors = embed_documents(
        texts, base_url=BASE_URL, model="m", embed_dim=EMBED_DIM, client=_client(handler)
    )
    assert len(vectors) == len(texts)
    assert [t for batch in requests for t in batch] == ["search_document: " + t for t in texts]
    for batch in requests:
        assert len(batch) <= BATCH_SIZE
        assert sum(len(t) for t in batch) <= BATCH_MAX_CHARS or len(batch) == 1
    assert any(len(batch) == 1 and batch[0].startswith("search_document: big") for batch in requests)
    # order: each vector equals the vector the fake returns for that text on its own
    single = fixed_dim_handler(EMBED_DIM)
    for text, vec in zip(texts[:3], vectors[:3]):
        alone = embed_documents([text], base_url=BASE_URL, model="m", embed_dim=EMBED_DIM, client=_client(single))
        assert alone[0] == vec


def test_truncation_positions_are_absolute_across_variable_batches() -> None:
    from retrieval.embed import BATCH_MAX_CHARS

    truncations: list = []
    big = "w" * (BATCH_MAX_CHARS // 2 + 1)
    texts = [big, big, big, "small"]
    embed_documents(
        texts, base_url=BASE_URL, model="m", embed_dim=EMBED_DIM,
        client=_client(_context_capped_handler(1000, [])), truncations=truncations,
    )
    assert [t[0] for t in truncations] == [0, 1, 2]
