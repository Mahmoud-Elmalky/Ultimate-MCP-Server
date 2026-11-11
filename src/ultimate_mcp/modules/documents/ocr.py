"""Optional image OCR service gated on Pillow and pytesseract."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ultimate_mcp.core.models import ErrorCode, ResultStatus, ToolResult, UltimateMCPError
from ultimate_mcp.modules.documents.models import MAX_RETURNED_CHARS, UNTRUSTED_DOC_WARNING
from ultimate_mcp.modules.documents.security import (
    build_doc_provenance,
    compute_document_id,
    validate_document_file,
)

OCR_IMAGE_EXTENSIONS: frozenset[str] = frozenset(
    {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff", ".bmp"}
)
MAX_OCR_IMAGE_BYTES = 10 * 1024 * 1024


def image_ocr_service(
    allowed_roots: Sequence[Path],
    *,
    path: str,
    lang: str = "eng",
) -> ToolResult:
    """Extract text from an image inside workspace roots via optional ``pytesseract`` + ``PIL``."""
    resolved, size_bytes = validate_document_file(
        path, allowed_roots, max_bytes=MAX_OCR_IMAGE_BYTES
    )
    if resolved.suffix.lower() not in OCR_IMAGE_EXTENSIONS:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Unsupported image extension '{resolved.suffix}' for OCR.",
        )

    try:
        from PIL import Image
    except ImportError as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Optional dependency 'Pillow' is required for image_ocr (install with --extra media).",
        ) from exc

    try:
        import pytesseract  # type: ignore[import-not-found]
    except ImportError as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE,
            "Optional dependency 'pytesseract' and Tesseract binary are required for image_ocr.",
        ) from exc

    try:
        Image.MAX_IMAGE_PIXELS = 40_000_000
        with Image.open(resolved) as img:
            raw_text = str(pytesseract.image_to_string(img, lang=lang) or "").strip()
    except Exception as exc:
        raise UltimateMCPError(
            ErrorCode.PROVIDER_UNAVAILABLE, f"OCR execution failed: {exc}"
        ) from exc

    truncated = len(raw_text) > MAX_RETURNED_CHARS
    return ToolResult.ok(
        "image_ocr",
        f"Extracted {len(raw_text)} chars via OCR from '{resolved.name}'.",
        {
            "document_id": compute_document_id(resolved),
            "path": str(resolved),
            "size_bytes": size_bytes,
            "lang": lang,
            "text": raw_text[:MAX_RETURNED_CHARS],
            "truncated": truncated,
            "content_trust": "untrusted_document_data",
        },
        warnings=[UNTRUSTED_DOC_WARNING],
        provenance=[build_doc_provenance(resolved, f"Image OCR ({lang})")],
        status=ResultStatus.PARTIAL if truncated else ResultStatus.SUCCESS,
    )
