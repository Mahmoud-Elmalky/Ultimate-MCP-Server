"""Unit tests for Phase 03 Document Intelligence extractors (stdlib + optional extras)."""

from __future__ import annotations

import builtins
from pathlib import Path
from typing import Any

import pytest

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.modules.documents.models import UNTRUSTED_DOC_WARNING
from ultimate_mcp.modules.documents.service import (
    document_extract_tables_service,
    document_read_service,
)


def _create_sample_pdf(path: Path, page_texts: list[str]) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font_dict = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        resources = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font_dict})}
        )
        page[NameObject("/Resources")] = resources
        safe_txt = text.replace("(", "\\(").replace(")", "\\)")
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({safe_txt}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)


def _create_sample_docx(path: Path) -> None:
    import docx

    doc = docx.Document()
    doc.add_paragraph("Executive Summary: Phase 03 targets document fidelity.")
    doc.add_paragraph("Risk Note: Always treat file contents as untrusted.")
    tbl = doc.add_table(rows=3, cols=2)
    tbl.rows[0].cells[0].text = "Metric"
    tbl.rows[0].cells[1].text = "Value"
    tbl.rows[1].cells[0].text = "Latency"
    tbl.rows[1].cells[1].text = "23ms"
    tbl.rows[2].cells[0].text = "Coverage"
    tbl.rows[2].cells[1].text = "92%"
    doc.save(str(path))


def _create_sample_xlsx(path: Path) -> None:
    import openpyxl

    wb = openpyxl.Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Budget"
    ws.append(["Department", "Q1", "Q2", "Total"])
    ws.append(["Engineering", 100, 150, "=B2+C2"])
    ws.append(["Research", 80, 120, "=B3+C3"])
    ws2 = wb.create_sheet("Summary")
    ws2.append(["Region", "Headcount"])
    ws2.append(["EMEA", 14])
    wb.save(str(path))
    wb.close()


def _create_sample_pptx(path: Path) -> None:
    import pptx
    from pptx.util import Inches

    prs = pptx.Presentation()
    s1 = prs.slides.add_slide(prs.slide_layouts[1])
    if s1.shapes.title:
        s1.shapes.title.text = "Architecture Overview"
    s1.placeholders[1].text = "Pinned IP proxy isolates Playwright egress."
    s1.notes_slide.notes_text_frame.text = "Remind audience about Windows Firewall limits."

    s2 = prs.slides.add_slide(prs.slide_layouts[5])
    if s2.shapes.title:
        s2.shapes.title.text = "Metrics Table"
    tbl = s2.shapes.add_table(2, 2, Inches(1), Inches(2), Inches(4), Inches(1.5)).table
    tbl.cell(0, 0).text = "Mode"
    tbl.cell(0, 1).text = "Tools"
    tbl.cell(1, 0).text = "default"
    tbl.cell(1, 1).text = "5"
    prs.save(str(path))


def test_pdf_multipage_extraction_and_pagination(tmp_path: Path) -> None:
    pdf_path = tmp_path / "quarterly.pdf"
    _create_sample_pdf(
        pdf_path, ["Page one intro budget", "Page two final revenue 99M", "Page three appendix"]
    )

    res1 = document_read_service([tmp_path], path=str(pdf_path), offset=0, limit=2)
    assert res1.data["format"] == "pdf"
    assert res1.data["total_units"] == 3
    assert res1.data["truncated"] is True
    assert res1.data["next_offset"] == 2
    assert len(res1.data["chunks"]) == 2
    assert res1.data["chunks"][0]["location"]["page"] == 1
    assert res1.data["chunks"][1]["location"]["page"] == 2
    assert "99M" in res1.data["chunks"][1]["text"]
    assert res1.provenance[0].untrusted_content is True

    res2 = document_read_service([tmp_path], path=str(pdf_path), offset=2, limit=2)
    assert res2.data["truncated"] is False
    assert res2.data["chunks"][0]["location"]["page"] == 3


def test_docx_xlsx_and_pptx_structured_extraction(tmp_path: Path) -> None:
    docx_path = tmp_path / "memo.docx"
    xlsx_path = tmp_path / "model.xlsx"
    pptx_path = tmp_path / "deck.pptx"
    _create_sample_docx(docx_path)
    _create_sample_xlsx(xlsx_path)
    _create_sample_pptx(pptx_path)

    docx_res = document_read_service([tmp_path], path=str(docx_path))
    assert "Executive Summary" in docx_res.data["chunks"][0]["text"]
    assert docx_res.data["tables"][0]["headers"] == ["Metric", "Value"]
    assert docx_res.data["tables"][0]["rows"][0] == ["Latency", "23ms"]

    xlsx_res = document_extract_tables_service([tmp_path], path=str(xlsx_path), sheet_name="Budget")
    tbl = xlsx_res.data["tables"][0]
    assert tbl["formulas_present"] is True
    assert tbl["headers"] == ["Department", "Q1", "Q2", "Total"]
    assert tbl["rows"][0][3]["formula"] == "=B2+C2"
    assert xlsx_res.data["metadata"]["sheets"] == ["Budget", "Summary"]

    pptx_res = document_read_service([tmp_path], path=str(pptx_path))
    assert pptx_res.data["total_units"] == 2
    assert pptx_res.data["chunks"][0]["location"]["slide"] == 1
    assert "Speaker Notes" in pptx_res.data["chunks"][0]["text"]
    assert pptx_res.data["tables"][0]["location"]["slide"] == 2
    assert pptx_res.data["tables"][0]["rows"][0] == ["default", "5"]


