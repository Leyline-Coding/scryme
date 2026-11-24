"""The request boundary (src/security.py): host, origin, login, read-only, rate limit, headers.

Each test here reproduces something that worked before the boundary existed and should not have.
"""

import pytest
from src import security
from src.config import get_settings

pytestmark = pytest.mark.asyncio


# --- cross-site requests (CSRF) -------------------------------------------------------------------

async def test_cross_site_post_is_refused(client):
    # Another site's form posting to a local instance: this used to create the saved search.
    resp = await client.post(
        "/saved", data={"name": "planted", "query": "t:goblin"},
        headers={"Origin": "https://evil.example", "Sec-Fetch-Site": "cross-site"},
    )
    assert resp.status_code == 403
    assert resp.json()["detail"] == "Cross-site request refused."


async def test_foreign_origin_without_fetch_metadata_is_refused(client):
    resp = await client.post("/saved", data={"name": "x", "query": "t:goblin"},
                             headers={"Origin": "https://evil.example"})
    assert resp.status_code == 403


async def test_null_origin_is_refused(client):
    resp = await client.post("/saved", data={"name": "x", "query": "t:goblin"},
                             headers={"Origin": "null"})
    assert resp.status_code == 403


async def test_same_origin_post_is_allowed(client):
    resp = await client.post(
        "/saved", data={"name": "mine", "query": "t:goblin"},
        headers={"Origin": "http://test", "Sec-Fetch-Site": "same-origin"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


async def test_matching_origin_behind_a_proxy_is_allowed(client):
    # nginx forwards ``Host`` without the port; the browser's Origin still carries it.
    resp = await client.post(
        "/saved", data={"name": "mine", "query": "t:goblin"},
        headers={"Host": "scryme.example", "Origin": "https://scryme.example:8443"},
        follow_redirects=False,
    )
    assert resp.status_code == 303


async def test_non_browser_client_without_origin_is_allowed(client):
    # curl, the scanner app: no Origin, no fetch metadata.
    resp = await client.post("/saved", data={"name": "mine", "query": "t:goblin"},
                             follow_redirects=False)
    assert resp.status_code == 303


async def test_same_site_sibling_is_refused(client):
    resp = await client.post(
        "/saved", data={"name": "x", "query": "t:goblin"},
        headers={"Host": "scryme.example", "Origin": "https://other.example",
                 "Sec-Fetch-Site": "same-site"},
    )
    assert resp.status_code == 403


async def test_cross_site_get_is_not_blocked(client):
    resp = await client.get("/health", headers={"Sec-Fetch-Site": "cross-site",
                                                "Origin": "https://evil.example"})
    assert resp.status_code == 200


# --- host header (DNS rebinding) ------------------------------------------------------------------

async def test_any_host_is_accepted_by_default(client):
    assert (await client.get("/health", headers={"Host": "anything.example"})).status_code == 200


async def test_unlisted_host_is_refused_when_hosts_are_configured(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "allowed_hosts", "scryme.example, *.home.lan")
    assert (await client.get("/health", headers={"Host": "evil.example"})).status_code == 400
    for host in ("scryme.example", "scryme.example:8080", "box.home.lan", "127.0.0.1:8000",
                 "localhost", "[::1]:8000", "192.168.1.20", "nas.local"):
        resp = await client.get("/health", headers={"Host": host})
        assert resp.status_code == 200, host


async def test_desktop_build_refuses_foreign_hostnames(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "lan_guard", True)
    assert not security.host_allowed("rebind.evil.example:8765", settings)
    assert security.host_allowed("127.0.0.1:8765", settings)
    assert security.host_allowed("192.168.1.20:8765", settings)


# --- login when a token is set --------------------------------------------------------------------

@pytest.fixture
def token(monkeypatch):
    monkeypatch.setattr(get_settings(), "api_token", "s3cret-token")
    return "s3cret-token"


async def test_token_gates_the_web_ui(client, token):
    # These all answered without a token while /api/v1 returned 401.
    for path in ("/export?fmt=csv", "/prefs", "/metrics", "/admin/status", "/settings",
                 "/docs", "/openapi.json"):
        resp = await client.get(path)
        assert resp.status_code == 401, path
    resp = await client.post("/backup/download")
    assert resp.status_code == 401
    resp = await client.post("/settings/devices", data={"label": "x", "scope": "write"})
    assert resp.status_code == 401


async def test_browser_navigation_is_sent_to_login(client, token):
    resp = await client.get("/collection?view=grid", headers={"Accept": "text/html"},
                            follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login?next=%2Fcollection%3Fview%3Dgrid"


async def test_htmx_request_is_told_to_redirect(client, token):
    resp = await client.get("/collection", headers={"HX-Request": "true"})
    assert resp.status_code == 401
    assert resp.headers["HX-Redirect"] == "/login"


async def test_open_paths_stay_open_with_a_token(client, token):
    assert (await client.get("/health")).status_code == 200
    assert (await client.get("/static/app.css")).status_code == 200
    assert (await client.get("/login")).status_code == 200
    # An unknown share token is a 404 from the share route, not a login wall.
    assert (await client.get("/share/not-a-real-token")).status_code == 404


async def test_login_sets_a_session_that_unlocks_the_ui(client, token):
    bad = await client.post("/login", data={"token": "wrong", "next": "/"})
    assert bad.status_code == 401
    assert security.SESSION_COOKIE not in bad.cookies

    good = await client.post("/login", data={"token": token, "next": "/settings"},
                             follow_redirects=False)
    assert good.status_code == 303
    assert good.headers["location"] == "/settings"
    cookie = good.headers["set-cookie"]
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie

    session = good.cookies[security.SESSION_COOKIE]
    resp = await client.get("/admin/status", cookies={security.SESSION_COOKIE: session})
    assert resp.status_code == 200


async def test_login_never_redirects_off_site(client, token):
    resp = await client.post("/login", data={"token": token, "next": "//evil.example/x"},
                             follow_redirects=False)
    # The host is dropped; only a path on this instance survives.
    assert resp.headers["location"] == "/x"


async def test_bearer_token_unlocks_ui_routes(client, token):
    resp = await client.get("/admin/status", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200


async def test_session_cookie_dies_with_the_token(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "api_token", "first")
    old = security.session_value("first")
    monkeypatch.setattr(settings, "api_token", "second")
    resp = await client.get("/admin/status", cookies={security.SESSION_COOKIE: old})
    assert resp.status_code == 401


async def test_a_device_token_does_not_unlock_the_ui(client, session, token):
    from src.client_tokens import issue_token

    _, secret = await issue_token(session, label="scanner", scope="write")
    resp = await client.get("/admin/status", headers={"Authorization": f"Bearer {secret}"})
    assert resp.status_code == 401


async def test_ui_is_open_without_a_token(client):
    assert (await client.get("/admin/status")).status_code == 200
    resp = await client.get("/login", follow_redirects=False)
    assert resp.status_code == 303 and resp.headers["location"] == "/"


# --- read-only ------------------------------------------------------------------------------------

@pytest.fixture
def read_only(monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)


@pytest.mark.parametrize("method,path", [
    ("post", "/ai/test"),
    ("post", "/decks/1/upgrade"),
    ("post", "/decks/1/ai/chat"),
    ("post", "/decks/1/ai/chat/clear"),
    ("post", "/card/00000000-0000-0000-0000-000000000000/ask"),
    ("post", "/backup/download"),
    ("post", "/a/route/that/does/not/exist/yet"),
    ("delete", "/anything"),
    ("patch", "/prefs"),
    ("put", "/anything"),
])
async def test_read_only_refuses_every_write_method(client, read_only, method, path):
    resp = await getattr(client, method)(path)
    assert resp.status_code == 403, path
    assert resp.json()["detail"] == security.READ_ONLY_DETAIL


async def test_read_only_still_serves_reads_and_the_fx_cache(client, read_only):
    assert (await client.get("/health")).status_code == 200
    resp = await client.post("/card/fx-history", data={"code": "usd"})
    assert resp.status_code == 200


async def test_read_only_disables_ai_even_when_configured(client, session, monkeypatch):
    from src import llm

    await llm.save_config(session, base_url="http://127.0.0.1:1/v1", api_key="k",
                          chat_model="m", embed_model="e", enabled=True)
    assert (await llm.get_config(session)).ready
    monkeypatch.setattr(get_settings(), "read_only", True)
    cfg = await llm.get_config(session)
    assert not cfg.ready and cfg.api_key == "" and cfg.base_url == ""


async def test_read_only_open_saved_search_does_not_clear_alerts(client, session, monkeypatch):
    from src import saved_alerts
    from src.models import SavedSearch

    saved = SavedSearch(name="s", query="t:goblin")
    session.add(saved)
    await session.commit()
    calls = []

    async def fake_clear(_session, saved_id):
        calls.append(saved_id)

    monkeypatch.setattr(saved_alerts, "clear_new", fake_clear)
    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.get(f"/saved/{saved.id}/open", follow_redirects=False)
    assert resp.status_code == 303
    assert calls == []


# --- rate limit -----------------------------------------------------------------------------------

async def test_limiter_counts_per_client_and_resets_each_window():
    limiter = security.RateLimiter()
    assert [limiter.check("a", "b", 2, now=0.0) for _ in range(2)] == [0, 0]
    assert limiter.check("a", "b", 2, now=1.0) == 59
    assert limiter.check("other", "b", 2, now=1.0) == 0
    assert limiter.check("a", "b", 2, now=61.0) == 0
    assert limiter.check("a", "b", 0, now=61.0) == 0  # 0 disables


async def test_application_routes_are_rate_limited(client, monkeypatch):
    # The previous limiter could not see routes added with include_router, so only /docs and
    # /openapi.json were ever limited.
    settings = get_settings()
    monkeypatch.setattr(security, "_limits_active", lambda _s: True)
    monkeypatch.setattr(settings, "rate_limit_default", 5)
    monkeypatch.setattr(settings, "client_ip_header", "X-Test-Client")
    security.limiter.reset()
    try:
        codes = [(await client.get("/health", headers={"X-Test-Client": "203.0.113.9"}))
                 .status_code for _ in range(7)]
        assert codes == [200] * 5 + [429] * 2
        other = await client.get("/health", headers={"X-Test-Client": "203.0.113.10"})
        assert other.status_code == 200
        limited = await client.get("/health", headers={"X-Test-Client": "203.0.113.9"})
        assert int(limited.headers["Retry-After"]) >= 1
        # Static assets are not counted.
        asset = await client.get("/static/app.css", headers={"X-Test-Client": "203.0.113.9"})
        assert asset.status_code == 200
    finally:
        security.limiter.reset()


async def test_login_and_heavy_routes_have_their_own_limits(client, token, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(security, "_limits_active", lambda _s: True)
    monkeypatch.setattr(settings, "rate_limit_login", 3)
    monkeypatch.setattr(settings, "client_ip_header", "X-Test-Client")
    security.limiter.reset()
    try:
        codes = [(await client.post("/login", data={"token": "guess"},
                                    headers={"X-Test-Client": "203.0.113.9"})).status_code
                 for _ in range(5)]
        assert codes == [401] * 3 + [429] * 2
    finally:
        security.limiter.reset()


async def test_loopback_clients_are_not_limited(monkeypatch):
    from starlette.requests import Request

    settings = get_settings()
    monkeypatch.setattr(security, "_limits_active", lambda _s: True)
    monkeypatch.setattr(settings, "rate_limit_default", 1)
    request = Request({"type": "http", "method": "GET", "path": "/health", "headers": [],
                       "query_string": b"", "client": ("127.0.0.1", 5000)})
    assert [security.rate_limited(request, settings) for _ in range(3)] == [0, 0, 0]


async def test_heavy_bucket_covers_expensive_routes():
    from starlette.requests import Request

    def bucket(method, path):
        request = Request({"type": "http", "method": method, "path": path, "headers": [],
                           "query_string": b""})
        chosen = security.bucket_for(request)
        return chosen[0] if chosen else None

    for path in ("/backup/download", "/backup/restore", "/export", "/ai/test",
                 "/decks/3/ai/chat", "/decks/3/upgrade", "/api/v1/scan", "/upload"):
        assert bucket("POST", path) == "heavy", path
    assert bucket("GET", "/search") == "default"
    assert bucket("POST", "/login") == "login"
    assert bucket("GET", "/images/a.jpg") is None


# --- headers --------------------------------------------------------------------------------------

async def test_security_headers_on_pages_and_on_refusals(client):
    for resp in (await client.get("/health"),
                 await client.post("/saved", headers={"Origin": "https://evil.example"})):
        assert resp.headers["X-Content-Type-Options"] == "nosniff"
        assert resp.headers["X-Frame-Options"] == "DENY"
        assert resp.headers["Referrer-Policy"] == "same-origin"
        csp = resp.headers["Content-Security-Policy"]
        assert "script-src 'self' 'unsafe-inline' 'unsafe-eval'" in csp
        assert "frame-ancestors 'none'" in csp and "object-src 'none'" in csp


async def test_no_page_loads_script_or_styles_from_a_third_party(client):
    html = (await client.get("/")).text
    import re

    external = re.findall(r"<(?:script|link)[^>]+(?:src|href)=[\"']\s*https?://[^\"']+", html)
    assert external == []
    for asset in re.findall(r"(?:src|href)=\"(/static/vendor/[^\"]+)\"", html):
        assert (await client.get(asset)).status_code == 200, asset


async def test_lan_cookie_is_http_only():
    import inspect

    from src import main

    assert "httponly=True" in inspect.getsource(main._install_lan_guard)
