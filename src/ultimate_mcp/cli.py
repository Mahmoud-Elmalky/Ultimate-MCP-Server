"""Command-line interface for Ultimate MCP Server v2 (`ultimate-mcp`).

Commands:
- `ultimate-mcp serve [--profile <profile>]` — start the MCP server over stdio.
- `ultimate-mcp doctor [--profile <profile>] [--json]` — print environment and tool diagnostics.
- `ultimate-mcp catalog [--profile <profile>]` — list registered tools and availability.
- `ultimate-mcp approvals list [--status <status>]` — list approval records.
- `ultimate-mcp approvals approve <approval_id> [--reason <text>]` — approve a pending request.
- `ultimate-mcp approvals reject <approval_id> [--reason <text>]` — reject a pending request.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence

from ultimate_mcp.app import build_doctor_report, create_gateway
from ultimate_mcp.core.approvals import ApprovalBroker, ApprovalStatus
from ultimate_mcp.core.config import ProfileName, Settings, TransportMode
from ultimate_mcp.core.models import UltimateMCPError


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ultimate-mcp",
        description="Ultimate MCP Server v2 — modular local AI tool gateway.",
    )
    sub = parser.add_subparsers(dest="command")

    serve_p = sub.add_parser("serve", help="Run the MCP server.")
    serve_p.add_argument(
        "--profile",
        choices=[p.value for p in ProfileName],
        default=None,
        help="Startup tool profile (default, research, coding, documents, browser, full).",
    )
    serve_p.add_argument(
        "--transport",
        choices=[t.value for t in TransportMode],
        default=None,
        help="MCP transport mode (default: stdio).",
    )

    doc_p = sub.add_parser("doctor", help="Inspect configuration, optional packages, and tools.")
    doc_p.add_argument(
        "--profile",
        choices=[p.value for p in ProfileName],
        default=None,
        help="Profile to evaluate.",
    )
    doc_p.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON.",
    )

    cat_p = sub.add_parser("catalog", help="List tool registry and availability.")
    cat_p.add_argument(
        "--profile",
        choices=[p.value for p in ProfileName],
        default=None,
        help="Profile to evaluate.",
    )

    app_p = sub.add_parser(
        "approvals",
        help="Out-of-band human approval management for side-effecting tools.",
    )
    app_sub = app_p.add_subparsers(dest="approvals_cmd", required=True)

    list_p = app_sub.add_parser("list", help="List approval requests.")
    list_p.add_argument(
        "--status",
        choices=[s.value for s in ApprovalStatus],
        default=None,
        help="Filter by approval status.",
    )

    approve_p = app_sub.add_parser("approve", help="Approve a pending request by ID.")
    approve_p.add_argument("approval_id", help="The approval_id to authorize.")
    approve_p.add_argument("--reason", default=None, help="Optional approval note.")

    reject_p = app_sub.add_parser("reject", help="Reject a pending request by ID.")
    reject_p.add_argument("approval_id", help="The approval_id to reject.")
    reject_p.add_argument("--reason", default=None, help="Optional rejection note.")

    return parser


def _cmd_serve(args: argparse.Namespace) -> int:
    profile = ProfileName(args.profile) if args.profile else None
    settings = Settings()
    if args.transport:
        settings.transport = TransportMode(args.transport)
    runtime = create_gateway(settings=settings, profile_override=profile)
    runtime.mcp.run(transport=runtime.settings.transport.value)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    profile = ProfileName(args.profile) if args.profile else None
    report = build_doctor_report(profile_override=profile)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"Ultimate MCP Server v{report['version']} (Python {report['python']})")
    print(f"Profile: {report['profile']} | SQLite FTS5: {report['sqlite_fts5']}")
    print(f"State DB: {report['settings']['db_path']}")
    print(f"Enabled tools ({report['enabled_tool_count']}): {', '.join(report['enabled_tools'])}")
    if report["warnings"]:
        print("Warnings:")
        for w in report["warnings"]:
            print(f"  - {w}")
    return 0


def _cmd_catalog(args: argparse.Namespace) -> int:
    profile = ProfileName(args.profile) if args.profile else None
    report = build_doctor_report(profile_override=profile)
    print(json.dumps(report["tools"], indent=2, ensure_ascii=False))
    return 0


def _cmd_approvals(args: argparse.Namespace) -> int:
    settings = Settings()
    settings.ensure_directories()
    broker = ApprovalBroker(
        settings.db_path,
        default_ttl_seconds=settings.approval_ttl_seconds,
        known_secrets=settings.known_secret_values(),
    )

    if args.approvals_cmd == "list":
        status_filter = ApprovalStatus(args.status) if args.status else None
        records = broker.list_records(status=status_filter)
        payload = [
            {
                "approval_id": r.approval_id,
                "action_type": r.action_type,
                "permission": r.permission_level.value,
                "status": r.status.value,
                "summary": r.summary,
                "target_path": r.target_path,
                "digest": r.payload_digest,
                "expires_at": r.expires_at_iso,
            }
            for r in records
        ]
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        return 0

    approve_flag = args.approvals_cmd == "approve"
    try:
        decided = broker.decide_out_of_band(
            args.approval_id,
            approve=approve_flag,
            decided_by="local-cli-user",
            reason=args.reason,
        )
    except UltimateMCPError as exc:
        print(
            json.dumps({"error": exc.code.value, "message": exc.message}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 1

    print(
        json.dumps(
            {
                "approval_id": decided.approval_id,
                "status": decided.status.value,
                "action_type": decided.action_type,
                "digest": decided.payload_digest,
                "expires_at": decided.expires_at_iso,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the `ultimate-mcp` command."""
    parser = _build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    cmd = args.command or "serve"
    if cmd == "serve":
        if not hasattr(args, "profile"):
            args.profile = None
            args.transport = None
        return _cmd_serve(args)
    if cmd == "doctor":
        return _cmd_doctor(args)
    if cmd == "catalog":
        return _cmd_catalog(args)
    if cmd == "approvals":
        return _cmd_approvals(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
