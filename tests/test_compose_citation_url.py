"""Why `KB_URL_BASE` must not end in `/kb`, and what pins that from each side.

Measured 2026-10-01 against the real HTTPS stack, by following a citation instead of
assuming it: `fetch` returned

    url: https://<host>/kb/kb/budget-form.md#dgx:block=budget-form:p001:b001

and that url answered **404**. Every citation link in the deployment was dead — the one
thing the `/kb/*` route exists for ("check a citation against the original") — while every
other check passed, because nothing had ever followed one end to end.

The segment is doubled because two things both supply it:

  * `retrieval/index.py` stores `rel_path` as `kb/<file>.md`, relative to `KB_PATH`, and
    `retrieval/server.py:_url` joins it onto `KB_URL_BASE` verbatim;
  * `compose/Caddyfile` routes `/kb/*` and strips that prefix before `kb-static`, which is
    rooted at `${KB_PATH}/kb`.

So `KB_URL_BASE` has to be the site root, and `.env.example` documented
`https://kb.internal.example/kb`. With the `/kb` removed the same citation answered 200.

The other repair — rooting `kb-static` at `${KB_PATH}` so the doubled path resolves — is
the wrong one, and `test_kb_static_is_rooted_at_the_corpus` is here to stop it: `KB_PATH`
is the knowledge repo, holding the source documents, the manifest and the disposable work
cache. Serving all of that to anyone holding a token, to repair a URL, trades a dead link
for an exposure.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parent.parent


def test_the_example_env_does_not_document_a_doubled_segment() -> None:
    """The example is what a deployment copies, so a wrong one ships to every machine."""
    for line in (REPO / ".env.example").read_text(encoding="utf-8").splitlines():
        stripped = line.lstrip("# ").strip()
        if stripped.startswith("KB_URL_BASE="):
            value = stripped.split("=", 1)[1]
            assert not re.search(r"/kb/?$", value), (
                f"{value!r} doubles the /kb segment — see this module's docstring"
            )


def test_compose_env_check_refuses_a_doubled_segment() -> None:
    """`make compose-up` already refuses a relative KB_PATH and a mismatched token path.
    This is the third trap of the same kind: silent, and invisible until someone clicks a
    citation."""
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    block = makefile.split("compose-env-check:")[1].split("\n\n")[0]
    # The existing "no .env" message merely NAMES the variable, so look for a line that
    # inspects its value.
    assert "KB_URL_BASE=.*/kb" in block, (
        "nothing stops a deployment from setting KB_URL_BASE=https://host/kb, which makes "
        "every citation url a 404"
    )


def test_the_indexer_stores_paths_relative_to_kb_path() -> None:
    """The fact the rule rests on. If `rel_path` ever stopped carrying the `kb/` prefix,
    `KB_URL_BASE` would have to gain it back and this whole module would invert."""
    text = (REPO / "retrieval" / "index.py").read_text(encoding="utf-8")
    assert 'f"kb/{f.name}"' in text, (
        "rel_path no longer hardcodes the kb/ prefix; re-check KB_URL_BASE's meaning"
    )


def test_the_url_builder_joins_the_two_verbatim() -> None:
    """The other fact: `_url` does no stripping, so the two halves must not overlap."""
    text = (REPO / "retrieval" / "server.py").read_text(encoding="utf-8")
    assert 'f"{cfg.kb_url_base}/{rel_path}#dgx:block={block_id}"' in text


def test_kb_static_is_rooted_at_the_corpus() -> None:
    """Not at `KB_PATH` — see this module's docstring for why that repair is refused."""
    compose = yaml.safe_load((REPO / "compose" / "docker-compose.yml").read_text(encoding="utf-8"))
    mounts = compose["services"]["kb-static"]["volumes"]
    assert len(mounts) == 1, mounts
    source, target = mounts[0].rsplit(":", 1)[0].rsplit(":", 1)[0], "/kb"
    assert mounts[0].endswith(":/kb:ro"), mounts[0]
    assert source.endswith("/kb"), (
        f"{source!r} must be ${{KB_PATH}}/kb: rooting kb-static at KB_PATH itself would "
        "serve the source documents and the manifest to every token holder"
    )
