"""Unified settings page (#202): the page renders both tabs, and the gear is a link (no popup)."""

import pytest


@pytest.mark.asyncio
async def test_settings_page_renders_both_tabs(client):
    body = (await client.get("/settings")).text
    # Collection tab: the shared preference controls live here now.
    assert "function settingsMenu" in body and "setPalette" in body
    assert "tab === 'collection'" in body and "tab === 'instance'" in body
    # Instance tab: read-only config + entry points; bools render as on/off (not True/False).
    assert "Operator configuration" in body and "SCRYME_" in body
    assert "Database" in body and "Scryfall" in body
    assert "Read-only mode" in body and ">False<" not in body and ">True<" not in body
    # The gear is hidden on /settings itself (it would just link to this page).
    assert 'href="/settings" title="Settings"' not in body


@pytest.mark.asyncio
async def test_gear_links_to_settings_no_popup(client):
    # The gear is included on every page via base.html; it must be a plain link to /settings with
    # no popup component (settingsMenu lives only on /settings now).
    body = (await client.get("/")).text
    assert 'href="/settings" title="Settings"' in body
    assert "settingsMenu()" not in body and 'x-show="open"' not in body
