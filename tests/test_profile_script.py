"""The corpus profile script.

It exists to be run where the documents are and nothing else can, which means two of its
properties are load-bearing rather than incidental: it must emit no document text, and it
must fail with a usable message rather than a traceback when pointed somewhere wrong.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "profile_corpus.py"


def run(*arguments, cwd=None):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *arguments],
        capture_output=True, text=True, cwd=cwd,
        env={"PATH": "/usr/bin:/bin", "HOME": str(Path.home())},
    )


def test_it_profiles_a_directory(fixtures_dir):
    result = run("--source-dir", str(fixtures_dir))
    assert result.returncode == 0, result.stderr
    assert "EVIDENCE PROFILE" in result.stdout
    for name in ("born_digital.pdf", "linked_form.pdf", "screenshot_form.pdf"):
        assert name in result.stdout


def test_it_reports_the_evidence_that_distinguishes_the_fixtures(fixtures_dir):
    """The numbers have to be the real ones, not a header with zeroes under it."""
    lines = {
        line.split()[0]: line.split()[1:]
        for line in run("--source-dir", str(fixtures_dir)).stdout.splitlines()
        if line.startswith(("linked_form", "screenshot_form"))
    }
    # Columns after the filename: pages class ch/pg cols ruled bordl annot w/CL fields
    # imgpg img%. linked_form has 5 annotations, 2 with a callout line, 2 form fields.
    assert lines["linked_form.pdf"][6:9] == ["5", "2", "2"]
    # screenshot_form: no annotations, one page that is mostly image.
    assert lines["screenshot_form.pdf"][9] == "1"


def test_it_emits_no_document_text(fixtures_dir):
    """The whole point: a profile of a corpus that cannot leave its machine.

    Filenames and counts only. Any sentence out of a fixture appearing here would mean the
    output cannot be pasted anywhere, which is most of its value gone.
    """
    output = run("--source-dir", str(fixtures_dir)).stdout
    for phrase in (
        "Travel costs are reimbursed",      # born_digital body text
        "TYPE OF SUBMISSION",               # a form field label
        "Use Application",                  # an annotation's contents
        "Federal funds carry over",         # boxed text
    ):
        assert phrase not in output, f"{phrase!r} leaked into the profile"


def test_a_directory_with_no_pdfs_is_an_error_with_a_message(tmp_path):
    result = run("--source-dir", str(tmp_path))
    assert result.returncode == 1
    assert "No PDFs under" in result.stderr
    assert "Traceback" not in result.stderr


def test_a_missing_directory_is_an_error_with_a_message(tmp_path):
    result = run("--source-dir", str(tmp_path / "nope"))
    assert result.returncode == 1
    assert "does not exist" in result.stderr
    assert "Traceback" not in result.stderr

# Deliberately not tested here: the no-SOURCE_DIR error. The script reads the repo's .env
# like every other entry point, so the outcome depends on whether the machine running the
# suite happens to have one — which is the environment dependence `conftest` exists to keep
# out. `config`'s own tests cover that error with the .env loader stubbed.
