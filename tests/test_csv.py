"""CSV conversion and its guardrails.

The guardrail tests matter more than the happy path. An oversized table that converts
"successfully" is the failure mode: it produces a `kb/` file that looks fine, retrieves
badly, and quietly settles a retrieval design question nobody was asked about.
"""

from __future__ import annotations

import pytest

from pipeline import StopAndAsk
from pipeline.converters.csv_table import (
    convert,
    escape_cell,
    prettify_filename,
    read_rows,
    render_table,
)


def test_reference_table_converts(fixtures_dir, config):
    body, converter = convert(fixtures_dir / "reference_table.csv", config)

    assert body.startswith("# Reference Table\n")
    assert "| Code | Expense category | Limit | Receipt required |" in body
    assert "| --- | --- | --- | --- |" in body
    assert body.count("\n|") == 10  # header + separator + 8 data rows
    assert "csv" in converter


def test_title_and_description_from_manifest(fixtures_dir, config):
    body, _ = convert(
        fixtures_dir / "reference_table.csv",
        config,
        title="FY26 Expense Limits",
        description="Effective 1 July 2026.",
    )

    assert body.startswith("# FY26 Expense Limits\n\nEffective 1 July 2026.\n")


def test_pipe_in_cell_is_escaped(fixtures_dir, config):
    """The fixture deliberately contains a pipe, which would otherwise split a column."""
    body, _ = convert(fixtures_dir / "reference_table.csv", config)

    assert r"Conference registration \| fees" in body
    for line in [line for line in body.splitlines() if line.startswith("|")]:
        assert line.count("|") - line.count(r"\|") == 5


def test_oversized_csv_raises_rather_than_emitting(fixtures_dir, config):
    with pytest.raises(StopAndAsk) as excinfo:
        convert(fixtures_dir / "too_big.csv", config)

    message = str(excinfo.value)
    assert "400 data rows" in message
    assert "CSV_MAX_ROWS" in message


def test_row_limit_comes_from_config(fixtures_dir, monkeypatch):
    from pipeline import config as config_module

    monkeypatch.setenv("CSV_MAX_ROWS", "1000")
    permissive = config_module.load(source_dir=fixtures_dir)

    body, _ = convert(fixtures_dir / "too_big.csv", permissive)
    assert body.count("\n|") == 402


def test_too_many_columns_raises(tmp_path, config):
    wide = tmp_path / "wide.csv"
    wide.write_text(
        ",".join(f"col{index}" for index in range(20)) + "\n" + ",".join("x" * 20) + "\n"
    )

    with pytest.raises(StopAndAsk, match="CSV_MAX_COLS"):
        convert(wide, config)


def test_single_column_raises(tmp_path, config):
    single = tmp_path / "single.csv"
    single.write_text("heading\nvalue one\nvalue two\n")

    with pytest.raises(StopAndAsk, match="single-column"):
        convert(single, config)


def test_empty_file_raises(tmp_path, config):
    empty = tmp_path / "empty.csv"
    empty.write_text("   \n\n")

    with pytest.raises(StopAndAsk, match="empty"):
        convert(empty, config)


def test_record_mode_is_a_stop_and_ask(fixtures_dir, config):
    """The interface exists so the schema is stable; the behaviour is deliberately absent."""
    with pytest.raises(StopAndAsk, match="record"):
        convert(fixtures_dir / "reference_table.csv", config, csv_mode="record")


def test_semicolon_dialect_is_sniffed(tmp_path, config):
    semicolons = tmp_path / "euro.csv"
    semicolons.write_text("Code;Label\nLDG;Lodging\nMEA;Meals\n", encoding="utf-8")

    body, _ = convert(semicolons, config)

    assert "| Code | Label |" in body
    assert "| LDG | Lodging |" in body


def test_bom_is_stripped(tmp_path, config):
    with_bom = tmp_path / "bom.csv"
    with_bom.write_bytes("Code,Label\nLDG,Lodging\n".encode("utf-8-sig"))

    body, _ = convert(with_bom, config)

    assert "| Code | Label |" in body


def test_ragged_rows_are_padded_to_header_width():
    table = render_table([["a", "b", "c"], ["1"], ["1", "2", "3", "4"]])

    for line in table.splitlines():
        assert line.count("|") == 4


@pytest.mark.parametrize(
    ("stem", "expected"),
    [
        ("reference_table", "Reference Table"),
        ("travel-rates-2024", "Travel Rates 2024"),
        ("RF_SUNY_rates", "RF SUNY Rates"),
    ],
)
def test_prettify_filename(tmp_path, stem, expected):
    assert prettify_filename(tmp_path / f"{stem}.csv") == expected


def test_newlines_in_cells_do_not_break_the_table():
    assert escape_cell("line one\nline two") == "line one line two"


def test_blank_rows_are_dropped(tmp_path):
    path = tmp_path / "gaps.csv"
    path.write_text("a,b\n1,2\n,\n3,4\n")

    assert read_rows(path) == [["a", "b"], ["1", "2"], ["3", "4"]]


# --------------------------------------------------------------- shared markdown tidying


def test_blank_line_inserted_before_a_table_after_a_list():
    """GFM ignores a table that starts on the line after list content."""
    from pipeline.converters import normalize_markdown

    text = normalize_markdown("- Lodging\n| A | B |\n| --- | --- |\n")

    assert text == "- Lodging\n\n| A | B |\n| --- | --- |"


def test_existing_blank_line_before_a_table_is_not_doubled():
    from pipeline.converters import normalize_markdown

    assert normalize_markdown("Text\n\n| A |\n") == "Text\n\n| A |"


def test_consecutive_table_rows_are_left_alone():
    from pipeline.converters import normalize_markdown

    table = "| A | B |\n| --- | --- |\n| 1 | 2 |"
    assert normalize_markdown(table) == table


def test_blank_line_runs_are_collapsed():
    from pipeline.converters import normalize_markdown

    assert normalize_markdown("A\n\n\n\n\nB") == "A\n\nB"
