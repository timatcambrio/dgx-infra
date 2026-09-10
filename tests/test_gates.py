"""Both policy gates, plus the Docling OCR-engine pin.

An unexercised gate is decorative, so the central test here plants `import fitz` in a fake
pipeline directory and requires the license gate to fail on it. That case also covers the
blind spot in metadata-based checking: PyMuPDF is not installed, so nothing in the installed
dependency graph could reveal it — only reading the source can.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = REPO_ROOT / "scripts"


def _load_script(name: str):
    path = SCRIPTS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"_gate_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


license_gate = _load_script("license_gate")
model_gate = _load_script("model_gate")


def _failures(findings):
    return [finding for finding in findings if finding.level == "FAIL"]


# --------------------------------------------------------------------------- license gate


@pytest.mark.parametrize(
    "source",
    [
        "import fitz\n",
        "import fitz as f\n",
        "from fitz import Document\n",
        "import pymupdf\n",
        "import pymupdf4llm\n",
        "from pymupdf4llm import to_markdown\n",
        "def f():\n    import fitz\n",
    ],
)
def test_license_gate_fails_on_banned_import(tmp_path, source):
    """The gate must fire on PyMuPDF however it is spelled or nested."""
    (tmp_path / "leak.py").write_text(source)

    findings = license_gate.run(tmp_path, SCRIPTS / "license_allowlist.yaml")

    failures = _failures(findings)
    assert failures, f"gate did not fail on {source!r}"
    assert any("banned" in finding.detail for finding in failures)


def test_license_gate_reports_where_the_banned_import_is(tmp_path):
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "converter.py").write_text("import fitz\n")

    findings = license_gate.run(tmp_path, SCRIPTS / "license_allowlist.yaml")

    assert any("converter.py" in finding.detail for finding in _failures(findings))


def test_license_gate_passes_on_the_real_pipeline():
    findings = license_gate.run(REPO_ROOT / "pipeline", SCRIPTS / "license_allowlist.yaml")
    assert _failures(findings) == []


def test_license_gate_main_exits_nonzero_on_violation(tmp_path, capsys):
    (tmp_path / "leak.py").write_text("import fitz\n")

    exit_code = license_gate.main(["--pipeline-dir", str(tmp_path)])

    assert exit_code == 1
    assert "FAILED" in capsys.readouterr().out


def test_license_gate_main_passes_on_the_real_pipeline(capsys):
    assert license_gate.main([]) == 0
    assert "PASSED" in capsys.readouterr().out


def test_relative_imports_are_not_treated_as_dependencies(tmp_path):
    (tmp_path / "mod.py").write_text("from . import config\nfrom .converters import pdf\n")

    findings = license_gate.run(tmp_path, SCRIPTS / "license_allowlist.yaml")

    assert _failures(findings) == []


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("MIT", license_gate.PERMISSIVE),
        ("BSD-3-Clause", license_gate.PERMISSIVE),
        ("Apache-2.0", license_gate.PERMISSIVE),
        ("GPL-3.0-only", license_gate.STRONG_COPYLEFT),
        ("License :: OSI Approved :: GNU General Public License v3 (GPLv3)", license_gate.STRONG_COPYLEFT),
        ("AGPL-3.0", license_gate.STRONG_COPYLEFT),
        ("GNU Affero General Public License v3", license_gate.STRONG_COPYLEFT),
        ("LGPL-2.1", license_gate.WEAK_COPYLEFT),
        ("GNU Lesser General Public License v2 (LGPLv2)", license_gate.WEAK_COPYLEFT),
        ("MPL-2.0", license_gate.WEAK_COPYLEFT),
        ("", license_gate.UNKNOWN),
    ],
)
def test_classify_license(text, expected):
    assert license_gate.classify_license(text) == expected


def test_gpl_compatible_is_not_copyleft():
    """The Python-2.0 licence text says 'GPL-compatible'. Naive matching bans half of PyPI."""
    text = "Python Software Foundation License, which is GPL-compatible"
    assert license_gate.classify_license(text) == license_gate.PERMISSIVE


def test_lgpl_is_not_misread_as_gpl():
    """'LGPL' contains 'GPL', so ordering inside the classifier is load-bearing."""
    assert license_gate.classify_license("LGPL-3.0") == license_gate.WEAK_COPYLEFT


# ------------------------------------------------------------------------------ model gate


def test_repo_models_yaml_passes_the_gate():
    models, pending, _ = model_gate.load_models(REPO_ROOT / "models.yaml")
    findings = model_gate.check_allowlist(models) + model_gate.check_pending(pending)
    assert _failures(findings) == []


def test_allowlist_is_empty_in_this_phase():
    """Stage 1 is model-free for text: nothing at all should be approved to download."""
    models, _, _ = model_gate.load_models(REPO_ROOT / "models.yaml")
    assert models == []


def test_pending_models_are_reported_on_every_run(capsys):
    exit_code = model_gate.main(
        ["--models-file", str(REPO_ROOT / "models.yaml"), "--skip-cache-scan"]
    )
    output = capsys.readouterr().out

    assert exit_code == 0
    assert "REVIEW" in output
    assert "TableFormerV2" in output
    assert "docling-layout-heron" in output


def test_unlisted_cached_model_fails(tmp_path):
    (tmp_path / "models--openai--whisper-large").mkdir()

    findings = model_gate.scan_caches([], [], [tmp_path])

    assert any("not in models.yaml" in finding.detail for finding in _failures(findings))


def test_pending_model_found_in_cache_fails(tmp_path):
    """Starting M2 by downloading a model whose provenance is unresolved must break."""
    (tmp_path / "models--docling-project--TableFormerV2").mkdir()
    _, pending, _ = model_gate.load_models(REPO_ROOT / "models.yaml")

    findings = model_gate.scan_caches([], pending, [tmp_path])

    assert any("under review" in finding.detail for finding in _failures(findings))


def test_allowed_cached_model_passes(tmp_path):
    (tmp_path / "models--ibm-granite--granite-docling").mkdir()
    allowed = [{"model": "ibm-granite/granite-docling"}]

    assert _failures(model_gate.scan_caches(allowed, [], [tmp_path])) == []


def test_empty_cache_directory_passes(tmp_path):
    assert model_gate.scan_caches([], [], [tmp_path]) == []


@pytest.mark.parametrize(
    "base_developer",
    ["Alibaba", "Qwen team", "Baidu", "DeepSeek", "ByteDance", "Shanghai AI Lab", "THUDM"],
)
def test_chinese_base_provenance_is_rejected(base_developer):
    entry = {
        "model": "someorg/some-model",
        "license": "apache-2.0",
        "developer": "A US Company",
        "base_model": "some-base",
        "base_developer": base_developer,
    }

    _, problems = model_gate.check_entry(entry, 0)

    assert any("provenance" in problem for problem in problems)


def test_us_finetune_of_a_chinese_base_is_rejected():
    """The rule is judged on the base weights, not on who released the fine-tune."""
    entry = {
        "model": "allenai/olmOCR-2",
        "license": "apache-2.0",
        "developer": "AI2 (US)",
        "base_model": "Qwen2.5-VL-7B",
        "base_developer": "Alibaba",
    }

    _, problems = model_gate.check_entry(entry, 0)

    assert problems


def test_undeclared_license_is_rejected():
    """No licence means all rights reserved, which is the opposite of permissive."""
    entry = {
        "model": "docling-project/TableFormerV2",
        "license": "UNDECLARED",
        "developer": "docling-project",
        "base_model": None,
        "base_developer": "IBM Research",
    }

    _, problems = model_gate.check_entry(entry, 0)

    assert any("all rights are reserved" in problem for problem in problems)


def test_unresolved_provenance_is_rejected():
    entry = {
        "model": "someorg/some-model",
        "license": "apache-2.0",
        "developer": "IBM Research",
        "base_model": "UNCONFIRMED",
        "base_developer": "UNCONFIRMED",
    }

    _, problems = model_gate.check_entry(entry, 0)

    assert any("unresolved" in problem for problem in problems)


def test_non_permissive_license_is_rejected():
    entry = {
        "model": "datalab-to/surya",
        "license": "AI-Pubs-OpenRAIL-M",
        "developer": "Datalab",
        "base_model": "surya",
        "base_developer": "Datalab",
    }

    _, problems = model_gate.check_entry(entry, 0)

    assert any("permissive" in problem for problem in problems)


def test_missing_required_field_is_rejected():
    _, problems = model_gate.check_entry({"model": "a/b", "license": "mit"}, 0)
    assert any("missing required field" in problem for problem in problems)


def test_models_file_without_allowed_models_key_is_fatal(tmp_path):
    path = tmp_path / "models.yaml"
    path.write_text("version: 1\nmodels: []\n")

    with pytest.raises(SystemExit):
        model_gate.load_models(path)


# ------------------------------------------------------------------- Docling OCR engine pin


def test_ocr_engine_is_pinned_in_config():
    from pipeline import config as config_module

    assert config_module.DOCLING_OCR_ENGINE == "easyocr"


def test_provenance_banned_engines_are_named():
    """RapidOCR wraps PaddleOCR (Baidu) weights, so it can never be the engine."""
    from pipeline.config import BANNED_OCR_ENGINES

    assert "rapidocr" in BANNED_OCR_ENGINES
    assert "paddleocr" in BANNED_OCR_ENGINES


@pytest.mark.parametrize("engine", ["rapidocr", "paddleocr", "paddle"])
def test_banned_engine_is_refused(config, monkeypatch, engine):
    from pipeline.converters.pdf import OcrEngineError, build_pipeline_options

    monkeypatch.setenv("DOCLING_OCR_ENGINE", engine)
    from pipeline import config as config_module

    with pytest.raises(OcrEngineError, match="provenance"):
        build_pipeline_options(config_module.load(source_dir=config.source_dir()))


def test_unknown_engine_does_not_fall_through_to_the_docling_default(config, monkeypatch):
    from pipeline.converters.pdf import OcrEngineError, build_pipeline_options
    from pipeline import config as config_module

    monkeypatch.setenv("DOCLING_OCR_ENGINE", "some-new-engine")

    with pytest.raises(OcrEngineError, match="silently"):
        build_pipeline_options(config_module.load(source_dir=config.source_dir()))


def test_pipeline_options_disable_ocr_and_pin_the_engine(config):
    """The pin must hold in the object Docling actually receives, not just in config."""
    from pipeline.converters.pdf import build_pipeline_options

    options = build_pipeline_options(config)

    assert options.do_ocr is False
    assert options.ocr_options.kind == "easyocr"
    assert options.do_table_structure is True


def test_no_enrichment_models_are_enabled(config):
    """Each enrichment is another model download; none is wanted in a model-free phase."""
    from pipeline.converters.pdf import build_pipeline_options

    options = build_pipeline_options(config)

    assert options.do_picture_classification is False
    assert options.do_code_enrichment is False
    assert options.do_formula_enrichment is False


@pytest.mark.parametrize(
    ("model", "base_model"),
    [
        ("docling-project/docling-layout-egret-large", "D-FINE"),
        ("docling-project/docling-layout-egret-medium", "HGNet-V2 backbone"),
    ],
)
def test_egret_layout_variants_fail_on_backbone_provenance(model, base_model):
    """The 'more accurate' layout models are a provenance regression, not an upgrade.

    Docling's egret variants are D-FINE based (USTC) on HGNet-V2 backbones (Baidu
    PaddleClas). Switching layout model for accuracy must not quietly bypass the rule.
    """
    entry = {
        "model": model,
        "license": "apache-2.0",
        "developer": "IBM Research",
        "base_model": base_model,
        "base_developer": "IBM Research",
    }

    _, problems = model_gate.check_entry(entry, 0)

    assert any("provenance" in problem for problem in problems)


def test_bundled_weights_in_an_installed_package_fail(tmp_path):
    """Weights shipped inside a wheel never touch a cache, so the cache scan cannot see them.

    This is not hypothetical: the `docling` meta-package is `docling-slim[standard]`, which
    installs `rapidocr`, whose wheel bundles Baidu PaddleOCR weights as plain files.
    """
    package = tmp_path / "rapidocr" / "models"
    package.mkdir(parents=True)
    (package / "PP-OCRv6_det_small.onnx").write_bytes(b"x" * 2048)

    findings = model_gate.scan_installed_packages(set(), [tmp_path])

    failures = _failures(findings)
    assert failures
    assert failures[0].subject == "rapidocr"
    assert "bypass every cache-based check" in failures[0].detail


@pytest.mark.parametrize(
    "filename", ["model.safetensors", "weights.pt", "net.onnx", "m.pdmodel", "q.gguf"]
)
def test_every_weight_format_is_detected(tmp_path, filename):
    (tmp_path / "somepkg").mkdir()
    (tmp_path / "somepkg" / filename).write_bytes(b"x" * 16)

    assert _failures(model_gate.scan_installed_packages(set(), [tmp_path]))


def test_small_bin_files_are_not_mistaken_for_weights(tmp_path):
    """.bin is far too common to treat as a model outright; size disambiguates."""
    (tmp_path / "somepkg").mkdir()
    (tmp_path / "somepkg" / "lookup.bin").write_bytes(b"x" * 1024)

    assert model_gate.scan_installed_packages(set(), [tmp_path]) == []


def test_bundled_weights_can_be_allowlisted(tmp_path):
    package = tmp_path / "approvedpkg"
    package.mkdir()
    (package / "model.onnx").write_bytes(b"x" * 2048)

    findings = model_gate.scan_installed_packages({"approvedpkg"}, [tmp_path])

    assert _failures(findings) == []


def test_the_real_install_ships_no_bundled_weights():
    """Regression guard: reinstalling `docling` instead of `docling-slim` must break this."""
    assert _failures(model_gate.scan_installed_packages(set())) == []


def test_python_path_files_are_not_mistaken_for_torch_weights(tmp_path):
    """`.pth` is Python's path-configuration extension as well as a PyTorch one.

    site-packages is full of tiny `.pth` files; treating them as weights makes the gate cry
    wolf on every run, which is how a gate ends up switched off.
    """
    (tmp_path / "_virtualenv.pth").write_text("import _virtualenv\n")

    assert model_gate.scan_installed_packages(set(), [tmp_path]) == []


def test_a_real_sized_pth_checkpoint_is_still_caught(tmp_path):
    (tmp_path / "somepkg").mkdir()
    (tmp_path / "somepkg" / "resnet50.pth").write_bytes(b"x" * (BIG := 2_000_000))

    assert _failures(model_gate.scan_installed_packages(set(), [tmp_path]))
