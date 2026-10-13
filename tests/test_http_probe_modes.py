"""What `scripts/http_probe.py` expects of a deployment, in each mode it can be in.

Two settings changed what a correct deployment looks like on 2026-10-11, and a probe that
still expects the old shape reports a broken deployment where there is none:

- `KB_AUTH=off`: `/mcp` answers without a token. The probe's own "anonymous is refused"
  check would fail on every such deployment, and refusing to run without a token would
  stop it before any check. `--no-auth` expects the open door instead, and sends no
  `Authorization` header at all, which is what the users' pushed settings will do.
- No document route: `/kb/*` is not served, so the probe checks that it is NOT, instead
  of fetching a document from it.
"""

from __future__ import annotations

import importlib.util
import socket
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROBE = REPO / "scripts" / "http_probe.py"


def _module():
    spec = importlib.util.spec_from_file_location("http_probe_under_test", PROBE)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _closed_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def test_no_token_sends_no_authorization_header() -> None:
    """`Bearer ` with nothing after it is not "no credential": a server with auth on
    rejects it, and one with auth off was never asked to accept it."""
    m = _module()
    assert m._auth_headers(None) == {}
    assert m._auth_headers("abc") == {"Authorization": "Bearer abc"}


def test_no_auth_mode_runs_without_a_token(monkeypatch) -> None:
    """Exit 2 means "could not start the probe". With --no-auth there is no token to
    ask for, so the run must get as far as the network and fail there instead (exit 1)."""
    monkeypatch.delenv("KB_PROBE_TOKEN", raising=False)
    m = _module()
    code = m.main(["--base-url", f"http://127.0.0.1:{_closed_port()}", "--no-auth"])
    assert code == 1


def test_a_token_is_still_required_without_no_auth(monkeypatch) -> None:
    monkeypatch.delenv("KB_PROBE_TOKEN", raising=False)
    m = _module()
    assert m.main(["--base-url", f"http://127.0.0.1:{_closed_port()}"]) == 2


def test_the_probe_no_longer_takes_a_document_to_fetch() -> None:
    """`--kb-file` and `--url-base` existed only to fetch from `/kb/*`."""
    text = PROBE.read_text(encoding="utf-8")
    assert "--kb-file" not in text and "--url-base" not in text
