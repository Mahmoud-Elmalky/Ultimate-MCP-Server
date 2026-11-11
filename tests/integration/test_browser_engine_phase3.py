"""End-to-end real Chromium tests for Phase 03 browser engine & missing-Playwright behavior."""

from __future__ import annotations

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.bootstrap import build_doctor_report, create_gateway
from ultimate_mcp.core.config import ProfileName, Settings
from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.modules.browser.action_tools import browser_close_session_service
from ultimate_mcp.modules.browser.forms import browser_submit_form_service
from ultimate_mcp.modules.browser.interactions import (
    browser_click_service,
    browser_select_service,
    browser_type_service,
)
from ultimate_mcp.modules.browser.manager import BrowserSessionManager
from ultimate_mcp.modules.browser.navigation import (
    browser_back_service,
    browser_open_service,
    browser_screenshot_service,
    browser_scroll_service,
    browser_snapshot_service,
    browser_tabs_service,
    browser_wait_service,
)
from ultimate_mcp.modules.browser.transfers import (
    browser_download_service,
    browser_upload_service,
)
from ultimate_mcp.storage.db import init_database

_HELPER_PATH = Path(__file__).with_name("_browser_test_servers.py")
_SPEC = importlib.util.spec_from_file_location("_browser_test_servers", _HELPER_PATH)
assert _SPEC and _SPEC.loader
_HELPERS = importlib.util.module_from_spec(_SPEC)
sys.modules["_browser_test_servers"] = _HELPERS
_SPEC.loader.exec_module(_HELPERS)
RecordedHTTPServer = _HELPERS.RecordedHTTPServer
make_public_sim_socket_factory = _HELPERS.make_public_sim_socket_factory

PUBLIC_HOST = "public.example.test"
PUBLIC_IP = "93.184.216.34"


def _fixture_handler(method: str, path: str, body: bytes) -> tuple[int, dict[str, str], bytes]:
    if path == "/":
        html = (
            b"<html><head><title>Home Fixture</title></head><body>"
            b"<a id='p2' href='/page2'>Next</a>"
            b"<button class='dup'>One</button><button class='dup'>Two</button>"
            b"<input id='q' type='text' value=''/>"
            b"<select id='role'><option value='dev'>D</option>"
            b"<option value='sec'>S</option></select>"
            b"<button id='show-btn' onclick=\"document.getElementById('dyn')"
            b".textContent='READY'\">Go</button>"
            b"<div id='dyn'></div><div style='height:1800px'>Spacer</div></body></html>"
        )
        return (200, {"Content-Type": "text/html"}, html)
    if path == "/page2":
        return (200, {"Content-Type": "text/html"}, b"<title>Page Two</title><h1>Two</h1>")
    if path == "/form":
        html = (
            b"<title>Form Page</title>"
            b"<form id='acct-form' method='POST' action='/submit'>"
            b"<input id='act' name='action' value='rename'/>"
            b"<button id='direct-submit' type='submit'>Submit</button></form>"
        )
        return (200, {"Content-Type": "text/html"}, html)
    if path == "/submit" and method == "POST":
        return (
            200,
            {"Content-Type": "text/html"},
            f"<title>Submitted</title><pre>{body.decode()}</pre>".encode(),
        )
    if path == "/upload":
        return (
            200,
            {"Content-Type": "text/html"},
            b"<title>Upload</title><input id='up-file' type='file' name='doc'/>",
        )
    if path == "/report.txt":
        return (200, {"Content-Type": "text/plain"}, b"safe-browser-download-payload")
    if path == "/installer.exe":
        return (200, {"Content-Type": "application/octet-stream"}, b"MZfake")
    return (404, {"Content-Type": "text/plain"}, b"not found")


def _make_runtime(
    tmp_path: Path, srv_port: int
) -> tuple[Settings, ApprovalBroker, BrowserSessionManager]:
    cfg = Settings(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
        read_roots=[tmp_path / "reads"],
        write_roots=[tmp_path / "out"],
    )
    cfg.ensure_directories()
    (tmp_path / "reads").mkdir(parents=True, exist_ok=True)
    init_database(cfg.db_path)
    broker = ApprovalBroker(cfg.db_path, default_ttl_seconds=300)
    mgr = BrowserSessionManager(
        approval_broker=broker,
        max_tabs=5,
        resolver=lambda host, _p: [PUBLIC_IP] if host == PUBLIC_HOST else [],
        socket_factory=make_public_sim_socket_factory(srv_port, expected_public_ip=PUBLIC_IP),
    )
    return cfg, broker, mgr


