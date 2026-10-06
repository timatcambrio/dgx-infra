"""An unpinned embedding model must not change an index silently.

`EMBED_MODEL=nomic-embed-text` means `nomic-embed-text:latest`, and what that resolves to
is whatever the registry last published. `index_meta` recorded only the model NAME, so a
retag would re-embed documents with different weights while every name, setting and log
line stayed identical -- and the only symptom would be retrieval quality moving with
nothing to attribute it to.

Measured 2026-10-06, which is what prompted this: the dev Mac and a fresh AWS host both
served digest `0a109f422b47`. Identical by luck. On the same run the proxy-corpus eval came
back 28 points above the recorded floor on the vector leg with the lexical leg unchanged
case for case, and ruling the model out as the cause took a manual digest comparison on two
machines. The index should be able to answer that question itself.
"""

from __future__ import annotations

import httpx
import pytest

from retrieval import embed as embed_module
from retrieval.index import _digest_conflict

TAGS = {
    "models": [
        {"name": "llama3.2:latest", "digest": "a80c4f17acd5"},
        {"name": "nomic-embed-text:latest", "digest": "0a109f422b47"},
    ]
}


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_the_digest_of_the_served_model_is_read() -> None:
    with _client(lambda r: httpx.Response(200, json=TAGS)) as c:
        got = embed_module.model_digest(
            base_url="http://ollama:11434", model="nomic-embed-text", client=c
        )
    assert got == "0a109f422b47"


def test_the_implicit_latest_tag_is_matched() -> None:
    """`/api/tags` always spells the tag out; EMBED_MODEL usually does not. Comparing them
    literally would never match, so the digest would always read as unavailable and the
    check would never run."""
    with _client(lambda r: httpx.Response(200, json=TAGS)) as c:
        assert embed_module.model_digest(
            base_url="http://ollama:11434", model="nomic-embed-text:latest", client=c
        ) == "0a109f422b47"


@pytest.mark.parametrize(
    "handler",
    [
        lambda r: httpx.Response(404, text="not found"),          # an ollama without /api/tags
        lambda r: httpx.Response(200, json={"models": []}),        # model not pulled yet
        lambda r: httpx.Response(200, text="not json"),            # a proxy in the way
    ],
)
def test_an_unreadable_digest_is_none_and_not_an_error(handler) -> None:
    """A check that cannot run must not stop an index. The deployment target may be behind
    a proxy, or running an older ollama, and indexing has to work there."""
    with _client(handler) as c:
        assert embed_module.model_digest(
            base_url="http://ollama:11434", model="nomic-embed-text", client=c
        ) is None


def test_a_retagged_model_is_refused_by_name_and_remedy() -> None:
    meta = {"embed_model": "nomic-embed-text", "embed_dim": "768", "embed_digest": "0a109f422b47"}
    message = _digest_conflict(meta, "ffffffffffff")
    assert message is not None
    assert "0a109f422b47" in message and "ffffffffffff" in message
    assert "--reindex-all" in message, "a refusal has to name the way out"


def test_the_same_digest_passes() -> None:
    meta = {"embed_digest": "0a109f422b47"}
    assert _digest_conflict(meta, "0a109f422b47") is None


def test_an_index_predating_the_check_adopts_the_live_digest() -> None:
    """No recorded digest is not a conflict. Existing deployments must keep indexing
    without a --reindex-all they have no reason to run."""
    assert _digest_conflict({"embed_model": "nomic-embed-text"}, "0a109f422b47") is None


def test_an_unreadable_live_digest_leaves_the_check_unrun() -> None:
    assert _digest_conflict({"embed_digest": "0a109f422b47"}, None) is None
