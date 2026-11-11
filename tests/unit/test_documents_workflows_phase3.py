"""Unit tests for Document Intelligence search, QA, compare, convert, create, edit, and OCR."""

from __future__ import annotations

import builtins
import json
from pathlib import Path
from typing import Any

import pytest

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.models import ErrorCode, ResultStatus, UltimateMCPError
from ultimate_mcp.modules.documents.analysis import (
    document_qa_service,
    document_search_service,
)
from ultimate_mcp.modules.documents.compare import document_compare_service
from ultimate_mcp.modules.documents.convert import document_convert_service
from ultimate_mcp.modules.documents.mutations import (
    document_create_service,
    document_edit_service,
)
from ultimate_mcp.modules.documents.ocr import image_ocr_service


def _create_sample_pdf(path: Path, page_texts: list[str]) -> None:
    from pypdf import PdfWriter
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = PdfWriter()
    font = DictionaryObject(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    for text in page_texts:
        page = writer.add_blank_page(width=612, height=792)
        page[NameObject("/Resources")] = DictionaryObject(
            {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
        )
        stream = DecodedStreamObject()
        stream.set_data(f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode("latin-1"))
        page[NameObject("/Contents")] = writer._add_object(stream)
    with path.open("wb") as handle:
        writer.write(handle)


def test_document_search_qa_and_compare_with_provenance(tmp_path: Path) -> None:
    pdf_path = tmp_path / "brief.pdf"
    _create_sample_pdf(
        pdf_path,
        [
            "Page 1 mentions zero internal LLM loops and local SQLite cache.",
            "Page 2 states pinned IP transport blocks DNS rebinding attacks.",
        ],
    )

    search_res = document_search_service([tmp_path], path=str(pdf_path), query="DNS rebinding")
    assert search_res.data["match_count"] == 1
    assert search_res.data["matches"][0]["location"]["page"] == 2
    assert search_res.provenance[0].untrusted_content is True

    qa_res = document_qa_service(
        [tmp_path], path=str(pdf_path), question="How are DNS rebinding attacks prevented?"
    )
    assert qa_res.data["retrieval_mode"] == "deterministic_lexical_ranking"
    assert qa_res.data["evidence"][0]["location"]["page"] == 2
    assert "pinned" in qa_res.data["evidence"][0]["excerpt"].lower()

    doc_a = tmp_path / "v1.md"
    doc_b = tmp_path / "v2.md"
    doc_a.write_text("# Plan\nLine one\nLine two\n", encoding="utf-8")
    doc_b.write_text("# Plan\nLine one\nLine updated\n", encoding="utf-8")
    cmp_res = document_compare_service([tmp_path], path_a=str(doc_a), path_b=str(doc_b))
    assert cmp_res.data["identical"] is False
    assert any("-Line two" in line for line in cmp_res.data["diff"])
    assert any("+Line updated" in line for line in cmp_res.data["diff"])
    assert len(cmp_res.provenance) == 2


def test_document_convert_preview_and_approved_write(tmp_path: Path) -> None:
    broker = ApprovalBroker(db_path=tmp_path / "approvals.sqlite")
    csv_path = tmp_path / "scores.csv"
    csv_path.write_text("name,score\nAlice,95\n", encoding="utf-8")

    preview = document_convert_service(
        [tmp_path], [tmp_path], broker, path=str(csv_path), target_format="md"
    )
    assert preview.status == ResultStatus.SUCCESS
    assert "| Alice | 95 |" in preview.data["converted_text"]

    out_json = tmp_path / "scores.json"
    prep = document_convert_service(
        [tmp_path],
        [tmp_path],
        broker,
        path=str(csv_path),
        target_format="json",
        output_path=str(out_json),
    )
    assert prep.status == ResultStatus.NEEDS_APPROVAL
    approval_id = prep.data["approval_id"]
    broker.decide_out_of_band(approval_id, approve=True, decided_by="user")

    written = document_convert_service(
        [tmp_path],
        [tmp_path],
        broker,
        path=str(csv_path),
        target_format="json",
        output_path=str(out_json),
        approval_id=approval_id,
    )
    assert written.status == ResultStatus.SUCCESS
    assert out_json.exists()
    parsed = json.loads(out_json.read_text(encoding="utf-8"))
    assert parsed["tables"][0]["rows"] == [["Alice", "95"]]


def test_document_create_and_edit_require_l2_approval_and_preserve_backup(tmp_path: Path) -> None:
    broker = ApprovalBroker(db_path=tmp_path / "approvals.sqlite")
    xlsx_out = tmp_path / "new_sheet.xlsx"

    prep_create = document_create_service(
        [tmp_path], broker, output_path=str(xlsx_out), headers=["Item", "Qty"], rows=[["Widget", 3]]
    )
    assert prep_create.status == ResultStatus.NEEDS_APPROVAL
    assert not xlsx_out.exists()
    broker.decide_out_of_band(prep_create.data["approval_id"], approve=True, decided_by="user")
    created = document_create_service(
        [tmp_path],
        broker,
        output_path=str(xlsx_out),
        headers=["Item", "Qty"],
        rows=[["Widget", 3]],
        approval_id=prep_create.data["approval_id"],
    )
    assert created.status == ResultStatus.SUCCESS and xlsx_out.exists()

    notes = tmp_path / "notes.md"
    notes.write_text("Original line\n", encoding="utf-8")
    prep_edit = document_edit_service(
        [tmp_path],
        [tmp_path],
        broker,
        path=str(notes),
        mode="replace_text",
        target_text="Original",
        replacement_text="Verified",
    )
    assert prep_edit.status == ResultStatus.NEEDS_APPROVAL
    app_id = prep_edit.data["approval_id"]
    broker.decide_out_of_band(app_id, approve=True, decided_by="user")

    edited = document_edit_service(
        [tmp_path],
        [tmp_path],
        broker,
        path=str(notes),
        mode="replace_text",
        target_text="Original",
        replacement_text="Verified",
        approval_id=app_id,
    )
    assert edited.status == ResultStatus.SUCCESS
    bak = Path(edited.data["backup_path"])
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == "Original line\n"
    assert notes.read_text(encoding="utf-8") == "Verified line\n"


def test_document_edit_detects_toctou_modification_between_prepare_and_execute(
    tmp_path: Path,
) -> None:
    broker = ApprovalBroker(db_path=tmp_path / "approvals.sqlite")
    target = tmp_path / "doc.txt"
    target.write_text("v1 content", encoding="utf-8")
    prep = document_edit_service(
        [tmp_path], [tmp_path], broker, path=str(target), mode="append", replacement_text="extra"
    )
    app_id = prep.data["approval_id"]
    broker.decide_out_of_band(app_id, approve=True, decided_by="user")
    target.write_text("tampered externally", encoding="utf-8")

    with pytest.raises(UltimateMCPError) as exc_info:
        document_edit_service(
            [tmp_path],
            [tmp_path],
            broker,
            path=str(target),
            mode="append",
            replacement_text="extra",
            approval_id=app_id,
        )
    assert exc_info.value.code == ErrorCode.APPROVAL_INVALID


def test_image_ocr_missing_and_mocked_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from PIL import Image

    img_path = tmp_path / "scan.png"
    Image.new("RGB", (60, 30), color="white").save(img_path)

    real_import = builtins.__import__

    class _FakeTesseract:
        @staticmethod
        def image_to_string(img: Any, lang: str = "eng") -> str:
            return f"Scanned Invoice 42 ({lang})"

    def _mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pytesseract":
            return _FakeTesseract()
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _mock_import)
    ok_res = image_ocr_service([tmp_path], path=str(img_path), lang="eng")
    assert ok_res.data["text"] == "Scanned Invoice 42 (eng)"
    assert ok_res.provenance[0].untrusted_content is True

    def _missing_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "pytesseract":
            raise ImportError("no pytesseract")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _missing_import)
    with pytest.raises(UltimateMCPError) as exc_info:
        image_ocr_service([tmp_path], path=str(img_path))
    assert exc_info.value.code == ErrorCode.PROVIDER_UNAVAILABLE


def test_docx_create_and_edit_with_backup(tmp_path: Path) -> None:
    broker = ApprovalBroker(db_path=tmp_path / "state" / "approvals.sqlite")
    docx_target = tmp_path / "memo.docx"
    kwargs: dict[str, Any] = {
        "output_path": str(docx_target),
        "title": "Memo",
        "content": "Line 1",
        "headers": ["K", "V"],
        "rows": [["a", "1"]],
    }
    prep = document_create_service([tmp_path], broker, **kwargs)
    broker.decide_out_of_band(prep.data["approval_id"], approve=True, decided_by="user")
    document_create_service([tmp_path], broker, approval_id=prep.data["approval_id"], **kwargs)

    edit_kw = {
        "path": str(docx_target),
        "mode": "replace_text",
        "target_text": "Line 1",
        "replacement_text": "Line Updated",
    }
    prep_e = document_edit_service([tmp_path], [tmp_path], broker, **edit_kw)
    broker.decide_out_of_band(prep_e.data["approval_id"], approve=True, decided_by="user")
    res_e = document_edit_service(
        [tmp_path], [tmp_path], broker, approval_id=prep_e.data["approval_id"], **edit_kw
    )
    assert Path(res_e.data["backup_path"]).exists()
