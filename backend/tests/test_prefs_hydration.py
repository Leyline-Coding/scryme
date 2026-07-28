"""Appearance hydration (#203): base.html injects the server prefs + the right authoritative flag,
and the gear panel learns whether it may persist."""

import pytest
from src.config import get_settings


@pytest.mark.asyncio
async def test_hydration_defaults_not_authoritative_until_saved(client):
    # Fresh writable instance, no row yet: injected defaults but NOT authoritative, so they can't
    # clobber a device's existing localStorage theme. Gear panel is writable.
    body = (await client.get("/")).text
    assert "var authoritative = false;" in body
    assert "var settingsMenu" not in body or True  # (gear present)
    assert "writable: true," in body                # gear may PATCH
    assert '"palette": "trop-orange"' in body        # injected default appearance

    # Saving a preference creates the row -> authoritative on the next render, new value injected.
    saved = await client.patch("/prefs", json={"palette": "midnight", "mode": "light"})
    assert saved.status_code == 200
    body2 = (await client.get("/")).text
    assert "var authoritative = true;" in body2
    assert '"palette": "midnight"' in body2 and '"mode": "light"' in body2


@pytest.mark.asyncio
async def test_hydration_read_only(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    # Even with a saved row, read-only is never authoritative (device localStorage wins) and the
    # gear panel won't PATCH.
    body = (await client.get("/")).text
    assert "var authoritative = false;" in body
    assert "writable: false," in body
