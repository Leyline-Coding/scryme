"""LAN sharing: state persistence, the pure access decision, QR, and the /lan page."""

import pytest
from src import lan


def test_state_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(lan, "_path", lambda settings=None: tmp_path / "lan.json")
    assert lan.lan_state() == {"enabled": False, "code": ""}
    lan.save_state(True, "abc123")
    assert lan.lan_state() == {"enabled": True, "code": "abc123"}


def test_is_loopback():
    assert lan.is_loopback("127.0.0.1")
    assert lan.is_loopback("::1")
    assert not lan.is_loopback("192.168.1.20")
    assert not lan.is_loopback(None)


def test_share_url_and_code():
    code = lan.make_code()
    assert len(code) == 6
    url = lan.share_url(8765, code)
    assert url.endswith(f":8765/?code={code}")
    assert lan.share_url(8765).endswith(":8765/")


def test_qr_svg_is_inline_svg():
    svg = lan.qr_svg("http://192.168.1.5:8765/")
    assert svg.lstrip().startswith("<?xml") and "<svg" in svg


@pytest.mark.parametrize(
    "host,path,state,cookie,query,expected",
    [
        ("127.0.0.1", "/", {"enabled": False, "code": ""}, None, None, "allow"),
        ("10.0.0.5", "/static/app.css", {"enabled": False, "code": ""}, None, None, "allow"),
        ("10.0.0.5", "/", {"enabled": False, "code": ""}, None, None, "deny"),
        ("10.0.0.5", "/", {"enabled": True, "code": ""}, None, None, "allow"),
        ("10.0.0.5", "/", {"enabled": True, "code": "x"}, None, None, "unlock"),
        ("10.0.0.5", "/", {"enabled": True, "code": "x"}, "x", None, "allow"),
        ("10.0.0.5", "/", {"enabled": True, "code": "x"}, None, "x", "set_cookie"),
        ("10.0.0.5", "/", {"enabled": True, "code": "x"}, None, "bad", "unlock"),
    ],
)
def test_access_decision(host, path, state, cookie, query, expected):
    assert lan.access_decision(
        host=host, path=path, state=state, cookie_code=cookie, query_code=query
    ) == expected


@pytest.mark.asyncio
async def test_lan_page_renders(client):
    """Without the guard (default), the page explains LAN sharing is desktop-only."""
    resp = await client.get("/lan")
    assert resp.status_code == 200
    assert "desktop app" in resp.text
