"""An output directory the pipeline cannot write is named, not thrown as a traceback.

Measured on a Linux deployment host, 2026-10-07. Converting in a container without
`--user "$(id -u):$(id -g)"` leaves KB_PATH writable only by the login user while the
process runs as the image's uid 10001, and `pipeline inventory` then died inside
`manifest.save`'s `path.write_text` with a bare `PermissionError` rendered as a forty-line
traceback — naming neither the directory, nor the uid, nor the fix. The command most
likely to hit this is the longest one in the deployment runbook, on someone's first
deployment, so the failure has to read as one line rather than as a crash in our code.

The check is deliberately a pre-flight rather than a `try` around each write: it fails
before any work instead of part-way through a corpus, and there are three write sites
(`manifest.py:save`, and the markdown and sidecar in `convert.py`) that would otherwise
each need the same wrapper.
"""

from __future__ import annotations

import os
import shutil
import stat
from pathlib import Path

import pytest
from typer.testing import CliRunner

from pipeline import config as config_module
from pipeline.config import ConfigError
from pipeline.cli import app

FIXTURES = Path(__file__).resolve().parent / "fixtures"
runner = CliRunner()

#: Root ignores the write bit, so these cannot be exercised as root at all.
not_as_root = pytest.mark.skipif(
    hasattr(os, "getuid") and os.getuid() == 0,
    reason="root bypasses directory permissions, so an unwritable directory is not one",
)


@pytest.fixture
def unwritable_workspace(tmp_path, monkeypatch):
    """A readable source directory and an output directory nobody may write."""
    source = tmp_path / "docs"
    source.mkdir()
    shutil.copy(FIXTURES / "simple.docx", source / "simple.docx")

    kb = tmp_path / "dgx-knowledge"
    kb.mkdir()
    kb.chmod(stat.S_IRUSR | stat.S_IXUSR)  # r-x------: readable, not writable
    monkeypatch.setenv("KB_PATH", str(kb))
    yield kb, source
    kb.chmod(stat.S_IRWXU)  # so tmp_path cleanup can remove it


@not_as_root
def test_an_unwritable_output_directory_is_a_config_error(unwritable_workspace):
    """The check names the directory, so the message is actionable on its own."""
    kb, _ = unwritable_workspace
    config = config_module.load()

    with pytest.raises(ConfigError) as caught:
        config.writable_kb_path()

    assert str(kb) in str(caught.value)


@not_as_root
@pytest.mark.parametrize("command", ["inventory", "triage", "convert", "prune"])
def test_the_write_commands_say_so_instead_of_raising(command, unwritable_workspace):
    """Every command that writes fails the same way, before it does any work.

    `prune` is included because it deletes kb/ output and rewrites the manifest; a dry run
    that cannot write is still worth refusing early rather than at the end.
    """
    kb, source = unwritable_workspace

    result = runner.invoke(app, [command, "--source-dir", str(source)])
    output = result.output + (result.stderr if result.stderr_bytes else "")

    assert result.exit_code == 1, output
    assert str(kb) in output
    assert "Traceback" not in output
    assert "PermissionError" not in output
    assert result.exception is None or isinstance(result.exception, SystemExit)


@not_as_root
def test_the_message_names_the_uid_and_the_container_fix(unwritable_workspace):
    """What the host run actually needed: which user it is, and the flag that fixes it.

    Naming the uid is what distinguishes this from an ordinary permissions problem — the
    directory looks fine to the person reading the error, because it is writable by *them*.
    """
    config = config_module.load()

    with pytest.raises(ConfigError) as caught:
        config.writable_kb_path()

    message = str(caught.value)
    assert f"uid {os.getuid()}" in message
    assert "--user" in message


def test_a_writable_output_directory_passes_and_need_not_exist_yet(tmp_path, monkeypatch):
    """The check must not break a first run, where KB_PATH has never been created.

    `manifest.save` creates the parents itself, so the question is whether the nearest
    existing ancestor is writable, not whether the directory is already there. A check that
    required it to exist would refuse every fresh clone.
    """
    kb = tmp_path / "dgx-knowledge"
    monkeypatch.setenv("KB_PATH", str(kb))

    config = config_module.load()

    assert config.writable_kb_path() == kb
    assert not kb.exists()
