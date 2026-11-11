"""MCP tool registration for approval-gated document conversion, creation, and editing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated, Any

from mcp.types import CallToolResult
from pydantic import Field

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.config import ProfileName, Settings
from ultimate_mcp.core.execution import ExecutionManager, TimeoutCategory
from ultimate_mcp.core.models import ToolResult
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.core.registry import ToolRegistry, ToolSpec
from ultimate_mcp.modules.documents.convert import document_convert_service
from ultimate_mcp.modules.documents.mutations import (
    document_create_service,
    document_edit_service,
)

DOC_PROFILES: frozenset[ProfileName] = frozenset({ProfileName.DOCUMENTS, ProfileName.FULL})
ResultAdapter = Callable[[ToolResult, Settings], CallToolResult]
PathArg = Annotated[str, Field(min_length=1, max_length=2000, description="Workspace file path.")]
ApprovalArg = Annotated[str | None, Field(max_length=64, description="Out-of-band L2 approval ID.")]


def register_document_action_tools(
    *,
    registry: ToolRegistry,
    settings: Settings,
    executor: ExecutionManager,
    broker: ApprovalBroker,
    adapt_result: ResultAdapter,
) -> None:
    """Register ``document_convert``, ``document_create``, and ``document_edit``."""
    read_roots = settings.effective_read_roots
    write_roots = settings.effective_write_roots

    def _register_action(
        name: str,
        description: str,
        handler: Any,
        wrapper: Any,
        *,
        produces_provenance: bool = False,
    ) -> None:
        registry.register(
            ToolSpec(
                name=name,
                module="documents",
                description=description,
                handler=handler,
                mcp_wrapper=wrapper,
                permission=PermissionLevel.L2_WRITE,
                profiles=DOC_PROFILES,
                optional_packages=(),
                idempotent=False,
                timeout_category=TimeoutCategory.LOCAL,
                produces_provenance=produces_provenance,
            )
        )

    async def document_convert(
        path: PathArg,
        target_format: Annotated[
            str,
            Field(min_length=2, max_length=16, description="txt, md, json, csv, docx, or xlsx."),
        ],
        output_path: Annotated[
            str | None,
            Field(max_length=2000, description="Optional write destination (L2 approved)."),
        ] = None,
        overwrite: Annotated[
            bool, Field(description="Allow overwriting target file with backup.")
        ] = False,
        approval_id: ApprovalArg = None,
    ) -> CallToolResult:
        """Convert a document to preview text (L0) or write converted file (L2 approved)."""
        writing = output_path is not None
        has_approval = bool(approval_id and approval_id.strip())
        perm = (
            PermissionLevel.L0_READ
            if not writing
            else (PermissionLevel.L2_WRITE if has_approval else PermissionLevel.L1_PREPARE)
        )
        res = await executor.execute(
            "document_convert",
            document_convert_service,
            {
                "allowed_read_roots": read_roots,
                "allowed_write_roots": write_roots,
                "broker": broker,
                "path": path,
                "target_format": target_format,
                "output_path": output_path,
                "overwrite": overwrite,
                "approval_id": approval_id,
            },
            permission=perm,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=not writing,
            has_valid_approval=has_approval,
        )
        return adapt_result(res, settings)

    async def document_create(
        output_path: Annotated[
            str, Field(min_length=1, max_length=2000, description="New file path.")
        ],
        doc_format: Annotated[
            str | None, Field(max_length=16, description="txt, md, json, csv, docx, xlsx.")
        ] = None,
        content: Annotated[
            str, Field(max_length=100_000, description="Document body text or JSON.")
        ] = "",
        title: Annotated[
            str | None, Field(max_length=200, description="Optional heading/sheet title.")
        ] = None,
        headers: Annotated[
            list[str] | None, Field(description="Optional table headers (csv/xlsx/docx).")
        ] = None,
        rows: Annotated[list[list[Any]] | None, Field(description="Optional table rows.")] = None,
        overwrite: Annotated[
            bool, Field(description="Overwrite existing file with backup.")
        ] = False,
        approval_id: ApprovalArg = None,
    ) -> CallToolResult:
        """Create a new workspace document behind two-step L2 approval."""
        has_approval = bool(approval_id and approval_id.strip())
        res = await executor.execute(
            "document_create",
            document_create_service,
            {
                "allowed_write_roots": write_roots,
                "broker": broker,
                "output_path": output_path,
                "doc_format": doc_format,
                "content": content,
                "title": title,
                "headers": headers,
                "rows": rows,
                "overwrite": overwrite,
                "approval_id": approval_id,
            },
            permission=PermissionLevel.L2_WRITE if has_approval else PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=False,
            has_valid_approval=has_approval,
        )
        return adapt_result(res, settings)

    async def document_edit(
        path: PathArg,
        mode: Annotated[str, Field(description="replace_text, append, or append_row.")],
        target_text: Annotated[
            str | None, Field(max_length=10_000, description="Text to replace.")
        ] = None,
        replacement_text: Annotated[
            str | None, Field(max_length=50_000, description="Replacement/appended text.")
        ] = None,
        row_values: Annotated[
            list[str] | None, Field(description="CSV row values for append_row.")
        ] = None,
        output_path: Annotated[
            str | None, Field(max_length=2000, description="Optional new copy path.")
        ] = None,
        approval_id: ApprovalArg = None,
    ) -> CallToolResult:
        """Safely edit a workspace document behind L2 approval with `.bak` backup."""
        has_approval = bool(approval_id and approval_id.strip())
        res = await executor.execute(
            "document_edit",
            document_edit_service,
            {
                "allowed_read_roots": read_roots,
                "allowed_write_roots": write_roots,
                "broker": broker,
                "path": path,
                "mode": mode,
                "target_text": target_text,
                "replacement_text": replacement_text,
                "row_values": row_values,
                "output_path": output_path,
                "approval_id": approval_id,
            },
            permission=PermissionLevel.L2_WRITE if has_approval else PermissionLevel.L1_PREPARE,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=False,
            has_valid_approval=has_approval,
        )
        return adapt_result(res, settings)

    _register_action(
        "document_convert",
        (
            "Convert a workspace document to bounded txt/md/json/csv preview, "
            "or L2-approved file output."
        ),
        document_convert_service,
        document_convert,
        produces_provenance=True,
    )
    _register_action(
        "document_create",
        (
            "Create a new txt/md/json/csv/docx/xlsx document inside workspace roots "
            "(requires L2 approval)."
        ),
        document_create_service,
        document_create,
    )
    _register_action(
        "document_edit",
        (
            "Edit a txt/md/json/csv/docx document with L2 approval and automatic "
            "timestamped .bak backup."
        ),
        document_edit_service,
        document_edit,
    )
