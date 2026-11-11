"""MCP tool registration for read-only Document Intelligence and optional OCR tools."""

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
from ultimate_mcp.modules.documents.action_tools import register_document_action_tools
from ultimate_mcp.modules.documents.analysis import (
    document_qa_service,
    document_search_service,
)
from ultimate_mcp.modules.documents.compare import document_compare_service
from ultimate_mcp.modules.documents.models import DEFAULT_CHUNK_CHARS, DEFAULT_ROW_LIMIT
from ultimate_mcp.modules.documents.ocr import image_ocr_service
from ultimate_mcp.modules.documents.service import (
    document_extract_tables_service,
    document_read_service,
)

DOC_PROFILES: frozenset[ProfileName] = frozenset({ProfileName.DOCUMENTS, ProfileName.FULL})
ResultAdapter = Callable[[ToolResult, Settings], CallToolResult]
PathArg = Annotated[
    str, Field(min_length=1, max_length=2000, description="Scoped workspace document path.")
]


def register_document_tools(
    *,
    registry: ToolRegistry,
    settings: Settings,
    executor: ExecutionManager,
    broker: ApprovalBroker,
    adapt_result: ResultAdapter,
) -> None:
    """Register Document Intelligence read, table, search, QA, compare, OCR, and mutation tools."""
    read_roots = settings.effective_read_roots

    def _register_read(
        name: str,
        description: str,
        handler: Any,
        wrapper: Any,
        *,
        optional_packages: tuple[str, ...] = (),
    ) -> None:
        registry.register(
            ToolSpec(
                name=name,
                module="documents",
                description=description,
                handler=handler,
                mcp_wrapper=wrapper,
                permission=PermissionLevel.L0_READ,
                profiles=DOC_PROFILES,
                optional_packages=optional_packages,
                idempotent=True,
                timeout_category=TimeoutCategory.LOCAL,
                produces_provenance=True,
            )
        )

    async def document_read(
        path: PathArg,
        offset: Annotated[
            int, Field(ge=0, description="Zero-based page/slide/row/segment offset.")
        ] = 0,
        limit: Annotated[
            int | None, Field(ge=1, le=2000, description="Optional unit batch limit.")
        ] = None,
        max_chars: Annotated[
            int, Field(ge=500, le=20_000, description="Maximum characters to return per batch.")
        ] = DEFAULT_CHUNK_CHARS,
        sheet_name: Annotated[
            str | None, Field(max_length=64, description="Target XLSX sheet name.")
        ] = None,
    ) -> CallToolResult:
        """Read a PDF, DOCX, XLSX, PPTX, HTML, CSV, JSON, Markdown, or TXT file with pagination."""
        res = await executor.execute(
            "document_read",
            document_read_service,
            {
                "allowed_roots": read_roots,
                "path": path,
                "offset": offset,
                "limit": limit,
                "max_chars": max_chars,
                "sheet_name": sheet_name,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return adapt_result(res, settings)

    async def document_search(
        path: PathArg,
        query: Annotated[
            str, Field(min_length=1, max_length=500, description="Substring to search for.")
        ],
        case_sensitive: Annotated[bool, Field(description="Match exact character casing.")] = False,
        max_matches: Annotated[
            int, Field(ge=1, le=25, description="Maximum matches to return.")
        ] = 10,
        sheet_name: Annotated[
            str | None, Field(max_length=64, description="Optional XLSX sheet filter.")
        ] = None,
    ) -> CallToolResult:
        """Search document text and tables for matches with exact provenance coordinates."""
        res = await executor.execute(
            "document_search",
            document_search_service,
            {
                "allowed_roots": read_roots,
                "path": path,
                "query": query,
                "case_sensitive": case_sensitive,
                "max_matches": max_matches,
                "sheet_name": sheet_name,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return adapt_result(res, settings)

    async def document_extract_tables(
        path: PathArg,
        sheet_name: Annotated[
            str | None, Field(max_length=64, description="Target XLSX sheet name.")
        ] = None,
        table_index: Annotated[
            int | None, Field(ge=0, description="Optional zero-based table index.")
        ] = None,
        offset: Annotated[int, Field(ge=0, description="Zero-based row offset.")] = 0,
        limit: Annotated[
            int, Field(ge=1, le=2000, description="Maximum rows to return.")
        ] = DEFAULT_ROW_LIMIT,
    ) -> CallToolResult:
        """Extract preserved structured tables from XLSX, CSV, TSV, DOCX, PPTX, or HTML files."""
        res = await executor.execute(
            "document_extract_tables",
            document_extract_tables_service,
            {
                "allowed_roots": read_roots,
                "path": path,
                "sheet_name": sheet_name,
                "table_index": table_index,
                "offset": offset,
                "limit": limit,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return adapt_result(res, settings)

    async def document_qa(
        path: PathArg,
        question: Annotated[
            str, Field(min_length=2, max_length=1000, description="Question to answer.")
        ],
        top_k: Annotated[
            int, Field(ge=1, le=10, description="Maximum cited passages to return.")
        ] = 5,
        sheet_name: Annotated[
            str | None, Field(max_length=64, description="Optional XLSX sheet filter.")
        ] = None,
    ) -> CallToolResult:
        """Retrieve ranked document evidence with page/slide/sheet/row provenance (zero LLM)."""
        res = await executor.execute(
            "document_qa",
            document_qa_service,
            {
                "allowed_roots": read_roots,
                "path": path,
                "question": question,
                "top_k": top_k,
                "sheet_name": sheet_name,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return adapt_result(res, settings)

    async def document_compare(
        path_a: PathArg,
        path_b: PathArg,
        max_diff_lines: Annotated[
            int, Field(ge=10, le=200, description="Maximum unified diff lines.")
        ] = 80,
    ) -> CallToolResult:
        """Compare two workspace documents and return structured delta + bounded unified diff."""
        res = await executor.execute(
            "document_compare",
            document_compare_service,
            {
                "allowed_roots": read_roots,
                "path_a": path_a,
                "path_b": path_b,
                "max_diff_lines": max_diff_lines,
            },
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return adapt_result(res, settings)

    async def image_ocr(
        path: PathArg,
        lang: Annotated[
            str, Field(min_length=2, max_length=24, description="Tesseract language code.")
        ] = "eng",
    ) -> CallToolResult:
        """Run optional OCR on a workspace image when pytesseract + Pillow are installed."""
        res = await executor.execute(
            "image_ocr",
            image_ocr_service,
            {"allowed_roots": read_roots, "path": path, "lang": lang},
            permission=PermissionLevel.L0_READ,
            timeout_category=TimeoutCategory.LOCAL,
            idempotent=True,
        )
        return adapt_result(res, settings)

    _register_read(
        "document_read",
        (
            "Read and paginate PDF, DOCX, XLSX, PPTX, HTML, CSV, JSON, Markdown, "
            "or TXT with provenance."
        ),
        document_read_service,
        document_read,
    )
    _register_read(
        "document_search",
        "Search document chunks and tables for matching snippets with page/slide/row provenance.",
        document_search_service,
        document_search,
    )
    _register_read(
        "document_extract_tables",
        "Extract preserved tabular structures (headers, rows, formulas) from documents.",
        document_extract_tables_service,
        document_extract_tables,
    )
    _register_read(
        "document_qa",
        (
            "Retrieve cited passages and table rows relevant to a question "
            "(deterministic, zero internal LLM)."
        ),
        document_qa_service,
        document_qa,
    )
    _register_read(
        "document_compare",
        "Compare two workspace documents returning structural deltas and a bounded unified diff.",
        document_compare_service,
        document_compare,
    )
    _register_read(
        "image_ocr",
        "Extract text from a workspace image via optional pytesseract and Pillow.",
        image_ocr_service,
        image_ocr,
        optional_packages=("pytesseract", "PIL"),
    )
    register_document_action_tools(
        registry=registry,
        settings=settings,
        executor=executor,
        broker=broker,
        adapt_result=adapt_result,
    )
