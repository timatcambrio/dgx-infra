"""Layout rule (brief §2.6, §4): `pipeline/` never imports `retrieval/`.

Stage 2 must not fatten or complicate Stage 1. `retrieval/` may import `pipeline.frontmatter`
(and nothing else from `pipeline/`); the reverse is never allowed. Grep-style, at the AST
level so a string that merely mentions "retrieval" cannot trip it.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
PIPELINE_DIR = REPO_ROOT / "pipeline"
RETRIEVAL_DIR = REPO_ROOT / "retrieval"


def _imported_top_level_names(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.module and not node.level:
                names.add(node.module.split(".")[0])
    return names


def test_pipeline_never_imports_retrieval() -> None:
    offenders = []
    for path in sorted(PIPELINE_DIR.rglob("*.py")):
        if "retrieval" in _imported_top_level_names(path):
            offenders.append(path)
    assert not offenders, (
        "pipeline/ must never import retrieval/ (brief §4): "
        f"{[str(p.relative_to(REPO_ROOT)) for p in offenders]}"
    )


def test_retrieval_only_imports_frontmatter_from_pipeline() -> None:
    """`retrieval/` may import `pipeline.frontmatter` and nothing else from `pipeline/`."""
    offenders = []
    for path in sorted(RETRIEVAL_DIR.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module and not node.level:
                if node.module == "pipeline" or node.module.startswith("pipeline."):
                    if node.module not in ("pipeline.frontmatter", "pipeline"):
                        offenders.append((path, node.module))
                    elif node.module == "pipeline":
                        names = {alias.name for alias in node.names}
                        if names - {"frontmatter"}:
                            offenders.append((path, node.module))
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "pipeline" or alias.name.startswith("pipeline."):
                        if alias.name != "pipeline.frontmatter":
                            offenders.append((path, alias.name))
    assert not offenders, (
        "retrieval/ may only import pipeline.frontmatter from pipeline/ (brief §4): "
        f"{offenders}"
    )


def test_make_fixtures_only_imports_frontmatter_from_pipeline() -> None:
    """The fixture generator is the other place brief §4 allows a `pipeline.frontmatter`
    import from."""
    path = Path(__file__).resolve().parent / "make_fixtures.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("pipeline"):
            if node.module != "pipeline.frontmatter":
                offenders.append(node.module)
    assert not offenders
