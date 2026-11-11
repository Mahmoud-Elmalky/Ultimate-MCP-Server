"""Workspace-scoped filesystem path resolution, bounded reads, and atomic writes.

Enforces ``docs/SECURITY_MODEL.md`` §4:
- Rejects paths when no allowed roots are configured.
- Rejects device paths, UNC paths, NUL bytes, traversal escapes, and symlink escapes.
- Enforces file size and character/byte read budgets with explicit continuation offsets.
- Performs atomic temp-file + replace writes inside approved write roots.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from ultimate_mcp.core.models import ErrorCode, UltimateMCPError

DEFAULT_MAX_FILE_SIZE_BYTES = 10 * 1024 * 1024  # 10 MiB hard ceiling for plain reads
DEFAULT_READ_CHAR_LIMIT = 8_000

WINDOWS_RESERVED_NAMES: frozenset[str] = frozenset(
    {
        "CON",
        "PRN",
        "AUX",
        "NUL",
        *(f"COM{i}" for i in range(1, 10)),
        *(f"LPT{i}" for i in range(1, 10)),
    }
)


@dataclass(frozen=True)
class BoundedTextRead:
    """Result of reading text from a workspace-validated file."""

    path: Path
    content: str
    total_chars: int
    start_offset: int
    end_offset: int
    truncated: bool
    next_offset: int | None
    size_bytes: int


def _reject_unsafe_raw_path(raw: str) -> str:
    cleaned = raw.strip().strip("'\"")
    if not cleaned:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "File path must not be empty.")
    if "\x00" in cleaned:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "File path contains illegal NUL byte.")
    if cleaned.startswith(("\\\\", "//")):
        raise UltimateMCPError(
            ErrorCode.PATH_OUTSIDE_WORKSPACE,
            "UNC and network share paths are not permitted by security policy.",
        )
    parts = Path(cleaned).parts
    for part in parts:
        stem = part.split(".")[0].upper()
        if stem in WINDOWS_RESERVED_NAMES:
            raise UltimateMCPError(
                ErrorCode.INVALID_INPUT,
                f"Reserved Windows device name '{part}' is not allowed.",
            )
    return cleaned


def _is_within_root(candidate: Path, root: Path) -> bool:
    """Return True if resolved ``candidate`` is equal to or inside resolved ``root``."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        # On Windows, case differences in drive letters or directory names can affect
        # Path.relative_to when paths originate from different casings.
        if os.name == "nt":
            cand_norm = os.path.normcase(str(candidate))
            root_norm = os.path.normcase(str(root)).rstrip("\\/")
            return cand_norm == root_norm or cand_norm.startswith(root_norm + "\\")
        return False


def resolve_scoped_path(
    file_path: str | Path,
    allowed_roots: Sequence[Path],
    *,
    must_exist: bool = False,
    allow_directory: bool = False,
) -> Path:
    """Resolve ``file_path`` and verify it stays inside ``allowed_roots`` after symlink resolution.

    Relative paths are resolved against the first configured root.
    """
    raw_str = _reject_unsafe_raw_path(str(file_path))
    if not allowed_roots:
        raise UltimateMCPError(
            ErrorCode.PATH_OUTSIDE_WORKSPACE,
            "No workspace roots are configured. Set ULTIMATE_MCP_READ_ROOTS or "
            "ULTIMATE_MCP_WRITE_ROOTS to grant scoped filesystem access.",
        )

    resolved_roots = [root.resolve(strict=False) for root in allowed_roots]
    candidate = Path(raw_str).expanduser()
    if not candidate.is_absolute():
        candidate = resolved_roots[0] / candidate

    resolved = candidate.resolve(strict=False)
    if not any(_is_within_root(resolved, root) for root in resolved_roots):
        raise UltimateMCPError(
            ErrorCode.PATH_OUTSIDE_WORKSPACE,
            f"Path '{raw_str}' resolves outside configured workspace roots.",
            details={"requested": raw_str},
        )

    if must_exist and not resolved.exists():
        raise UltimateMCPError(
            ErrorCode.NOT_FOUND,
            f"File not found: '{raw_str}'.",
        )

    if resolved.exists() and resolved.is_dir() and not allow_directory:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Expected a file path, but '{raw_str}' is a directory.",
        )

    return resolved


def read_text_bounded(
    file_path: str | Path,
    allowed_roots: Sequence[Path],
    *,
    offset: int = 0,
    max_chars: int = DEFAULT_READ_CHAR_LIMIT,
    max_file_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
) -> BoundedTextRead:
    """Read UTF-8 text from an approved path with strict byte and character limits."""
    if offset < 0:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "offset must be >= 0.")
    if max_chars <= 0:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "max_chars must be > 0.")

    resolved = resolve_scoped_path(file_path, allowed_roots, must_exist=True)
    try:
        stat = resolved.stat()
    except OSError as exc:
        raise UltimateMCPError(ErrorCode.NOT_FOUND, f"Cannot stat file: {exc}") from exc

    if stat.st_size > max_file_bytes:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"File size ({stat.st_size} bytes) exceeds maximum allowed ({max_file_bytes} bytes).",
        )

    try:
        text = resolved.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise UltimateMCPError(
            ErrorCode.UNSUPPORTED_FORMAT,
            "File is not valid UTF-8 text.",
        ) from exc
    except OSError as exc:
        raise UltimateMCPError(
            ErrorCode.INTERNAL_ERROR,
            f"Failed to read file: {exc}",
        ) from exc

    total_chars = len(text)
    sliced = text[offset : offset + max_chars]
    end_offset = offset + len(sliced)
    truncated = end_offset < total_chars
    next_offset = end_offset if truncated else None

    return BoundedTextRead(
        path=resolved,
        content=sliced,
        total_chars=total_chars,
        start_offset=offset,
        end_offset=end_offset,
        truncated=truncated,
        next_offset=next_offset,
        size_bytes=stat.st_size,
    )


def atomic_write_text(
    file_path: str | Path,
    content: str,
    allowed_write_roots: Sequence[Path],
    *,
    overwrite: bool = False,
    max_bytes: int = DEFAULT_MAX_FILE_SIZE_BYTES,
) -> Path:
    """Atomically write UTF-8 text to a path inside ``allowed_write_roots``."""
    encoded = content.encode("utf-8")
    if len(encoded) > max_bytes:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"Write payload ({len(encoded)} bytes) exceeds limit ({max_bytes} bytes).",
        )

    target = resolve_scoped_path(file_path, allowed_write_roots, must_exist=False)
    if target.exists() and not overwrite:
        raise UltimateMCPError(
            ErrorCode.PERMISSION_DENIED,
            f"Target file '{target.name}' already exists and overwrite=False.",
        )

    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{target.name}.",
        suffix=".tmp",
        dir=str(target.parent),
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, target)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return target
