"""The probe must judge the deployment, not the machine it is run from.

Measured 2026-10-06. A correct AWS stack, probed from the dev Mac, reported three
failures and none of them were real:

    FAIL  /kb/* serves a converted document to a valid token — HTTP 404
    FAIL  /kb/* returns the document, not a redirect or an index — 0 chars
    FAIL  the citation url points into KB_URL_BASE — https://ec2-...../kb/budget-form.md

Both causes were the probe reading the local checkout. The document to request came from
`sorted((cfg.kb_path / "kb").glob("*.md"))[0]`, which on that Mac is the first of twelve
proxy documents, asked of a server holding four fixtures -- a correct 404 about nothing.
And the citation url was compared against the Mac's own `KB_URL_BASE`, still
`http://localhost/kb`, so a correct url failed and the "actually fetchable" check that
follows it never ran (16 checks reported instead of 17).

The same run from the host passed 17/17, which is what makes this worth pinning: the
failure appears only in the mode the module docstring advertises -- "from any client
machine" -- and looks exactly like a broken deployment.
"""

from __future__ import annotations

import importlib.util
import re
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "scripts" / "http_probe.py"


def _module():
    spec = importlib.util.spec_from_file_location("http_probe_under_test", PROBE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def test_a_citation_url_is_judged_against_the_address_being_probed() -> None:
    m = _module()
    assert m._resolve_url_base(None, "https://kb.example") == "https://kb.example"


def test_a_trailing_slash_does_not_make_a_correct_url_fail() -> None:
    """`--base-url https://host/` and a citation of `https://host/kb/x.md` differ by one
    character before the comparison, which is the kind of thing that reads as a server bug."""
    m = _module()
    assert m._resolve_url_base(None, "https://kb.example/") == "https://kb.example"


def test_an_explicit_url_base_still_wins() -> None:
    """A deployment may publish citations under a different name from the one dialled."""
    m = _module()
    assert m._resolve_url_base("https://cite.example/", "https://kb.example") == "https://cite.example"


def test_the_local_env_is_not_consulted_for_the_citation_check() -> None:
    """The specific regression. `cfg.kb_url_base` is this machine's setting, and on a
    client machine it describes somebody else's deployment."""
    source = PROBE.read_text(encoding="utf-8")
    call = re.search(r"probe_mcp\(([^)]*)\)", source, re.S)
    assert call, "probe_mcp is no longer called the way this test reads it"
    assert "cfg.kb_url_base" not in call.group(1), call.group(1)


def test_the_document_to_request_comes_from_the_server() -> None:
    """Not from a local directory, which on a client machine holds a different corpus or
    none at all."""
    source = PROBE.read_text(encoding="utf-8")
    assert "_discover_kb_file" in source
    assert "list_documents" in source, "the server has to be asked what it serves"


def test_a_slug_maps_to_the_converted_file() -> None:
    m = _module()
    assert m._kb_file_for_slug("budget-form") == "budget-form.md"