def test_html_csv_json_and_markdown_extraction(tmp_path: Path) -> None:
    html_file = tmp_path / "report.html"
    html_file.write_text(
        "<html><head><style>body{}</style><script>alert(1)</script></head>"
        "<body><h1>Headline</h1><p>Visible info.</p>"
        "<table><tr><th>K</th><th>V</th></tr><tr><td>A</td><td>1</td></tr></table></body></html>",
        encoding="utf-8",
    )
    html_res = document_read_service([tmp_path], path=str(html_file))
    joined = " ".join(c["text"] for c in html_res.data["chunks"])
    assert "Headline" in joined and "Visible info." in joined
    assert "alert(1)" not in joined
    assert html_res.data["tables"][0]["headers"] == ["K", "V"]

    csv_file = tmp_path / "data.csv"
    csv_file.write_text("id,val\n1,alpha\n2,beta\n3,gamma\n", encoding="utf-8")
    csv_res = document_read_service([tmp_path], path=str(csv_file), offset=1, limit=1)
    assert csv_res.data["tables"][0]["rows"] == [["2", "beta"]]
    assert csv_res.data["next_offset"] == 2
    assert UNTRUSTED_DOC_WARNING in csv_res.warnings

    json_file = tmp_path / "cfg.json"
    json_file.write_text('{"k": 123}', encoding="utf-8")
    json_res = document_read_service([tmp_path], path=str(json_file))
    assert '"k": 123' in json_res.data["chunks"][0]["text"]


def test_missing_optional_dependency_gives_actionable_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf_path = tmp_path / "missing_dep.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    real_import = builtins.__import__

    def _fake_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pypdf":
            raise ImportError("simulated missing pypdf")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _fake_import)
    with pytest.raises(UltimateMCPError) as exc_info:
        document_read_service([tmp_path], path=str(pdf_path))
    assert exc_info.value.code == ErrorCode.PROVIDER_UNAVAILABLE
    assert "uv sync --extra documents" in exc_info.value.message


async def test_documents_mcp_profile_wrappers_and_approval_flow(tmp_path: Path) -> None:
    import json

    from ultimate_mcp.core.approvals import ApprovalBroker
    from ultimate_mcp.core.bootstrap import build_registry
    from ultimate_mcp.core.config import ProfileName, Settings
    from ultimate_mcp.core.execution import ExecutionManager

    sample = tmp_path / "memo.md"
    sample.write_text("# Quarterly Memo\nRevenue target reached.\n", encoding="utf-8")

    cfg = Settings(
        profile=ProfileName.DOCUMENTS,
        read_roots=(tmp_path,),
        write_roots=(tmp_path,),
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
    )
    broker = ApprovalBroker(db_path=tmp_path / "state" / "approvals.sqlite")
    registry = build_registry(cfg, ExecutionManager(cfg), broker)
    enabled = {s.name: s for s in registry.enabled_specs(cfg, ProfileName.DOCUMENTS)}
    assert {"document_read", "document_search", "document_qa", "document_create"} <= set(enabled)

    read_res = await enabled["document_read"].mcp_wrapper(path=str(sample))
    assert read_res.is_error is False
    parsed_read = json.loads(read_res.content[0].text)
    assert parsed_read["data"]["format"] == "md"

    new_doc = tmp_path / "summary.txt"
    prep_res = await enabled["document_create"].mcp_wrapper(
        output_path=str(new_doc), content="Generated report"
    )
    parsed_prep = json.loads(prep_res.content[0].text)
    assert parsed_prep["status"] == "needs_approval"
    broker.decide_out_of_band(parsed_prep["data"]["approval_id"], approve=True, decided_by="user")
    exec_res = await enabled["document_create"].mcp_wrapper(
        output_path=str(new_doc),
        content="Generated report",
        approval_id=parsed_prep["data"]["approval_id"],
    )
    assert exec_res.is_error is False
    assert new_doc.read_text(encoding="utf-8") == "Generated report"
