"""Real headless Chromium integration tests for Phase 03 Slice 3.1.1 egress security."""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from ultimate_mcp.core.approvals import ApprovalBroker
from ultimate_mcp.modules.browser.egress import (
    BrowserEgressPolicy,
    LoopbackValidatingProxy,
    prepare_browser_mutation_approval,
)

_HELPER_PATH = Path(__file__).with_name("_browser_test_servers.py")
_SPEC = importlib.util.spec_from_file_location("_browser_test_servers", _HELPER_PATH)
assert _SPEC and _SPEC.loader
_HELPERS = importlib.util.module_from_spec(_SPEC)
sys.modules["_browser_test_servers"] = _HELPERS
_SPEC.loader.exec_module(_HELPERS)

RecordedHTTPServer = _HELPERS.RecordedHTTPServer
RecordedWSServer = _HELPERS.RecordedWSServer
RecordedUDPListener = _HELPERS.RecordedUDPListener
RecordedTCPListener = _HELPERS.RecordedTCPListener
make_public_sim_socket_factory = _HELPERS.make_public_sim_socket_factory
build_public_sim_handler = _HELPERS.build_public_sim_handler
_resolver = _HELPERS.sim_resolver

pw_async = pytest.importorskip("playwright.async_api")


@pytest.fixture
def victim_http() -> Iterator[Any]:
    srv = RecordedHTTPServer(lambda m, p, b: (200, {"Content-Type": "text/plain"}, b"SECRET"))
    srv.start()
    yield srv
    srv.stop()


@pytest.fixture
def public_sim(victim_http: Any) -> Iterator[Any]:
    srv = RecordedHTTPServer(build_public_sim_handler(victim_http.port))
    srv.start()
    yield srv
    srv.stop()


async def test_chromium_allows_validated_public_navigation_and_blocks_redirects(
    public_sim: Any, victim_http: Any
) -> None:
    """Real Chromium loads public page and rejects 302 redirects to loopback/metadata."""
    factory = make_public_sim_socket_factory(public_sim.port)
    async with LoopbackValidatingProxy(resolver=_resolver, socket_factory=factory) as proxy:
        policy = BrowserEgressPolicy(proxy=proxy, resolver=_resolver, socket_factory=factory)
        async with pw_async.async_playwright() as p:
            browser = await p.chromium.launch(**policy.build_launch_options(headless=True))
            ctx = await browser.new_context(**policy.build_context_options())
            await policy.attach_to_context(ctx)
            page = await ctx.new_page()

            await page.goto("http://public.example.test/page")
            assert "Safe Public Page" in await page.inner_text("h1")

            for bad_path in ("/redirect-loopback", "/redirect-metadata"):
                with pytest.raises(pw_async.Error):
                    await page.goto(f"http://public.example.test{bad_path}")
            await browser.close()

    assert victim_http.hits == []
    assert any(h[1] == "/page" for h in public_sim.hits)


async def test_chromium_blocks_private_images_scripts_iframes_and_fetch_xhr(
    public_sim: Any, victim_http: Any
) -> None:
    """Real Chromium blocks private img, script, iframe, fetch, and XHR; 0 victim hits."""
    factory = make_public_sim_socket_factory(public_sim.port)
    async with LoopbackValidatingProxy(resolver=_resolver, socket_factory=factory) as proxy:
        policy = BrowserEgressPolicy(proxy=proxy, resolver=_resolver, socket_factory=factory)
        async with pw_async.async_playwright() as p:
            browser = await p.chromium.launch(**policy.build_launch_options(headless=True))
            ctx = await browser.new_context(**policy.build_context_options())
            await policy.attach_to_context(ctx)
            page = await ctx.new_page()
            await page.goto("http://public.example.test/hostile-embeds")
            res = await page.evaluate(
                "async () => [await window._f1, await window._f2, await window._x]"
            )
            img_ok = await page.evaluate(
                "() => document.getElementById('im').naturalWidth > 0"
            )
            await browser.close()

    assert res == ["BLK", "BLK", "BLK"]
    assert img_ok is False
    assert victim_http.hits == []