@pytest.mark.asyncio
async def test_browser_nav_snapshot_screenshot_scroll_wait_tabs_isolation(tmp_path: Path) -> None:
    srv = RecordedHTTPServer(_fixture_handler)
    port = srv.start()
    cfg, _broker, mgr = _make_runtime(tmp_path, port)
    base = f"http://{PUBLIC_HOST}"
    try:
        r1 = await browser_open_service(mgr, url=f"{base}/", session_id="s1")
        s1_page = mgr.get_session("s1").current_page
        await s1_page.evaluate("() => localStorage.setItem('k', 'secret-s1')")

        await browser_open_service(mgr, url=f"{base}/", session_id="s2")
        s2_page = mgr.get_session("s2").current_page
        assert await s2_page.evaluate("() => localStorage.getItem('k')") is None

        snap = await browser_snapshot_service(mgr, session_id="s1", max_chars=2000)
        assert snap.warnings and "UNTRUSTED WEB CONTENT" in snap.warnings[0]
        assert snap.data["content_trust"] == "untrusted_external_page"
        assert len(snap.data["elements"]) >= 4
        assert any(el["ref"] == "e1" for el in snap.data["elements"])

        shot = await browser_screenshot_service(mgr, cfg, session_id="s1", filename="home.png")
        shot_path = Path(shot.data["artifact_path"])
        assert shot_path.exists() and shot_path.parent.parent == cfg.output_dir.resolve()
        assert "data:image" not in str(shot.data)

        scroll_res = await browser_scroll_service(
            mgr, session_id="s1", direction="down", pixels=400
        )
        assert scroll_res.data["scroll_y"] > 0

        await browser_click_service(mgr, selector="#show-btn", session_id="s1")
        wait_res = await browser_wait_service(mgr, session_id="s1", text="READY")
        assert wait_res.data["text"] == "READY"

        await browser_open_service(mgr, url=f"{base}/page2", session_id="s1")
        back_res = await browser_back_service(mgr, session_id="s1")
        assert back_res.data["url"].endswith("/")

        for _ in range(4):
            await browser_tabs_service(mgr, session_id="s1", action="new")
        with pytest.raises(UltimateMCPError) as tab_cap:
            await browser_tabs_service(mgr, session_id="s1", action="new")
        assert tab_cap.value.code == ErrorCode.RESOURCE_LIMIT_EXCEEDED
        await browser_tabs_service(mgr, session_id="s1", action="switch", tab_index=0)
        await browser_tabs_service(mgr, session_id="s1", action="close", tab_index=1)

        closed = await browser_close_session_service(mgr, session_id="s1")
        assert closed.data["closed"] is True
        assert "s1" not in mgr.active_session_ids and r1.data["session_id"] == "s1"
    finally:
        await mgr.close_all()
        srv.stop()


@pytest.mark.asyncio
async def test_browser_interactions_ambiguity_and_blocked_submit(tmp_path: Path) -> None:
    srv = RecordedHTTPServer(_fixture_handler)
    port = srv.start()
    _cfg, _broker, mgr = _make_runtime(tmp_path, port)
    base = f"http://{PUBLIC_HOST}"
    try:
        await browser_open_service(mgr, url=f"{base}/", session_id="ix")
        with pytest.raises(UltimateMCPError) as ambig:
            await browser_click_service(mgr, selector=".dup", session_id="ix")
        assert ambig.value.code == ErrorCode.INVALID_INPUT

        with pytest.raises(UltimateMCPError) as missing:
            await browser_click_service(mgr, selector="#nope", session_id="ix")
        assert missing.value.code == ErrorCode.NOT_FOUND

        typed = await browser_type_service(mgr, selector="#q", text="hello", session_id="ix")
        assert typed.data["chars_entered"] == 5
        sel = await browser_select_service(mgr, selector="#role", value="sec", session_id="ix")
        assert sel.data["selected"] == ["sec"]

        await browser_open_service(mgr, url=f"{base}/form", session_id="ix")
        with pytest.raises(UltimateMCPError) as blocked_sub:
            await browser_click_service(mgr, selector="#direct-submit", session_id="ix")
        assert blocked_sub.value.code == ErrorCode.PERMISSION_DENIED
        assert all(p != "/submit" for _, p, _ in srv.hits)
    finally:
        await mgr.close_all()
        srv.stop()


@pytest.mark.asyncio
async def test_browser_form_submit_oob_approval_and_tamper_rejection(tmp_path: Path) -> None:
    srv = RecordedHTTPServer(_fixture_handler)
    port = srv.start()
    _cfg, broker, mgr = _make_runtime(tmp_path, port)
    base = f"http://{PUBLIC_HOST}"
    try:
        await browser_open_service(mgr, url=f"{base}/form", session_id="frm")
        prep = await browser_submit_form_service(
            mgr, broker, form_selector="#acct-form",
            field_values={"action": "archive"}, session_id="frm",
        )
        assert prep.status == "needs_approval"
        aid = str(prep.data["approval_id"])
        assert all(p != "/submit" for _, p, _ in srv.hits)

        broker.decide_out_of_band(aid, approve=True)
        with pytest.raises(UltimateMCPError) as tampered:
            await browser_submit_form_service(
                mgr, broker, form_selector="#acct-form", field_values={"action": "delete"},
                session_id="frm", approval_id=aid,
            )
        assert tampered.value.code == ErrorCode.APPROVAL_INVALID
        assert all(p != "/submit" for _, p, _ in srv.hits)

        done = await browser_submit_form_service(
            mgr, broker, form_selector="#acct-form", field_values={"action": "archive"},
            session_id="frm", approval_id=aid,
        )
        assert done.data["status_code"] == 200
        assert ("POST", "/submit", b"action=archive") in srv.hits

        await browser_open_service(mgr, url=f"{base}/form", session_id="frm")
        with pytest.raises(UltimateMCPError) as replay:
            await browser_submit_form_service(
                mgr, broker, form_selector="#acct-form", field_values={"action": "archive"},
                session_id="frm", approval_id=aid,
            )
        assert replay.value.code == ErrorCode.APPROVAL_INVALID
    finally:
        await mgr.close_all()
        srv.stop()


