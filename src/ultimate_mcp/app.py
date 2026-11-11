"""Application factory entry module for Ultimate MCP Server v2."""

from __future__ import annotations

from ultimate_mcp.core.bootstrap import GatewayRuntime, build_doctor_report, create_gateway

__all__ = ["GatewayRuntime", "build_doctor_report", "create_gateway"]
