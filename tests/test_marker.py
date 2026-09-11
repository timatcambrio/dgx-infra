"""The Marker evaluation engine.

Everything here guards a property that is silent when it breaks. A Marker run that quietly
used a GPU, wrote into SOURCE_DIR, sent page text to a hosted LLM, or produced output with
no licence banner would all still look like a successful conversion.
"""

from __future__ import annotations

import ast
import shutil
from pathlib import Path

import pytest
import yaml

from pipeline import config as config_module
from pipeline.converters import pdf, pdf_marker
from pipeline.converters.pdf_marker import MarkerError

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def marker_config(config, monkeypatch):
    monkeypatch.setenv("KB_PATH", str(config.kb_path))
    monkeypatch.setenv("PDF_ENGINE", "marker")
    return config_module.load(source_dir=config.source_dir())


def test_docker_command_pins_cpu_and_mounts_the_source_read_only(marker_config, fixtures_dir):
    command = pdf_marker.build_command(
        fixtures_dir / "born_digital.pdf", Path("/tmp/out"), marker_config
    )
    joined = " ".join(command)

    assert command[:3] == ["docker", "run", "--rm"]
    assert "--platform" in command and "linux/amd64" in command
    assert f"{fixtures_dir}:/in:ro" in command, "SOURCE_DIR must be mounted read-only"
    assert "-e" in command and "TORCH_DEVICE=cpu" in command
    assert "FAST_DETECTOR_DEVICE=cpu" in command
    assert marker_config.marker_image in command
    assert "/in/born_digital.pdf" in joined
    assert "--output_format markdown" in joined


def test_hosted_llm_keys_are_blanked(marker_config, fixtures_dir):
    """Marker calls a hosted LLM if handed a key. For this corpus that is a disclosure."""
    command = pdf_marker.build_command(
        fixtures_dir / "born_digital.pdf", Path("/tmp/out"), marker_config
    )
    for name in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"):
        assert f"{name}=" in command, f"{name} must be explicitly blanked"
        assert not any(item.startswith(f"{name}=") and len(item) > len(name) + 1 for item in command)


def test_local_runner_invokes_the_binary_directly(marker_config, fixtures_dir, monkeypatch):
    monkeypatch.setenv("MARKER_RUNNER", "local")
    config = config_module.load(source_dir=marker_config.source_dir())
    command = pdf_marker.build_command(
        fixtures_dir / "born_digital.pdf", Path("/tmp/out"), config
    )
    assert command[0] == "marker_single"
    assert "docker" not in command


def test_unknown_runner_is_an_error_not_a_silent_default(marker_config, fixtures_dir, monkeypatch):
    monkeypatch.setenv("MARKER_RUNNER", "kubernetes")
    config = config_module.load(source_dir=marker_config.source_dir())
    with pytest.raises(MarkerError, match="Unknown MARKER_RUNNER"):
        pdf_marker.build_command(fixtures_dir / "x.pdf", Path("/tmp/out"), config)