async def test_chromium_blocks_websockets_ws_and_wss_across_targets(
    public_sim: Any,
) -> None:
    """Verify real Chromium route_web_socket blocks ws:// and wss:// across all targets."""
    ws_srv = RecordedWSServer()
    await ws_srv.start()
    factory = make_public_sim_socket_factory(public_sim.port)
    try:
        async with LoopbackValidatingProxy(resolver=_resolver, socket_factory=factory) as proxy:
            policy = BrowserEgressPolicy(proxy=proxy, resolver=_resolver, socket_factory=factory)
            async with pw_async.async_playwright() as p:
                browser = await p.chromium.launch(**policy.build_launch_options(headless=True))
                ctx = await browser.new_context(**policy.build_context_options())
                await policy.attach_to_context(ctx)
                page = await ctx.new_page()
                await page.goto("http://public.example.test/page")
                targets = [
                    f"ws://127.0.0.1:{ws_srv.port}/ws",
                    f"wss://localhost:{ws_srv.port}/ws",
                    "ws://10.0.0.5/ws",
                    "wss://93.184.216.34/ws",
                    "ws://public.example.test/ws",
                ]
                outcomes = await page.evaluate(
                    """async (urls) => {
                      const out = [];
                      for (const u of urls) {
                        out.push(await new Promise(r => {
                          const w = new WebSocket(u);
                          w.onopen = () => r("OPEN");
                          w.onclose = e => r("CLOSED:" + e.code);
                          w.onerror = () => r("ERR");
                        }));
                      }
                      return out;
                    }""",
                    targets,
                )
                await browser.close()
    finally:
        await ws_srv.stop()

    assert all(item != "OPEN" for item in outcomes)
    assert ws_srv.hits == []


async def test_chromium_disables_service_workers_and_downloads(
    public_sim: Any,
) -> None:
    """Real Chromium rejects ServiceWorker registration and blocks attachment downloads."""
    factory = make_public_sim_socket_factory(public_sim.port)
    async with LoopbackValidatingProxy(resolver=_resolver, socket_factory=factory) as proxy:
        policy = BrowserEgressPolicy(proxy=proxy, resolver=_resolver, socket_factory=factory)
        async with pw_async.async_playwright() as p:
            browser = await p.chromium.launch(**policy.build_launch_options(headless=True))
            ctx = await browser.new_context(**policy.build_context_options())
            await policy.attach_to_context(ctx)
            page = await ctx.new_page()
            await page.goto("http://public.example.test/sw-page")
            sw_state = await page.evaluate(
                """async () => {
                  try {
                    const reg = await navigator.serviceWorker.register('/sw.js');
                    return reg ? 'REGISTERED' : 'NONE';
                  } catch (e) { return 'BLOCKED:' + e.name; }
                }"""
            )
            with pytest.raises(pw_async.Error):
                await page.goto("http://public.example.test/download")
            await browser.close()

    assert sw_state.startswith("BLOCKED:")
    assert not any(h[1] == "/sw.js" for h in public_sim.hits)


async def test_chromium_proxy_enforces_fail_closed_without_route_hooks(
    victim_http: Any,
) -> None:
    """Even without route hooks, LoopbackValidatingProxy blocks literal IPv4/IPv6/WS."""
    ws_srv = RecordedWSServer()
    await ws_srv.start()
    try:
        async with LoopbackValidatingProxy(resolver=_resolver) as proxy:
            policy = BrowserEgressPolicy(proxy=proxy, resolver=_resolver)
            async with pw_async.async_playwright() as p:
                browser = await p.chromium.launch(**policy.build_launch_options(headless=True))
                ctx = await browser.new_context(**policy.build_context_options())
                page = await ctx.new_page()
                for target in (
                    f"http://127.0.0.1:{victim_http.port}/secret",
                    f"http://[::1]:{victim_http.port}/secret",
                    "http://169.254.169.254/latest/meta-data/",
                    "http://localhost:8080/admin",
                ):
                    resp = await page.goto(target)
                    assert resp is not None and resp.status == 403
                ws_res = await page.evaluate(
                    f"""() => new Promise(r => {{
                      const w = new WebSocket('ws://127.0.0.1:{ws_srv.port}/ws');
                      w.onopen = () => r('OPEN');
                      w.onerror = () => r('BLOCKED');
                    }})"""
                )
                await browser.close()
    finally:
        await ws_srv.stop()

    assert ws_res == "BLOCKED"
    assert victim_http.hits == [] and ws_srv.hits == []


