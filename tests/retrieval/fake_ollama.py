"""A fake ollama `/api/embed` (brief §8.2): deterministic, meaningless vectors.

For each input string, derive a vector from `sha256(text)`: seed a `random.Random` with
the hex digest, draw `dim` uniforms, L2-normalise. Same text -> same vector, always; no
test ever contacts a real model.
"""

from __future__ import annotations

import hashlib
import json
import math
import random

import httpx


def deterministic_vector(text: str, dim: int) -> list[float]:
    seed = hashlib.sha256(text.encode("utf-8")).hexdigest()
    rng = random.Random(seed)
    raw = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
    norm = math.sqrt(sum(x * x for x in raw)) or 1.0
    return [x / norm for x in raw]


def fixed_dim_handler(dim: int, *, requests: list | None = None):
    """An `httpx.MockTransport` handler answering `POST /api/embed` deterministically.

    If `requests` is given, every request is appended to it so a test can inspect the
    body (prefixes, batch size, model name).
    """

    def handler(request: httpx.Request) -> httpx.Response:
        if requests is not None:
            requests.append(request)
        body = json.loads(request.content)
        vectors = [deterministic_vector(t, dim) for t in body["input"]]
        return httpx.Response(200, json={"embeddings": vectors})

    return handler
