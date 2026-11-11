"""Approval-gated, pinned-IP browser downloads and workspace-scoped uploads (Phase 03)."""

from __future__ import annotations

import asyncio
import hashlib
import os
import tempfile
import urllib.parse
from pathlib import Path
from typing import Any

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.config import Settings
from ultimate_mcp.core.filesystem import DEFAULT_MAX_FILE_SIZE_BYTES, resolve_scoped_path
from ultimate_mcp.core.http import fetch_safe_url, validate_and_resolve_url
from ultimate_mcp.core.models import ErrorCode, ToolResult, UltimateMCPError
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.modules.browser.interactions import _resolve_unique_locator
from ultimate_mcp.modules.browser.manager import BrowserSessionManager

ACTION_BROWSER_DOWNLOAD = "browser_download"
ACTION_BROWSER_UPLOAD = "browser_upload"
DEFAULT_MAX_DOWNLOAD_BYTES = 2 * 1024 * 1024

FORBIDDEN_DOWNLOAD_SUFFIXES: frozenset[str] = frozenset(
    {
        ".exe", ".bat", ".cmd", ".ps1", ".vbs", ".vbe",
        ".js", ".jse", ".msi", ".dll", ".scr", ".com", ".pif", ".sh",
    }
)

ALLOWED_DOWNLOAD_CONTENT_TYPES: tuple[str, ...] = (
    "text/plain",
    "text/csv",
    "text/html",
    "text/markdown",
    "text/xml",
    "application/json",
    "application/pdf",
    "application/zip",
    "application/octet-stream",
    "application/xml",
    "image/png",
    "image/jpeg",
    "image/webp",
)


def _validate_download_filename(raw_name: str | None, url_path: str) -> str:
    candidate = (
        (raw_name or "").strip() or Path(urllib.parse.unquote(url_path)).name or "download.bin"
    )
    if "/" in candidate or "\\" in candidate or ".." in candidate or candidate in (".", ".."):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Download filename '{candidate}' contains illegal path components.",
        )
    suffix = Path(candidate).suffix.lower()
    if suffix in FORBIDDEN_DOWNLOAD_SUFFIXES:
        raise UltimateMCPError(
            ErrorCode.PERMISSION_DENIED,
            f"Downloading executable file type '{suffix}' is blocked by security policy.",
        )
    return candidate


def _atomic_write_bytes(target: Path, data: bytes) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent)
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def _sha256_file(path: Path) -> tuple[str, int]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(65536)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


async def browser_download_service(
    manager: BrowserSessionManager,
    broker: ApprovalBroker,
    settings: Settings,
    *,
    url: str,
    filename: str | None = None,
    approval_id: str | None = None,
    max_bytes: int = DEFAULT_MAX_DOWNLOAD_BYTES,
) -> ToolResult:
    """Prepare or execute an L3-approved, pinned-IP download stored inside ``output_dir``."""
    normalized, _ = validate_and_resolve_url(url, resolver=manager.resolver)
    normalized_url = normalized.geturl()
    safe_name = _validate_download_filename(filename, normalized.path)
    max_allowed = min(settings.browser_max_download_bytes, DEFAULT_MAX_FILE_SIZE_BYTES)
    bounded_bytes = max(1, min(int(max_bytes), max_allowed))
    dest = resolve_scoped_path(
        Path("downloads") / safe_name,
        [settings.output_dir],
        must_exist=False,
    )
    payload: dict[str, Any] = {
        "url": normalized_url,
        "target_path": str(dest),
        "max_bytes": bounded_bytes,
    }

    if not approval_id:
        summary = f"Download '{normalized_url}' to '{dest}' (max {bounded_bytes} bytes)."
        record = broker.prepare_action(
            ACTION_BROWSER_DOWNLOAD,
            PermissionLevel.L3_SENSITIVE,
            payload,
            summary,
            target_path=dest,
        )
        return ToolResult.pending_approval(
            tool="browser_download",
            approval_id=record.approval_id,
            summary=summary,
            digest=record.payload_digest,
            expires_at=record.expires_at_iso,
            target=str(dest),
        )

    record = broker.consume_approved(
        approval_id.strip(),
        expected_action_type=ACTION_BROWSER_DOWNLOAD,
        expected_payload=payload,
    )
    resp = await asyncio.to_thread(
        fetch_safe_url,
        normalized_url,
        method="GET",
        allowed_content_types=ALLOWED_DOWNLOAD_CONTENT_TYPES,
        max_response_bytes=bounded_bytes,
        max_redirects=4,
        raise_for_status=True,
        resolver=manager.resolver,
        socket_factory=manager.socket_factory,
        ssl_wrapper=manager.ssl_wrapper,
    )
    final_suffix = Path(urllib.parse.urlsplit(resp.final_url).path).suffix.lower()
    if final_suffix in FORBIDDEN_DOWNLOAD_SUFFIXES:
        raise UltimateMCPError(
            ErrorCode.PERMISSION_DENIED,
            f"Redirect target suffix '{final_suffix}' is blocked.",
        )

    _atomic_write_bytes(dest, resp.body)
    body_sha = hashlib.sha256(resp.body).hexdigest()
    return ToolResult.ok(
        tool="browser_download",
        summary=f"Downloaded {len(resp.body)} bytes from '{resp.final_url}' to '{dest}'.",
        data={
            "approval_id": record.approval_id,
            "path": str(dest),
            "bytes_written": len(resp.body),
            "sha256": body_sha,
            "content_type": resp.content_type,
            "final_url": resp.final_url,
            "resolved_ips": list(resp.resolved_ips),
        },
    )


