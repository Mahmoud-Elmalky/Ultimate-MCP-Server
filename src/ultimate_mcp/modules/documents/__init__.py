"""Document Intelligence module package (Phase 03)."""

from ultimate_mcp.modules.documents.reader import read_workspace_text_file_service
from ultimate_mcp.modules.documents.tools import register_document_tools

__all__ = [
    "read_workspace_text_file_service",
    "register_document_tools",
]
