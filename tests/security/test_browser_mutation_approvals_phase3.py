"""Security tests verifying canonical mutation approval binding and zero secret storage."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

import pytest

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.core.config import Settings
from ultimate_mcp.core.http import RawHTTPResponse
from ultimate_mcp.core.models import ErrorCode, UltimateMCPError
from ultimate_mcp.modules.browser.egress import (
    BrowserEgressPolicy,
    prepare_browser_mutation_approval,
)
from ultimate_mcp.modules.browser.forms import _preflight_mutation_approval
from ultimate_mcp.modules.browser.manager import BrowserSessionManager
from ultimate_mcp.modules.browser.transfers import (
    browser_download_service,
    browser_upload_service,
)
from ultimate_mcp.storage.db import init_database


class _SpySock:
    def __init__(self, ip: str, port: int) -> None:
        self._peer = (ip, port)

    def settimeout(self, value: float | None) -> None:
        return None

    def getpeername(self) -> tuple[str, int]:
        return self._peer

    def sendall(self, data: bytes) -> None:
        return None

    def makefile(self, mode: str = "rb", buffering: int = -1) -> Any:
        return io.BytesIO(
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 2\r\n\r\n{}"
        )

    def close(self) -> None:
        return None


def test_mutation_approval_rejects_tampered_headers_origin_cookies_auth_and_session(
    tmp_path: Path,
) -> None:
    """Changing Content-Type, Origin, X-CSRF, Auth, Cookie, or session_id must fail."""
    broker = ApprovalBroker(tmp_path / "apr.sqlite3", clock=lambda: 1000.0)
    dialed: list[tuple[str, int]] = []

    def sock_factory(addr: tuple[str, int], timeout: float) -> Any:
        dialed.append(addr)
        return _SpySock(addr[0], addr[1])

    secret_auth = "Bearer super-secret-jwt-9988"
    secret_cookie = "sessionid=secret-cookie-val-7766; csrf=abc"
    base_headers = {
        "Content-Type": "application/json",
        "Origin": "https://app.example.com",
        "X-CSRF-Token": "csrf-xyz",
    }
    url = "https://api.example.com/transfer"
    body = b'{"amount":10}'

    rec = prepare_browser_mutation_approval(
        broker,
        method="POST",
        url=url,
        body=body,
        headers=base_headers,
        session_id="sess-ctx-A",
        auth_context=secret_auth,
        cookie_header=secret_cookie,
        ttl_seconds=60,
    )
    broker.decide_out_of_band(rec.approval_id, approve=True)

    # Verify ZERO raw auth tokens or raw cookies stored anywhere in approval payload or summary
    stored = broker.get_record(rec.approval_id)
    serialized = repr(stored.payload) + stored.summary
    assert secret_auth not in serialized
    assert secret_cookie not in serialized
    assert "secret-jwt" not in serialized
    assert "secret-cookie" not in serialized
    assert "csrf-xyz" not in repr(stored.payload)

    def _run_attempt(
        *,
        headers: dict[str, str] | None = None,
        session_id: str = "sess-ctx-A",
        auth_context: str = secret_auth,
        cookie_header: str = secret_cookie,
        post_body: bytes = body,
        target_url: str = url,
    ) -> bool:
        pol = BrowserEgressPolicy(
            approval_broker=broker,
            session_id=session_id,
            auth_context=auth_context,
            cookie_header=cookie_header,
            resolver=lambda h, p: ["93.184.216.34"],
            socket_factory=sock_factory,
            ssl_wrapper=lambda s, h: s,
        )
        pol.authorize_mutation(rec.approval_id)
        dec = pol.evaluate_request(
            target_url,
            method="POST",
            resource_type="fetch",
            headers=headers if headers is not None else dict(base_headers),
            post_data=post_body,
        )
        for ev in pol.audit_log:
            assert secret_auth not in repr(ev) and secret_cookie not in repr(ev)
        return dec.allowed

    # 1. Content-Type tampered -> rejected
    assert not _run_attempt(headers={**base_headers, "Content-Type": "text/plain"})
    # 2. Origin header tampered -> rejected
    assert not _run_attempt(headers={**base_headers, "Origin": "https://evil.example.org"})
    # 3. Security header (X-CSRF-Token) tampered -> rejected
    assert not _run_attempt(headers={**base_headers, "X-CSRF-Token": "wrong-csrf"})
    # 4. Authorization context tampered -> rejected
    assert not _run_attempt(auth_context="Bearer different-token")
    # 5. Cookie / session identity tampered -> rejected
    assert not _run_attempt(cookie_header="sessionid=hijacked-user")
    # 6. Different BrowserContext session_id -> rejected
    assert not _run_attempt(session_id="sess-ctx-B")
    # 7. Exact canonical match succeeds once (single-use)
    assert _run_attempt() is True
    assert len(dialed) == 1
    # 8. Replay blocked
    assert not _run_attempt()
    assert len(dialed) == 1


def test_mutation_approval_rejects_exact_expiry_and_redirect_reuse(tmp_path: Path) -> None:
    """Approvals expire at now >= expires_at and redirects cannot reuse mutation approvals."""
    now = [1000.0]
    broker = ApprovalBroker(tmp_path / "exp.sqlite3", clock=lambda: now[0])
    rec = prepare_browser_mutation_approval(
        broker,
        method="POST",
        url="https://api.example.com/step1",
        body=b"ok=1",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        session_id="s-exp",
        ttl_seconds=30,
    )
    broker.decide_out_of_band(rec.approval_id, approve=True)

    # Exact expiry boundary now == expires_at (1030.0) must reject before network dial
    now[0] = 1030.0
    dialed: list[tuple[str, int]] = []
    pol = BrowserEgressPolicy(
        approval_broker=broker,
        session_id="s-exp",
        resolver=lambda h, p: ["93.184.216.34"],
        socket_factory=lambda a, t: (dialed.append(a), _SpySock(a[0], a[1]))[1],
        ssl_wrapper=lambda s, h: s,
    )
    pol.authorize_mutation(rec.approval_id)
    dec = pol.evaluate_request(
        "https://api.example.com/step1",
        method="POST",
        resource_type="fetch",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        post_data=b"ok=1",
    )
    assert dec.allowed is False
    assert dialed == []

    # Fresh approval hitting a 302 redirect: mutation stops at 302 (max_redirects=0) and
    # cannot be replayed against the redirect target.
    now[0] = 2000.0
    rec2 = prepare_browser_mutation_approval(
        broker,
        method="POST",
        url="https://api.example.com/redir",
        body=b"k=v",
        session_id="s-redir",
        ttl_seconds=60,
    )
    broker.decide_out_of_band(rec2.approval_id, approve=True)

    def _redir_sender(*args: Any) -> RawHTTPResponse:
        return RawHTTPResponse(
            url=str(args[0]),
            status_code=302,
            headers={"location": "https://api.example.com/step2", "content-type": "text/html"},
            body=b"",
            peer_ip="93.184.216.34",
        )

    pol2 = BrowserEgressPolicy(
        approval_broker=broker,
        session_id="s-redir",
        resolver=lambda h, p: ["93.184.216.34"],
        sender=_redir_sender,
    )
    pol2.authorize_mutation(rec2.approval_id)
    first = pol2.evaluate_request(
        "https://api.example.com/redir", method="POST", resource_type="fetch", post_data=b"k=v"
    )
    assert first.allowed is False and "Exceeded maximum redirects (0)" in first.reason
    second = pol2.evaluate_request(
        "https://api.example.com/step2", method="POST", resource_type="fetch", post_data=b"k=v"
    )
    assert second.allowed is False


@pytest.mark.asyncio
async def test_form_download_and_upload_lifecycle_bounds_expiry_and_session_binding(
    tmp_path: Path,
) -> None:
    """Verify expiry, auth/session mismatch, and download byte caps across lifecycle helpers."""
    now = [5000.0]
    reads = tmp_path / "reads"
    reads.mkdir()
    target_file = reads / "note.txt"
    target_file.write_text("payload-1", encoding="utf-8")

    cfg = Settings(
        state_dir=tmp_path / "state",
        output_dir=tmp_path / "out",
        read_roots=[reads],
        write_roots=[tmp_path / "out"],
        browser_max_sessions=2,
        browser_max_download_bytes=1024,
    )
    cfg.ensure_directories()
    init_database(cfg.db_path)
    broker = ApprovalBroker(cfg.db_path, default_ttl_seconds=30, clock=lambda: now[0])

    # 1. Form preflight rejects changed auth_context, changed cookie_header, and expiry
    f_rec = prepare_browser_mutation_approval(
        broker,
        method="POST",
        url="https://app.example.test/save",
        body=b"x=1",
        headers={"content-type": "application/x-www-form-urlencoded"},
        session_id="sess-1",
        auth_context="Bearer tok-1",
        cookie_header="sid=c1",
    )
    broker.decide_out_of_band(f_rec.approval_id, approve=True)
    with pytest.raises(UltimateMCPError) as auth_err:
        _preflight_mutation_approval(
            broker,
            f_rec.approval_id,
            method="POST",
            target_url="https://app.example.test/save",
            body_bytes=b"x=1",
            headers={"content-type": "application/x-www-form-urlencoded"},
            session_id="sess-1",
            auth_context="Bearer tok-OTHER",
            cookie_header="sid=c1",
        )
    assert auth_err.value.code == ErrorCode.APPROVAL_INVALID
    with pytest.raises(UltimateMCPError) as cookie_err:
        _preflight_mutation_approval(
            broker,
            f_rec.approval_id,
            method="POST",
            target_url="https://app.example.test/save",
            body_bytes=b"x=1",
            headers={"content-type": "application/x-www-form-urlencoded"},
            session_id="sess-1",
            auth_context="Bearer tok-1",
            cookie_header="sid=hijacked",
        )
    assert cookie_err.value.code == ErrorCode.APPROVAL_INVALID
    now[0] = 5030.0
    with pytest.raises(UltimateMCPError) as fexp_err:
        _preflight_mutation_approval(
            broker,
            f_rec.approval_id,
            method="POST",
            target_url="https://app.example.test/save",
            body_bytes=b"x=1",
            headers={"content-type": "application/x-www-form-urlencoded"},
            session_id="sess-1",
            auth_context="Bearer tok-1",
            cookie_header="sid=c1",
        )
    assert fexp_err.value.code == ErrorCode.APPROVAL_EXPIRED

    # 2. Download enforces configured max_bytes limit and rejects expired approvals
    now[0] = 6000.0

    class _LargeSock(_SpySock):
        def makefile(self, mode: str = "rb", buffering: int = -1) -> Any:
            big = b"A" * 2048
            hdr = (
                f"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\n"
                f"Content-Length: {len(big)}\r\n\r\n"
            ).encode()
            return io.BytesIO(hdr + big)

    mgr = BrowserSessionManager(
        approval_broker=broker,
        max_sessions=cfg.browser_max_sessions,
        resolver=lambda h, _p: ["93.184.216.34"] if h == "dl.example.test" else [],
        socket_factory=lambda a, _t: _LargeSock(a[0], a[1]),
        ssl_wrapper=lambda s, _h: s,
    )
    dl_prep = await browser_download_service(mgr, broker, cfg, url="http://dl.example.test/big.txt")
    broker.decide_out_of_band(str(dl_prep.data["approval_id"]), approve=True)
    with pytest.raises(UltimateMCPError) as cap_err:
        await browser_download_service(
            mgr, broker, cfg, url="http://dl.example.test/big.txt",
            approval_id=str(dl_prep.data["approval_id"]),
        )
    assert cap_err.value.code == ErrorCode.RESOURCE_LIMIT_EXCEEDED

    # 3. Upload rejects session switch and expired approval using stubbed page/session
    class _FakeLoc:
        async def count(self) -> int:
            return 1

        async def evaluate(self, _js: str) -> dict[str, str]:
            return {"tag": "input", "type": "file"}

        async def set_input_files(self, _path: str) -> None:
            return None

    class _FakePage:
        def __init__(self, url: str) -> None:
            self.url = url

        def locator(self, _sel: str) -> _FakeLoc:
            return _FakeLoc()

    class _StubMgr(BrowserSessionManager):
        def __init__(self, page: Any, sid: str) -> None:
            super().__init__(approval_broker=broker)
            self._sid = sid
            self._page = page

        def get_session(self, session_id: str | None = None) -> Any:
            return type(
                "_S",
                (),
                {
                    "session_id": session_id or self._sid,
                    "current_page": self._page,
                    "last_refs": {},
                },
            )()

    stub = _StubMgr(_FakePage("https://up.example.test/form"), "up-A")
    up_prep = await browser_upload_service(
        stub, broker, cfg, selector="#f", file_path=str(target_file), session_id="up-A"
    )
    up_id = str(up_prep.data["approval_id"])
    broker.decide_out_of_band(up_id, approve=True)

    with pytest.raises(UltimateMCPError) as sess_err:
        await browser_upload_service(
            stub, broker, cfg, selector="#f", file_path=str(target_file),
            session_id="up-B", approval_id=up_id,
        )
    assert sess_err.value.code == ErrorCode.APPROVAL_INVALID

    now[0] = 6030.0
    with pytest.raises(UltimateMCPError) as uexp_err:
        await browser_upload_service(
            stub, broker, cfg, selector="#f", file_path=str(target_file),
            session_id="up-A", approval_id=up_id,
        )
    assert uexp_err.value.code == ErrorCode.APPROVAL_EXPIRED
