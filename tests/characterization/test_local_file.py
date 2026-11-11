"""Characterization: read_local_file (TXT/PDF/CSV)."""

from __future__ import annotations

from pathlib import Path

import PyPDF2

LEGACY_OUTPUT_CAP = 4000
LEGACY_CSV_ROWS = 50
LEGACY_PDF_PAGES = 5


def test_should_report_missing_file(legacy, tmp_path: Path) -> None:
    assert legacy.read_local_file(str(tmp_path / "nope.txt")) == "File not found."


def test_should_read_txt_and_silently_truncate(legacy, tmp_path: Path) -> None:
    target = tmp_path / "big.txt"
    target.write_text("a" * 6000, encoding="utf-8")
    out = legacy.read_local_file(str(target))
    assert out == "a" * LEGACY_OUTPUT_CAP


def test_should_strip_wrapping_quotes(legacy, tmp_path: Path) -> None:
    target = tmp_path / "q.txt"
    target.write_text("hello", encoding="utf-8")
    assert legacy.read_local_file(f'"{target}"') == "hello"


def test_should_read_csv_head(legacy, tmp_path: Path) -> None:
    target = tmp_path / "rows.csv"
    lines = ["id,value"] + [f"{i},{i * 2}" for i in range(80)]
    target.write_text("\n".join(lines), encoding="utf-8")
    out = legacy.read_local_file(str(target))
    assert len(out.splitlines()) == LEGACY_CSV_ROWS + 1
    assert "79" not in out.split()


def test_should_read_only_first_pdf_pages(legacy, tmp_path: Path) -> None:
    writer = PyPDF2.PdfWriter()
    for _ in range(LEGACY_PDF_PAGES + 2):
        writer.add_blank_page(width=72, height=72)
    target = tmp_path / "doc.pdf"
    with target.open("wb") as handle:
        writer.write(handle)
    out = legacy.read_local_file(str(target))
    assert out == "\n" * (LEGACY_PDF_PAGES - 1)


def test_should_reject_unsupported_extension(legacy, tmp_path: Path) -> None:
    target = tmp_path / "data.json"
    target.write_text("{}", encoding="utf-8")
    assert legacy.read_local_file(str(target)) == "Unsupported format."


def test_should_return_error_text_on_decode_failure(legacy, tmp_path: Path) -> None:
    target = tmp_path / "latin.txt"
    target.write_bytes(b"\xff\xfe\xfa bad")
    assert legacy.read_local_file(str(target)).startswith("Read error:")


def test_should_allow_path_traversal_outside_any_root(legacy, tmp_path: Path) -> None:
    """Security risk: no workspace root; '..' segments are normalized and followed."""
    secret = tmp_path / "secret.txt"
    secret.write_text("top-secret", encoding="utf-8")
    nested = tmp_path / "a" / "b"
    nested.mkdir(parents=True)
    traversal = nested / ".." / ".." / "secret.txt"
    assert legacy.read_local_file(str(traversal)) == "top-secret"
