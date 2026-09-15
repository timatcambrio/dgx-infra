"""The answerability suite, and the runner underneath it.

Goldens catch change; they cannot catch uselessness. Output can be byte-stable and still fail
to answer the question the document exists to answer -- which is what every silent loss found
in this pipeline so far has looked like. These cases assert that the evidence needed to answer
a real question survives conversion.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import answerability
from pipeline.answerability import Case, SpecError

CASES = Path(__file__).resolve().parent / "answerability" / "fixtures.yaml"

#: Fixtures that must be converted for the case file to have anything to read.
CONVERTED = ("linked_form.pdf", "ruled_form.pdf", "annotated_form.pdf", "born_digital.pdf")


@pytest.fixture
def converted_kb(config, entry_for, tmp_path):
    """Convert the fixtures the cases ask about, and return the directory holding them."""
    from pipeline.convert import convert_entry

    for fixture in CONVERTED:
        result = convert_entry(entry_for(fixture), config)
        assert result.output is not None, f"{fixture} did not convert: {result.message}"
    return config.kb_dir


# --------------------------------------------------------------------------------------
# The cases themselves
# --------------------------------------------------------------------------------------


def test_every_case_passes(converted_kb):
    results = answerability.run(answerability.load_cases(CASES), converted_kb)
    failed = [result for result in results if not result.passed]
    assert not failed, "\n" + answerability.render(results)


def test_the_case_file_is_not_empty():
    """Guards against the suite above passing because it asked nothing."""
    assert len(answerability.load_cases(CASES)) >= 5


# --------------------------------------------------------------------------------------
# The runner
# --------------------------------------------------------------------------------------


def _case(**kwargs):
    base = {"id": "c", "question": "q?", "document": "d.md", "expect": ("needle",)}
    base.update(kwargs)
    return Case(**base)


def test_a_missing_expectation_fails_and_says_which(tmp_path):
    (tmp_path / "d.md").write_text("haystack", encoding="utf-8")
    [result] = answerability.run([_case()], tmp_path)
    assert not result.passed
    assert "missing: 'needle'" in result.failures[0]


def test_a_forbidden_string_fails(tmp_path):
    (tmp_path / "d.md").write_text("needle and chaff", encoding="utf-8")
    [result] = answerability.run([_case(forbid=("chaff",))], tmp_path)
    assert not result.passed
    assert "present but forbidden: 'chaff'" in result.failures[0]


def test_a_missing_document_is_reported_as_such(tmp_path):
    """Not the same problem as a wrong answer, and not to be confused with one."""
    [result] = answerability.run([_case()], tmp_path)
    assert result.missing_document and not result.passed
    assert "document not found" in answerability.render([result])


def test_a_passing_case_passes(tmp_path):
    (tmp_path / "d.md").write_text("a needle in there", encoding="utf-8")
    [result] = answerability.run([_case()], tmp_path)
    assert result.passed


@pytest.mark.parametrize(
    "spec, message",
    [
        ("cases: []", "no cases"),
        ("nothing: true", "no cases"),
        ("cases:\n  - question: q\n    document: d.md\n    expect: [x]", "missing id"),
        ("cases:\n  - id: a\n    question: q\n    document: d.md", "expects nothing"),
        (
            "cases:\n  - id: a\n    question: q\n    document: d.md\n    expect: [x]\n"
            "  - id: a\n    question: q\n    document: d.md\n    expect: [y]",
            "duplicate case id",
        ),
        ("cases:\n  - id: a\n    question: q\n    document: d.md\n    expect: 'x'",
         "list of strings"),
    ],
)
def test_an_unusable_case_file_is_an_error_not_a_skip(tmp_path, spec, message):
    """A silently dropped case leaves the suite green while asking one fewer question."""
    path = tmp_path / "cases.yaml"
    path.write_text(spec, encoding="utf-8")
    with pytest.raises(SpecError, match=message):
        answerability.load_cases(path)


def test_render_names_the_question_that_failed(tmp_path):
    """A failure has to be actionable without opening the case file."""
    [result] = answerability.run([_case(question="Which box?")], tmp_path)
    text = answerability.render([result])
    assert "Which box?" in text and "FAIL c" in text