async def browser_upload_service(
    manager: BrowserSessionManager,
    broker: ApprovalBroker,
    settings: Settings,
    *,
    selector: str,
    file_path: str,
    session_id: str | None = None,
    approval_id: str | None = None,
) -> ToolResult:
    """Prepare or execute an L3-approved file attachment into ``<input type='file'>``."""
    session = manager.get_session(session_id)
    page = session.current_page
    resolved_file = resolve_scoped_path(
        file_path,
        settings.effective_read_roots,
        must_exist=True,
    )
    file_sha256, size_bytes = _sha256_file(resolved_file)
    if size_bytes > DEFAULT_MAX_FILE_SIZE_BYTES:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"File size ({size_bytes} bytes) exceeds limit ({DEFAULT_MAX_FILE_SIZE_BYTES} bytes).",
        )

    locator, resolved_sel = await _resolve_unique_locator(session, page, selector)
    meta = await locator.evaluate(
        "(el) => ({ tag: el.tagName.toLowerCase(), "
        "type: (el.getAttribute('type') || '').toLowerCase() })"
    )
    if meta.get("tag") != "input" or meta.get("type") != "file":
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Selector '{selector}' must point to an <input type='file'> element.",
        )

    parsed = urllib.parse.urlsplit(page.url)
    origin = f"{parsed.scheme.lower()}://{parsed.netloc.lower()}"
    payload: dict[str, Any] = {
        "file_path": str(resolved_file),
        "file_sha256": file_sha256,
        "size_bytes": size_bytes,
        "origin": origin,
        "selector": selector.strip(),
        "session_id": session.session_id,
    }

    if not approval_id:
        summary = (
            f"Attach file '{resolved_file.name}' (sha256={file_sha256[:12]}) to '{selector}' on "
            f"origin '{origin}'."
        )
        record = broker.prepare_action(
            ACTION_BROWSER_UPLOAD,
            PermissionLevel.L3_SENSITIVE,
            payload,
            summary,
            target_path=resolved_file,
        )
        return ToolResult.pending_approval(
            tool="browser_upload",
            approval_id=record.approval_id,
            summary=summary,
            digest=record.payload_digest,
            expires_at=record.expires_at_iso,
            target=str(resolved_file),
        )

    record = broker.consume_approved(
        approval_id.strip(),
        expected_action_type=ACTION_BROWSER_UPLOAD,
        expected_payload=payload,
    )
    await locator.set_input_files(str(resolved_file))
    return ToolResult.ok(
        tool="browser_upload",
        summary=f"Attached '{resolved_file.name}' to '{selector}' on '{origin}'.",
        data={
            "approval_id": record.approval_id,
            "session_id": session.session_id,
            "selector": resolved_sel,
            "origin": origin,
            "path": str(resolved_file),
            "sha256": file_sha256,
            "size_bytes": size_bytes,
        },
    )
