"""Out-of-band canonical request approval binding for state-changing browser requests."""

from __future__ import annotations

import hashlib
import json
import urllib.parse
from typing import Any

from ultimate_mcp.core.approvals import ApprovalBroker, ApprovalRecord
from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.core.permissions import PermissionLevel

BROWSER_MUTATION_ACTION_TYPE = "browser_http_mutation"
DEFAULT_BROWSER_SESSION_ID = "default-ephemeral-session"
STATE_CHANGING_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})

SECURITY_HEADER_NAMES: tuple[str, ...] = (
    "content-type",
    "origin",
    "referer",
    "authorization",
    "cookie",
    "x-csrf-token",
    "x-xsrf-token",
    "x-requested-with",
    "x-http-method-override",
    "x-method-override",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonicalize_mutation_url(url: str) -> tuple[str, str]:
    """Return ``(canonical_origin, canonical_url)`` without fragments or trailing host dots."""
    parsed = urllib.parse.urlsplit(url.strip())
    scheme = parsed.scheme.lower()
    host = (parsed.hostname or "").strip().rstrip(".").lower()
    if not scheme or not host:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, f"Invalid mutation URL '{url}'.")
    formatted_host = f"[{host}]" if ":" in host else host
    default_port = 443 if scheme == "https" else 80 if scheme == "http" else None
    try:
        port = parsed.port
    except ValueError as exc:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, f"Invalid URL port: {exc}") from exc
    netloc = (
        formatted_host if port is None or port == default_port else f"{formatted_host}:{port}"
    )
    origin = f"{scheme}://{netloc}"
    canon_url = urllib.parse.urlunsplit((scheme, netloc, parsed.path or "/", parsed.query, ""))
    return origin, canon_url


def _normalize_security_headers(headers: dict[str, str] | None) -> list[tuple[str, str]]:
    """Extract sorted security-relevant headers for canonical hashing (no raw secret logging)."""
    normalized: dict[str, str] = {}
    for key, val in (headers or {}).items():
        low = key.strip().lower()
        if low in SECURITY_HEADER_NAMES or low.startswith("x-"):
            normalized[low] = val.strip()
    return sorted(normalized.items())


def build_mutation_approval_payload(
    method: str,
    url: str,
    body: bytes | None,
    *,
    headers: dict[str, str] | None = None,
    session_id: str = DEFAULT_BROWSER_SESSION_ID,
    auth_context: str = "",
    cookie_header: str = "",
) -> dict[str, str]:
    """Build canonical, secret-free approval payload bound to all material request fields."""
    origin, canon_url = canonicalize_mutation_url(url)
    norm_headers = _normalize_security_headers(headers)
    header_map = dict(norm_headers)

    content_type = header_map.get("content-type", "").lower()
    req_origin_hdr = header_map.get("origin", "").lower()
    effective_auth = auth_context.strip() or header_map.get("authorization", "")
    effective_cookie = cookie_header.strip() or header_map.get("cookie", "")
    headers_canonical = json.dumps(norm_headers, separators=(",", ":"), ensure_ascii=True)
    return {
        "method": method.strip().upper(),
        "origin": origin,
        "url": canon_url,
        "content_type": content_type,
        "request_origin_header": req_origin_hdr,
        "auth_context_sha256": _sha256_bytes(effective_auth.encode("utf-8")),
        "session_state_sha256": _sha256_bytes(effective_cookie.encode("utf-8")),
        "headers_sha256": _sha256_bytes(headers_canonical.encode("utf-8")),
        "session_id": session_id.strip() or DEFAULT_BROWSER_SESSION_ID,
        "body_sha256": _sha256_bytes(body or b""),
    }


def prepare_browser_mutation_approval(
    broker: ApprovalBroker,
    *,
    method: str,
    url: str,
    body: bytes | None = b"",
    headers: dict[str, str] | None = None,
    session_id: str = DEFAULT_BROWSER_SESSION_ID,
    auth_context: str = "",
    cookie_header: str = "",
    max_body_bytes: int = 256 * 1024,
    requester_id: str = "mcp-client",
    ttl_seconds: int | None = None,
) -> ApprovalRecord:
    """Prepare a PENDING L3 approval bound to canonical request digests (no raw secrets)."""
    norm_method = method.strip().upper()
    if norm_method not in STATE_CHANGING_METHODS:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Out-of-band mutation approval applies to state-changing methods, not {norm_method}.",
        )
    raw_body = body or b""
    if len(raw_body) > max_body_bytes:
        raise UltimateMCPError(
            ErrorCode.RESOURCE_LIMIT_EXCEEDED,
            f"Mutation payload ({len(raw_body)} bytes) exceeds {max_body_bytes} byte limit.",
        )
    payload = build_mutation_approval_payload(
        norm_method,
        url,
        raw_body,
        headers=headers,
        session_id=session_id,
        auth_context=auth_context,
        cookie_header=cookie_header,
    )
    summary = (
        f"Browser {payload['method']} {payload['url']} "
        f"(session={payload['session_id']}, body={payload['body_sha256'][:12]})"
    )
    return broker.prepare_action(
        BROWSER_MUTATION_ACTION_TYPE,
        PermissionLevel.L3_SENSITIVE,
        payload,
        summary,
        requester_id=requester_id,
        ttl_seconds=ttl_seconds,
    )


class MutationApprovalGate:
    """Single-use, canonical-request-bound, time-bounded L3 gate for mutations."""

    def __init__(self, approval_broker: ApprovalBroker | None = None) -> None:
        self._broker = approval_broker
        self._authorized_ids: list[str] = []

    def authorize(self, approval_id: str) -> None:
        """Register an out-of-band approval_id for one-time matching consumption."""
        cleaned = approval_id.strip()
        if cleaned and cleaned not in self._authorized_ids:
            self._authorized_ids.append(cleaned)

    def consume_for_request(
        self,
        method: str,
        url: str,
        post_data: bytes | None,
        *,
        headers: dict[str, str] | None = None,
        session_id: str = DEFAULT_BROWSER_SESSION_ID,
        auth_context: str = "",
        cookie_header: str = "",
    ) -> str | None:
        """Consume matching out-of-band L3 approval once, or return a denial reason string."""
        if self._broker is None or not self._authorized_ids:
            return (
                f"HTTP method '{method}' blocked: requires explicit out-of-band L3 approval "
                "bound to canonical method, URL, headers, session, and payload digest."
            )
        try:
            expected_payload: dict[str, Any] = build_mutation_approval_payload(
                method,
                url,
                post_data,
                headers=headers,
                session_id=session_id,
                auth_context=auth_context,
                cookie_header=cookie_header,
            )
        except UltimateMCPError as exc:
            return exc.message

        last_err = f"No matching out-of-band L3 approval found for {method} {url}."
        for idx, approval_id in enumerate(list(self._authorized_ids)):
            try:
                rec = self._broker.get_record(approval_id)
                if rec.payload != expected_payload:
                    continue
                if self._broker._clock() >= rec.expires_at:
                    self._authorized_ids.pop(idx)
                    return f"Approval '{approval_id}' has expired."
                self._broker.consume_approved(
                    approval_id,
                    expected_action_type=BROWSER_MUTATION_ACTION_TYPE,
                    expected_payload=expected_payload,
                )
                self._authorized_ids.pop(idx)
                return None
            except UltimateMCPError as exc:
                self._authorized_ids.pop(idx)
                last_err = f"{exc.code.value}: {exc.message}"
        return last_err
