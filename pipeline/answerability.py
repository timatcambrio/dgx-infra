"""Answerability checks: can the converted markdown still answer known questions?

The conversion pipeline has goldens, and goldens catch *change*. They cannot catch the thing
that actually matters here, because the goal is not markdown that mirrors the source. It is
markdown a reader can answer questions from. A golden is perfectly happy with output that is
byte-stable and useless, and every silent loss found so far -- a budget grid flattened into
prose, a callout stranded from its field -- was byte-stable and useless.

So: a fixed set of questions with known answers, each naming the document it is asked of and
the text that must be present for the answer to be recoverable. A converter change that
improves one document and quietly breaks another shows up here and nowhere else.

Deliberately no model in the loop. Checks are literal substring assertions over the converted
file, which makes them offline, deterministic, free, and reviewable by a person who can
disagree with a case. What they measure is whether *the evidence needed to answer* survived
conversion -- not whether a given model gets the answer right, which is a different question
and not one the converter can control.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


class SpecError(RuntimeError):
    """The case file is unusable. Always actionable in its message."""


@dataclass(frozen=True)
class Case:
    """One question, the document it is asked of, and what answering it requires."""

    id: str
    question: str
    document: str
    #: Substrings that must all be present. The answer is recoverable only if they are.
    expect: tuple[str, ...]
    #: Substrings that must not appear -- usually a wrong anchor a past version produced.
    forbid: tuple[str, ...] = ()


@dataclass(frozen=True)
class Result:
    case: Case
    #: Missing `expect` strings and present `forbid` strings, phrased for a person.
    failures: tuple[str, ...]
    #: Set when the document itself is absent, which is a different problem from a wrong answer.
    missing_document: bool = False

    @property
    def passed(self) -> bool:
        return not self.failures and not self.missing_document


def load_cases(path: Path) -> list[Case]:
    """Parse a case file, rejecting anything ambiguous rather than skipping it.

    A silently dropped case is worse than a crash: the suite still reports green and one
    fewer question is being asked, which is exactly the failure this module exists to stop.
    """
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        raise SpecError(f"Cannot read answerability cases at {path}: {exc}") from exc

    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise SpecError(f"{path} defines no cases under a top-level 'cases:' list.")

    cases: list[Case] = []
    seen: set[str] = set()
    for index, item in enumerate(cases_raw, start=1):
        if not isinstance(item, dict):
            raise SpecError(f"{path}: case {index} is not a mapping.")
        missing = [key for key in ("id", "question", "document") if not item.get(key)]
        if missing:
            raise SpecError(f"{path}: case {index} is missing {', '.join(missing)}.")
        case_id = str(item["id"])
        if case_id in seen:
            raise SpecError(f"{path}: duplicate case id {case_id!r}.")
        seen.add(case_id)
        expect = _strings(item.get("expect"), path, case_id, "expect")
        if not expect:
            raise SpecError(
                f"{path}: case {case_id!r} expects nothing, so it can never fail."
            )
        cases.append(
            Case(
                id=case_id,
                question=str(item["question"]),
                document=str(item["document"]),
                expect=expect,
                forbid=_strings(item.get("forbid"), path, case_id, "forbid"),
            )
        )
    return cases


def _strings(value: Any, path: Path, case_id: str, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise SpecError(f"{path}: case {case_id!r} field {field!r} must be a list of strings.")
    return tuple(value)


def run(cases: list[Case], kb_dir: Path) -> list[Result]:
    """Check every case against the converted markdown in `kb_dir`."""
    return [_run_one(case, kb_dir / case.document) for case in cases]


def _run_one(case: Case, document: Path) -> Result:
    if not document.is_file():
        return Result(case=case, failures=(), missing_document=True)
    text = document.read_text(encoding="utf-8")
    failures = [f"missing: {item!r}" for item in case.expect if item not in text]
    failures += [f"present but forbidden: {item!r}" for item in case.forbid if item in text]
    return Result(case=case, failures=tuple(failures))


def render(results: list[Result]) -> str:
    """A report a person can act on: what failed, for which question, and why."""
    passed = [result for result in results if result.passed]
    lines = [
        "ANSWERABILITY",
        f"  cases   : {len(results)}",
        f"  passed  : {len(passed)}",
        f"  failed  : {len(results) - len(passed)}",
    ]
    for result in results:
        if result.passed:
            continue
        lines += ["", f"  FAIL {result.case.id}", f"    Q: {result.case.question}",
                  f"    in: {result.case.document}"]
        if result.missing_document:
            lines.append("    document not found in kb/ -- has it been converted?")
        lines += [f"    {failure}" for failure in result.failures]
    return "\n".join(lines)