async def test_chromium_controlled_post_requires_single_use_oob_approval(
    tmp_path: Path, public_sim: Any
) -> None:
    """Real Chromium POST enforces canonical headers, session, digest, single-use, no redirect."""
    broker = ApprovalBroker(tmp_path / "browser_apr.sqlite3", clock=lambda: 2000.0)
    factory = make_public_sim_socket_factory(public_sim.port)
    target = "http://public.example.test/submit"
    redir_target = "http://public.example.test/submit-redir"
    body = b'{"amount":10}'
    bound_hdrs = {
        "Content-Type": "application/json",
        "Origin": "http://public.example.test",
        "Referer": "http://public.example.test/page",
    }
    js_post = (
        "async ([u, b, ct]) => { try { "
        "const r = await fetch(u, {method:'POST', headers:{'Content-Type':ct}, body:b}); "
        "return r.status; } catch { return 'BLK'; } }"
    )

    async with LoopbackValidatingProxy(resolver=_resolver, socket_factory=factory) as proxy:
        policy = BrowserEgressPolicy(
            approval_broker=broker,
            proxy=proxy,
            session_id="sess-chrome-1",
            resolver=_resolver,
            socket_factory=factory,
        )
        async with pw_async.async_playwright() as p:
            browser = await p.chromium.launch(**policy.build_launch_options(headless=True))
            ctx = await browser.new_context(**policy.build_context_options())
            await policy.attach_to_context(ctx)
            page = await ctx.new_page()
            await page.goto("http://public.example.test/page")

            res0 = await page.evaluate(js_post, [target, '{"amount":10}', "application/json"])
            assert res0 == "BLK"

            rec = prepare_browser_mutation_approval(
                broker,
                method="POST",
                url=target,
                body=body,
                headers=bound_hdrs,
                session_id="sess-chrome-1",
                ttl_seconds=60,
            )
            broker.decide_out_of_band(rec.approval_id, approve=True)
            policy.authorize_mutation(rec.approval_id)

            # Tampered Content-Type and tampered body rejected
            r_ct = await page.evaluate(js_post, [target, '{"amount":10}', "text/plain"])
            assert r_ct == "BLK"
            r_bd = await page.evaluate(js_post, [target, '{"amount":999}', "application/json"])
            assert r_bd == "BLK"
            # Exact canonical match succeeds once
            r_ok = await page.evaluate(js_post, [target, '{"amount":10}', "application/json"])
            assert r_ok == 200
            # Replay rejected
            policy.authorize_mutation(rec.approval_id)
            r_rep = await page.evaluate(js_post, [target, '{"amount":10}', "application/json"])
            assert r_rep == "BLK"

            # Redirect never follows or reuses approval
            rec_red = prepare_browser_mutation_approval(
                broker,
                method="POST",
                url=redir_target,
                body=body,
                headers=bound_hdrs,
                session_id="sess-chrome-1",
                ttl_seconds=60,
            )
            broker.decide_out_of_band(rec_red.approval_id, approve=True)
            policy.authorize_mutation(rec_red.approval_id)
            res_red = await page.evaluate(
                js_post, [redir_target, '{"amount":10}', "application/json"]
            )
            assert res_red == "BLK"
            await browser.close()

    post_hits = [h for h in public_sim.hits if h[0] == "POST" and h[1] == "/submit"]
    assert post_hits == [("POST", "/submit", body)]


async def test_chromium_blocks_webrtc_udp_and_tcp_stun_turn_across_ipv4_and_ipv6() -> None:
    """Verify WebRTC STUN/TURN UDP & TCP ICE across IPv4/IPv6 never reach local listeners."""
    u4, u6 = RecordedUDPListener(), RecordedUDPListener()
    t4, t6 = RecordedTCPListener(), RecordedTCPListener()
    await u4.start("127.0.0.1")
    await u6.start("::1")
    await t4.start("127.0.0.1")
    await t6.start("::1")
    ice_urls = [
        f"stun:127.0.0.1:{u4.port}",
        f"stun:[::1]:{u6.port}",
        f"turn:127.0.0.1:{u4.port}?transport=udp",
        f"turn:127.0.0.1:{t4.port}?transport=tcp",
        f"turn:[::1]:{t6.port}?transport=tcp",
    ]
    js_ice = """async (urls) => {
      try {
        const pc = new RTCPeerConnection({
          iceServers: urls.map(u => ({urls: u, username: 'u', credential: 'p'}))
        });
        pc.createDataChannel('d');
        await pc.setLocalDescription(await pc.createOffer());
        await new Promise(r => setTimeout(r, 400));
        return 'STARTED';
      } catch (e) { return 'BLOCKED:' + e.name; }
    }"""
    try:
        async with LoopbackValidatingProxy(resolver=_resolver) as proxy:
            async with pw_async.async_playwright() as p:
                for disable_js in (True, False):
                    pol = BrowserEgressPolicy(
                        proxy=proxy, disable_webrtc=disable_js, resolver=_resolver
                    )
                    browser = await p.chromium.launch(**pol.build_launch_options(headless=True))
                    ctx = await browser.new_context(**pol.build_context_options())
                    await pol.attach_to_context(ctx)
                    page = await ctx.new_page()
                    outcome = await page.evaluate(js_ice, ice_urls)
                    if disable_js:
                        assert outcome.startswith("BLOCKED:")
                    await browser.close()
    finally:
        u4.stop()
        u6.stop()
        await t4.stop()
        await t6.stop()

    assert u4.packets == [] and u6.packets == []
    assert t4.connections == [] and t6.connections == []
    assert any(e.method == "CONNECT" and e.status_code == 403 for e in proxy.audit_log)
