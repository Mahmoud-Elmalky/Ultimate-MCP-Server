"""Security tests verifying canonical mutation approval binding and zero secret storage."""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.modules.browser.egress import (
    BrowserEgressPolicy,
    prepare_browser_mutation_approval,
)


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
