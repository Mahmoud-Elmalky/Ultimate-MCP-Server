"""L3 out-of-band approval-gated HTML form submission service (Phase 03)."""

from __future__ import annotations

import asyncio
import urllib.parse
from typing import Any

from ultimate_mcp.core.approvals import ApprovalBroker, ApprovalStatus
from ultimate_mcp.core.models import ErrorCode, ToolResult, UltimateMCPError
from ultimate_mcp.core.permissions import PermissionLevel
from ultimate_mcp.modules.browser.interactions import (
    _INSPECT_EDITABLE_JS,
    _resolve_unique_locator,
)
from ultimate_mcp.modules.browser.manager import BrowserSession, BrowserSessionManager
from ultimate_mcp.modules.browser.mutation_auth import (
    BROWSER_MUTATION_ACTION_TYPE,
    build_mutation_approval_payload,
    prepare_browser_mutation_approval,
)
from ultimate_mcp.modules.browser.policy_types import STATE_CHANGING_METHODS

ACTION_BROWSER_FORM_GET = "browser_form_submit_get"
FORM_SUBMIT_TIMEOUT_MS = 15_000

_PREVIEW_FORM_JS = """
(form, overrides) => {
  const data = new FormData(form);
  if (overrides && typeof overrides === 'object') {
    for (const [k, v] of Object.entries(overrides)) {
      data.set(String(k), String(v));
    }
  }
  const params = new URLSearchParams();
  for (const [k, v] of data.entries()) {
    if (typeof v === 'string') params.append(k, v);
  }
  return {
    action: form.getAttribute('action') || '',
    method: (form.getAttribute('method') || 'GET').toUpperCase(),
    enctype: (form.getAttribute('enctype') || 'application/x-www-form-urlencoded').toLowerCase(),
    encodedBody: params.toString(),
  };
}
"""

_APPLY_AND_SUBMIT_JS = """
(form, overrides) => {
  if (overrides && typeof overrides === 'object') {
    for (const [k, v] of Object.entries(overrides)) {
      const target = form.elements.namedItem(String(k));
      if (target && 'value' in target) {
        target.value = String(v);
      } else {
        const hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = String(k);
        hidden.value = String(v);
        form.appendChild(hidden);
      }
    }
  }
  form.submit();
}
"""


def _compute_form_headers(page_url: str, target_url: str, enctype: str) -> dict[str, str]:
    parsed_page = urllib.parse.urlsplit(page_url)
    parsed_target = urllib.parse.urlsplit(target_url)
    origin = f"{parsed_page.scheme.lower()}://{parsed_page.netloc.lower()}"
    same_origin = (
        parsed_page.scheme.lower() == parsed_target.scheme.lower()
        and parsed_page.netloc.lower() == parsed_target.netloc.lower()
    )
    full_ref = urllib.parse.urlunsplit(
        (parsed_page.scheme, parsed_page.netloc, parsed_page.path, parsed_page.query, "")
    )
    return {
        "content-type": enctype,
        "origin": origin,
        "referer": full_ref if same_origin else f"{origin}/",
    }


async def _prepare_form_submission(
    broker: ApprovalBroker,
    session: BrowserSession,
    page: Any,
    form_selector: str,
    meta: dict[str, Any],
) -> ToolResult:
    target_url = urllib.parse.urljoin(page.url, str(meta.get("action") or page.url))
    method = str(meta.get("method") or "GET").upper()
    enctype = str(meta.get("enctype") or "application/x-www-form-urlencoded")
    body_bytes = str(meta.get("encodedBody") or "").encode("utf-8")

    if method in STATE_CHANGING_METHODS:
        headers = _compute_form_headers(page.url, target_url, enctype)
        record = prepare_browser_mutation_approval(
            broker, method=method, url=target_url, body=body_bytes,
            headers=headers, session_id=session.session_id,
            auth_context=session.policy.auth_context,
            cookie_header=session.policy.cookie_header,
        )
    else:
        record = broker.prepare_action(
            ACTION_BROWSER_FORM_GET,
            PermissionLevel.L3_SENSITIVE,
            {
                "method": method, "url": target_url,
                "query": str(meta.get("encodedBody") or ""),
                "form_selector": form_selector, "session_id": session.session_id,
            },
            f"Browser {method} form submission to '{target_url}' (session={session.session_id})",
        )
    return ToolResult.pending_approval(
        tool="browser_submit_form", approval_id=record.approval_id, summary=record.summary,
        digest=record.payload_digest, expires_at=record.expires_at_iso, target=target_url,
    )