@pytest.mark.asyncio
async def test_browser_download_upload_approvals_and_missing_playwright(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    srv = RecordedHTTPServer(_fixture_handler)
    port = srv.start()
    cfg, broker, mgr = _make_runtime(tmp_path, port)
    base = f"http://{PUBLIC_HOST}"
    try:
        with pytest.raises(UltimateMCPError) as exe_err:
            await browser_download_service(mgr, broker, cfg, url=f"{base}/installer.exe")
        assert exe_err.value.code == ErrorCode.PERMISSION_DENIED

        with pytest.raises(UltimateMCPError) as trav_err:
            await browser_download_service(
                mgr, broker, cfg, url=f"{base}/report.txt", filename="../escape.txt"
            )
        assert trav_err.value.code == ErrorCode.INVALID_INPUT

        dl_prep = await browser_download_service(mgr, broker, cfg, url=f"{base}/report.txt")
        assert dl_prep.status == "needs_approval"
        broker.decide_out_of_band(str(dl_prep.data["approval_id"]), approve=True)
        dl_ok = await browser_download_service(
            mgr, broker, cfg, url=f"{base}/report.txt", approval_id=str(dl_prep.data["approval_id"])
        )
        dl_path = Path(dl_ok.data["path"])
        assert dl_path.read_bytes() == b"safe-browser-download-payload"
        assert dl_ok.data["sha256"] == hashlib.sha256(b"safe-browser-download-payload").hexdigest()

        await browser_open_service(mgr, url=f"{base}/upload", session_id="up")
        outside = tmp_path / "outside.txt"
        outside.write_text("nope", encoding="utf-8")
        with pytest.raises(UltimateMCPError) as out_err:
            await browser_upload_service(
                mgr, broker, cfg, selector="#up-file", file_path=str(outside), session_id="up"
            )
        assert out_err.value.code == ErrorCode.PATH_OUTSIDE_WORKSPACE

        upload_file = tmp_path / "reads" / "doc.txt"
        upload_file.write_text("v1-clean", encoding="utf-8")
        up_prep = await browser_upload_service(
            mgr, broker, cfg, selector="#up-file", file_path=str(upload_file), session_id="up"
        )
        up_id = str(up_prep.data["approval_id"])
        broker.decide_out_of_band(up_id, approve=True)
        upload_file.write_text("v2-tampered", encoding="utf-8")
        with pytest.raises(UltimateMCPError) as hash_err:
            await browser_upload_service(
                mgr, broker, cfg, selector="#up-file", file_path=str(upload_file),
                session_id="up", approval_id=up_id,
            )
        assert hash_err.value.code == ErrorCode.APPROVAL_INVALID

        up_prep2 = await browser_upload_service(
            mgr, broker, cfg, selector="#up-file", file_path=str(upload_file), session_id="up"
        )
        broker.decide_out_of_band(str(up_prep2.data["approval_id"]), approve=True)
        up_ok = await browser_upload_service(
            mgr, broker, cfg, selector="#up-file", file_path=str(upload_file),
            session_id="up", approval_id=str(up_prep2.data["approval_id"]),
        )
        assert up_ok.data["path"] == str(upload_file.resolve())
    finally:
        await mgr.close_all()
        srv.stop()

    browser_gw = create_gateway(settings=cfg, profile_override=ProfileName.BROWSER)
    assert len(browser_gw.registry.enabled_specs(cfg, ProfileName.BROWSER)) == 15
    open_spec = browser_gw.registry.get("browser_open")
    assert open_spec is not None
    ssrf_call = await open_spec.mcp_wrapper(url="http://127.0.0.1:1/")
    assert ssrf_call.is_error is True

    def _fake_pkg_check(name: str) -> bool:
        return False if name == "playwright" else True

    monkeypatch.setattr("ultimate_mcp.core.registry.is_package_available", _fake_pkg_check)
    monkeypatch.setattr("ultimate_mcp.core.bootstrap.is_package_available", _fake_pkg_check)
    monkeypatch.setattr(
        "ultimate_mcp.modules.browser.manager.is_package_available", _fake_pkg_check
    )
    doc = build_doctor_report(settings=cfg, profile_override=ProfileName.FULL)
    assert doc["browser"]["playwright_installed"] is False
    assert "browser_open" not in doc["enabled_tools"]
    gw = create_gateway(settings=cfg, profile_override=ProfileName.DEFAULT)
    assert len(gw.registry.enabled_specs(cfg, ProfileName.DEFAULT)) == 5