def test_a_missing_docker_says_why_the_container_exists(marker_config, fixtures_dir, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    with pytest.raises(MarkerError) as excinfo:
        pdf_marker.convert(fixtures_dir / "born_digital.pdf", marker_config)
    message = str(excinfo.value)
    assert "make marker-image" in message
    assert "torch" in message, "the message must explain why marker is not simply installed"


def test_engine_dispatch_routes_to_marker(marker_config, fixtures_dir, monkeypatch):
    called: list[Path] = []
    monkeypatch.setattr(
        pdf_marker, "convert", lambda path, config: (called.append(path), ("body", "marker"))[1]
    )
    body, converter = pdf.convert(fixtures_dir / "born_digital.pdf", marker_config)
    assert called == [fixtures_dir / "born_digital.pdf"]
    assert (body, converter) == ("body", "marker")


def test_output_carries_the_not_for_delivery_banner(marker_config, tmp_path):
    out = tmp_path / "out" / "doc"
    out.mkdir(parents=True)
    (out / "doc.md").write_text("# Heading\n\nBody text.\n", encoding="utf-8")

    body = pdf_marker._read_output(out.parent, Path("doc.pdf"), marker_config)
    assert body.startswith("> **EVALUATION OUTPUT — NOT FOR DELIVERY.**")
    assert "RAIL-M" in body
    assert "Body text." in body


def test_extracted_images_are_named_rather_than_silently_dropped(marker_config, tmp_path):
    out = tmp_path / "out" / "doc"
    out.mkdir(parents=True)
    (out / "doc.md").write_text("![](_page_1_Figure_2.png)\n", encoding="utf-8")
    (out / "_page_1_Figure_2.png").write_bytes(b"\x89PNG")

    body = pdf_marker._read_output(out.parent, Path("doc.pdf"), marker_config)
    assert "_page_1_Figure_2.png" in body
    assert "do not resolve" in body


def test_no_markdown_is_an_error_not_an_empty_document(marker_config, tmp_path):
    out = tmp_path / "out"
    out.mkdir()
    with pytest.raises(MarkerError, match="produced no markdown"):
        pdf_marker._read_output(out, Path("doc.pdf"), marker_config)


def test_the_pipeline_never_imports_marker_surya_or_torch():
    """Call style, not licence string — the same rule that governs LibreOffice.

    Importing marker is not a licence violation (its code is Apache-2.0); it is a platform
    and provenance violation. It would pull a second torch into the project's venv, which
    `tool.uv.required-environments` cannot resolve on darwin-x86_64 at all, and it would put
    RAIL-M-licensed model loading inside our own process.
    """
    banned = {"marker", "marker_pdf", "surya", "torch", "transformers"}
    offenders: list[str] = []
    for path in sorted((REPO_ROOT / "pipeline").rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
                names = [node.module.split(".")[0]]
            else:
                continue
            offenders += [f"{path.name}: {name}" for name in names if name in banned]
    assert offenders == []


def test_models_yaml_lists_the_surya_weights_as_evaluation_only():
    data = yaml.safe_load((REPO_ROOT / "models.yaml").read_text(encoding="utf-8"))
    evaluation = {entry["model"] for entry in data.get("evaluation_only") or []}
    assert "datalab-to/surya-ocr-2" in evaluation
    assert "s3://text_detection/2025_05_07" in evaluation, (
        "surya's non-HuggingFace weights must be listed, or the gate cannot see them"
    )
    # None of them may be on the allowlist, whatever else changes.
    allowed = {entry["model"] for entry in data.get("allowed_models") or []}
    assert allowed & evaluation == set()


def test_the_datalab_cache_is_scanned_by_the_model_gate(marker_config):
    import sys

    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import model_gate

    assert marker_config.marker_cache_dir in model_gate.default_cache_dirs()


def test_evaluation_output_never_lands_in_kb(marker_config, entry_for, monkeypatch):
    """`kb/` is the deliverable. RAIL-M output must not be able to reach it."""
    from pipeline import convert as convert_module

    monkeypatch.setattr(
        pdf_marker, "convert", lambda path, config: ("# Evaluation body\n", "marker")
    )
    entry = entry_for("born_digital.pdf")
    result = convert_module.convert_entry(entry, marker_config, force=True)

    assert result.output is not None
    assert convert_module.EVAL_OUTPUT_DIR in result.output.parts
    assert marker_config.kb_dir not in result.output.parents
    assert not marker_config.kb_dir.exists(), "an evaluation must not create kb/ at all"


def test_an_evaluation_records_nothing_in_the_manifest(marker_config, entry_for, monkeypatch):
    """corpus.yaml is committed; it must not claim provenance from an undelivered engine."""
    from pipeline import convert as convert_module

    monkeypatch.setattr(
        pdf_marker, "convert", lambda path, config: ("# Evaluation body\n", "marker")
    )
    entry = entry_for("born_digital.pdf")
    convert_module.convert_entry(entry, marker_config, force=True)

    assert entry.get("conversion") is None
