"""Workspace-scoped bounded text file reader service."""

from __future__ import annotations

from ultimate_mcp.core.config import Settings
from ultimate_mcp.core.filesystem import DEFAULT_READ_CHAR_LIMIT, read_text_bounded
from ultimate_mcp.core.models import Provenance, ResultStatus, ToolResult


def read_workspace_text_file_service(
    settings: Settings,
    *,
    file_path: str,
    offset: int = 0,
    max_chars: int = DEFAULT_READ_CHAR_LIMIT,
) -> ToolResult:
    """Read UTF-8 text from an allowed workspace root with explicit pagination metadata."""
    bounded = read_text_bounded(
        file_path,
        settings.effective_read_roots,
        offset=offset,
        max_chars=max_chars,
    )
    warnings: list[str] = []
    status = ResultStatus.SUCCESS
    if bounded.truncated:
        status = ResultStatus.PARTIAL
        warnings.append(
            f"File truncated at {bounded.end_offset}/{bounded.total_chars} chars. "
            f"Pass offset={bounded.next_offset} to read the next segment."
        )

    res = ToolResult.ok(
        tool="read_text_file",
        summary=(
            f"Read {bounded.end_offset - bounded.start_offset} chars from '{bounded.path.name}'."
        ),
        data={
            "path": str(bounded.path),
            "content": bounded.content,
            "start_offset": bounded.start_offset,
            "end_offset": bounded.end_offset,
            "total_chars": bounded.total_chars,
            "size_bytes": bounded.size_bytes,
            "truncated": bounded.truncated,
            "next_offset": bounded.next_offset,
        },
        warnings=warnings,
        provenance=[
            Provenance(
                provider="local_filesystem",
                source_id=str(bounded.path),
                freshness_note=f"Read range [{bounded.start_offset}:{bounded.end_offset}] chars.",
                untrusted_content=True,
            )
        ],
        status=status,
    )
    res.metadata.truncated = bounded.truncated
    res.metadata.next_offset = bounded.next_offset
    return res
