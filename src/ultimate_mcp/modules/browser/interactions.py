"""Ambiguity-checked DOM click, text entry, and option selection interactions (Phase 03)."""

from __future__ import annotations

from typing import Any

from ultimate_mcp.core.models import ErrorCode, ToolResult, UltimateMCPError
from ultimate_mcp.modules.browser.manager import BrowserSession, BrowserSessionManager

INTERACTION_TIMEOUT_MS = 5_000

_INSPECT_CLICKABLE_JS = """
(el) => {
  const tag = el.tagName.toLowerCase();
  const type = (el.getAttribute('type') || '').toLowerCase();
  const hasForm = el.form !== null;
  const isSubmit = (
    (tag === 'input' && (type === 'submit' || type === 'image'))
    || (tag === 'button' && hasForm && (type === '' || type === 'submit'))
  );
  return { tag, type, isSubmit };
}
"""

_INSPECT_EDITABLE_JS = """
(el) => ({
  tag: el.tagName.toLowerCase(),
  type: (el.getAttribute('type') || '').toLowerCase(),
  editable: Boolean(el.isContentEditable),
})
"""


async def _resolve_unique_locator(
    session: BrowserSession,
    page: Any,
    selector: str,
) -> tuple[Any, str]:
    """Resolve snapshot ref or CSS selector and reject missing/ambiguous matches."""
    cleaned = selector.strip()
    if not cleaned:
        raise UltimateMCPError(ErrorCode.INVALID_INPUT, "Selector must not be empty.")
    resolved = session.last_refs.get(cleaned, cleaned)
    locator = page.locator(resolved)
    count = int(await locator.count())
    if count == 0:
        raise UltimateMCPError(ErrorCode.NOT_FOUND, f"No element matched selector '{selector}'.")
    if count > 1:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Selector '{selector}' matched {count} elements (ambiguous); refine selector or ref.",
        )
    return locator, resolved


async def browser_click_service(
    manager: BrowserSessionManager,
    *,
    selector: str,
    session_id: str | None = None,
) -> ToolResult:
    """Click a uniquely identified non-submit element in the active browser page."""
    session = manager.get_session(session_id)
    page = session.current_page
    locator, resolved = await _resolve_unique_locator(session, page, selector)

    meta = await locator.evaluate(_INSPECT_CLICKABLE_JS)
    if bool(meta.get("isSubmit")):
        raise UltimateMCPError(
            ErrorCode.PERMISSION_DENIED,
            "Clicking form submit controls directly is blocked; use browser_submit_form "
            "with L3 out-of-band approval.",
        )

    await locator.click(timeout=INTERACTION_TIMEOUT_MS)
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=1500)
    except Exception:
        pass
    title = (await page.title())[:300]
    return ToolResult.ok(
        tool="browser_click",
        summary=f"Clicked '{selector}' on '{page.url}'.",
        data={
            "session_id": session.session_id,
            "selector": selector,
            "resolved_selector": resolved,
            "url": page.url,
            "title": title,
        },
    )


async def browser_type_service(
    manager: BrowserSessionManager,
    *,
    selector: str,
    text: str,
    clear_first: bool = True,
    session_id: str | None = None,
) -> ToolResult:
    """Type or fill text into a uniquely identified non-file text input element."""
    session = manager.get_session(session_id)
    page = session.current_page
    locator, resolved = await _resolve_unique_locator(session, page, selector)
    meta = await locator.evaluate(_INSPECT_EDITABLE_JS)
    tag = str(meta.get("tag", ""))
    input_type = str(meta.get("type", ""))
    if tag == "input" and input_type == "file":
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            "File inputs cannot be filled with browser_type; use approval-gated browser_upload.",
        )
    if tag not in ("input", "textarea") and not bool(meta.get("editable")):
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Element '{selector}' ({tag}) is not a text input or editable element.",
        )

    if clear_first:
        await locator.fill(text, timeout=INTERACTION_TIMEOUT_MS)
    else:
        await locator.type(text, timeout=INTERACTION_TIMEOUT_MS)

    return ToolResult.ok(
        tool="browser_type",
        summary=f"Entered {len(text)} character(s) into '{selector}'.",
        data={
            "session_id": session.session_id,
            "selector": selector,
            "resolved_selector": resolved,
            "chars_entered": len(text),
            "clear_first": clear_first,
        },
    )


async def browser_select_service(
    manager: BrowserSessionManager,
    *,
    selector: str,
    value: str,
    session_id: str | None = None,
) -> ToolResult:
    """Select an option value on a uniquely identified ``<select>`` element."""
    session = manager.get_session(session_id)
    page = session.current_page
    locator, resolved = await _resolve_unique_locator(session, page, selector)
    meta = await locator.evaluate(_INSPECT_EDITABLE_JS)
    if meta.get("tag") != "select":
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Element '{selector}' is not a <select> control.",
        )
    try:
        selected = await locator.select_option(value, timeout=INTERACTION_TIMEOUT_MS)
    except Exception as exc:
        raise UltimateMCPError(
            ErrorCode.INVALID_INPUT,
            f"Failed to select option '{value}' in '{selector}': {exc}",
        ) from exc

    return ToolResult.ok(
        tool="browser_select",
        summary=f"Selected option {selected} in '{selector}'.",
        data={
            "session_id": session.session_id,
            "selector": selector,
            "resolved_selector": resolved,
            "selected": selected,
        },
    )
