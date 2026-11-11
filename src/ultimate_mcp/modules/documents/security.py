"""Workspace path validation, zip/decompression bomb checks, and provenance helpers."""

from __future__ import annotations

import hashlib
import zipfile
from collections.abc import Sequence
from pathlib import Path

from ultimate_mcp.core.filesystem import resolve_scoped_path
from ultimate_mcp.core.models import ErrorCode, Provenance, UltimateMCPError
from ultimate_mcp.modules.documents.models import (
    MAX_ARCHIVE_ENTRIES,
    MAX_ARCHIVE_RATIO,
    MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    MAX_DOC_FILE_BYTES,
    MIN_RATIO_CHECK_BYTES,
)


def validate_document_file(
    file_path: str | Path,
    allowed_roots: Sequence[Path],
    *,
    max_bytes: int = MAX_DOC_FILE_BYTES,
) -> tuple[Path, int]:
    """Validate path containment under ``allowed_roots`` and enforce max file size."""
    resolved = resolve_scoped_path(file_path, allowed_roots, must_exist=True)
    try:
        stat = resolved.stat()
    except OSError as exc:
        raise UltimateMCPError(ErrorCode.NOT_FOUND, f"Cannot stat '{file_path}': {exc}") from exc

    if stat.st_size > max_bytes:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"Document size ({stat.st_size} bytes) exceeds limit ({max_bytes} bytes).",
        )
    return resolved, int(stat.st_size)


def compute_document_id(path: Path) -> str:
    """Return deterministic document identifier (``doc_<sha256[:12]>``)."""
    digest = hashlib.sha256(str(path.resolve(strict=False)).encode("utf-8")).hexdigest()
    return f"doc_{digest[:12]}"


def inspect_zip_archive_safety(
    path: Path,
    *,
    max_entries: int = MAX_ARCHIVE_ENTRIES,
    max_uncompressed: int = MAX_ARCHIVE_UNCOMPRESSED_BYTES,
    max_ratio: float = MAX_ARCHIVE_RATIO,
) -> None:
    """Inspect OOXML zip headers BEFORE invoking parsers to block zip bombs and encryption."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > max_entries:
                raise UltimateMCPError(
                    ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                    f"Archive contains {len(infos)} entries, exceeding cap ({max_entries}).",
                )
            total_uncompressed = 0
            total_compressed = 0
            for info in infos:
                norm_name = info.filename.replace("\\", "/")
                if norm_name.startswith("/") or ".." in Path(norm_name).parts:
                    raise UltimateMCPError(
                        ErrorCode.PATH_OUTSIDE_WORKSPACE,
                        f"Suspicious archive member path '{info.filename}' rejected.",
                    )
                lower_name = norm_name.lower()
                if lower_name in ("encryptioninfo", "encryptedpackage") or (info.flag_bits & 0x1):
                    raise UltimateMCPError(
                        ErrorCode.UNSUPPORTED_FORMAT,
                        "Encrypted or password-protected Office document is not supported.",
                    )
                if info.file_size < 0:
                    raise UltimateMCPError(
                        ErrorCode.UNSUPPORTED_FORMAT,
                        "Malformed archive entry size.",
                    )
                total_uncompressed += info.file_size
                total_compressed += max(1, info.compress_size)
                if total_uncompressed > max_uncompressed:
                    raise UltimateMCPError(
                        ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                        f"Decompressed archive size exceeds {max_uncompressed} byte safety limit.",
                    )
            if total_uncompressed >= MIN_RATIO_CHECK_BYTES:
                ratio = total_uncompressed / max(1, total_compressed)
                if ratio > max_ratio:
                    raise UltimateMCPError(
                        ErrorCode.RESOURCE_LIMIT_EXCEEDED,
                        f"Suspicious archive expansion ratio ({ratio:.1f}:1 > {max_ratio}:1) "
                        "rejected (potential zip bomb).",
                    )
    except UltimateMCPError:
        raise
    except zipfile.BadZipFile as exc:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT,
            f"Malformed or corrupted archive document '{path.name}': {exc}",
        ) from exc
    except OSError as exc:
        raise UltimateMCPError(
            ErrorCode.INTERNAL_ERROR,
            f"Failed to inspect archive '{path.name}': {exc}",
        ) from exc


def build_doc_provenance(path: Path, note: str) -> Provenance:
    """Build untrusted-content provenance entry referencing exact scoped source file."""
    return Provenance(
        provider="local_documents",
        source_id=str(path),
        freshness_note=note,
        untrusted_content=True,
    )