def _preflight_mutation_approval(
    broker: ApprovalBroker,
    approval_id: str,
    *,
    method: str,
    target_url: str,
    body_bytes: bytes,
    headers: dict[str, str],
    session_id: str,
    auth_context: str = "",
    cookie_header: str = "",
) -> None:
    try:
        record = broker.get_record(approval_id)
    except UltimateMCPError as exc:
        raise UltimateMCPError(ErrorCode.APPROVAL_INVALID, exc.message) from exc
    if record.status == ApprovalStatus.PENDING:
        raise UltimateMCPError(ErrorCode.APPROVAL_REQUIRED, f"Approval '{approval_id}' is pending.")
    if record.status == ApprovalStatus.EXPIRED:
        raise UltimateMCPError(ErrorCode.APPROVAL_EXPIRED, f"Approval '{approval_id}' has expired.")
    if (
        record.status != ApprovalStatus.APPROVED
        or record.action_type != BROWSER_MUTATION_ACTION_TYPE
    ):
        raise UltimateMCPError(
            ErrorCode.APPROVAL_INVALID,
            f"Approval '{approval_id}' is not valid/approved for browser form mutation.",
        )
    expected = build_mutation_approval_payload(
        method, target_url, body_bytes, headers=headers, session_id=session_id,
        auth_context=auth_context, cookie_header=cookie_header,
    )
    if expected != record.payload:
        raise UltimateMCPError(
            ErrorCode.APPROVAL_INVALID,
            "Form target, method, headers, session, or fields do not match approved digest.",
        )


async def _execute_form_submit_and_wait(
    page: Any,
    locator: Any,
    field_values: dict[str, str] | None,
) -> tuple[int | None, str]:
    failed_reason: list[str] = []
    status_holder: list[int] = []

    def _on_failed(req: Any) -> None:
        if req.is_navigation_request():
            failed_reason.append(str(getattr(req, "failure", "request failed")))

    def _on_response(resp: Any) -> None:
        if resp.request.is_navigation_request():
            status_holder.append(int(resp.status))

    page.on("requestfailed", _on_failed)
    page.on("response", _on_response)
    try:
        async with page.expect_navigation(
            wait_until="domcontentloaded", timeout=FORM_SUBMIT_TIMEOUT_MS
        ):
            await locator.evaluate(_APPLY_AND_SUBMIT_JS, field_values or {})
    except Exception as exc:
        if failed_reason or "ERR_BLOCKED_BY_CLIENT" in str(exc):
            detail = failed_reason[0] if failed_reason else str(exc)
            raise UltimateMCPError(
                ErrorCode.APPROVAL_INVALID,
                f"Form submission blocked by browser egress policy: {detail}",
            ) from exc
        raise UltimateMCPError(ErrorCode.SSRF_BLOCKED, f"Form submission failed: {exc}") from exc
    finally:
        page.remove_listener("requestfailed", _on_failed)
        page.remove_listener("response", _on_response)

    if failed_reason and not status_holder:
        raise UltimateMCPError(ErrorCode.APPROVAL_INVALID, f"Form blocked: {failed_reason[0]}")
    return (status_holder[-1] if status_holder else None), page.url


async def browser_submit_form_service(
    manager: BrowserSessionManager,
    broker: ApprovalBroker,
    *,
    form_selector: str = "form",
    field_values: dict[str, str] | None = None,
    session_id: str | None = None,
    approval_id: str | None = None,
) -> ToolResult:
    """Prepare or execute an L3 out-of-band approved HTML form submission."""
    session = manager.get_session(session_id)
    page = session.current_page
    locator, resolved = await _resolve_unique_locator(session, page, form_selector)
    el_meta = await locator.evaluate(_INSPECT_EDITABLE_JS)
    if el_meta.get("tag") != "form":
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, f"'{form_selector}' is not a <form>.")

    preview = await locator.evaluate(_PREVIEW_FORM_JS, field_values or {})
    if not approval_id:
        return await _prepare_form_submission(broker, session, page, form_selector, preview)

    target_url = urllib.parse.urljoin(page.url, str(preview.get("action") or page.url))
    method = str(preview.get("method") or "GET").upper()
    enctype = str(preview.get("enctype") or "application/x-www-form-urlencoded")
    body_bytes = str(preview.get("encodedBody") or "").encode("utf-8")
    cleaned_id = approval_id.strip()

    if method in STATE_CHANGING_METHODS:
        headers = _compute_form_headers(page.url, target_url, enctype)
        _preflight_mutation_approval(
            broker, cleaned_id, method=method, target_url=target_url,
            body_bytes=body_bytes, headers=headers, session_id=session.session_id,
            auth_context=session.policy.auth_context,
            cookie_header=session.policy.cookie_header,
        )
        session.policy.authorize_mutation(cleaned_id)
    else:
        broker.consume_approved(
            cleaned_id,
            expected_action_type=ACTION_BROWSER_FORM_GET,
            expected_payload={
                "method": method, "url": target_url,
                "query": str(preview.get("encodedBody") or ""),
                "form_selector": form_selector, "session_id": session.session_id,
            },
        )

    status_code, final_url = await _execute_form_submit_and_wait(page, locator, field_values)
    await asyncio.sleep(0)
    return ToolResult.ok(
        tool="browser_submit_form",
        summary=f"Submitted {method} form to '{final_url}' (HTTP {status_code}).",
        data={
            "session_id": session.session_id, "form_selector": resolved, "method": method,
            "status_code": status_code, "final_url": final_url,
            "title": (await page.title())[:300], "approval_id": cleaned_id,
        },
    )
