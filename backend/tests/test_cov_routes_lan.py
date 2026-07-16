"""Coverage for src/routes/lan.py — loopback-guarded toggle/code endpoints."""

import pytest
from src import lan


@pytest.fixture(autouse=True)
def _isolate_lan_state(tmp_path, monkeypatch):
    # Keep LAN state in a temp file so these tests don't touch the real data dir.
    monkeypatch.setattr(lan, "_path", lambda settings=None: tmp_path / "lan.json")


@pytest.mark.asyncio
async def test_toggle_requires_loopback(client, monkeypatch):
    monkeypatch.setattr(lan, "is_loopback", lambda host: False)
    resp = await client.post("/lan/toggle", follow_redirects=False)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_toggle_flips_state(client, monkeypatch):
    monkeypatch.setattr(lan, "is_loopback", lambda host: True)
    assert lan.lan_state()["enabled"] is False
    resp = await client.post("/lan/toggle", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/lan"
    assert lan.lan_state()["enabled"] is True


@pytest.mark.asyncio
async def test_code_set_and_clear(client, monkeypatch):
    monkeypatch.setattr(lan, "is_loopback", lambda host: True)
    set_resp = await client.post("/lan/code", data={"action": "set"}, follow_redirects=False)
    assert set_resp.status_code == 303
    assert len(lan.lan_state()["code"]) == 6  # make_code() token_hex(3)

    clear_resp = await client.post("/lan/code", data={"action": "clear"}, follow_redirects=False)
    assert clear_resp.status_code == 303
    assert lan.lan_state()["code"] == ""


@pytest.mark.asyncio
async def test_code_requires_loopback(client, monkeypatch):
    monkeypatch.setattr(lan, "is_loopback", lambda host: False)
    resp = await client.post("/lan/code", data={"action": "set"}, follow_redirects=False)
    assert resp.status_code == 403
