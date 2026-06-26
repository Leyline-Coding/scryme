"""Advanced-search page test: the form renders with its builder and key controls."""

import pytest


@pytest.mark.asyncio
async def test_advanced_page_renders(client):
    resp = await client.get("/advanced")
    assert resp.status_code == 200
    body = resp.text
    assert "<html" in body  # full page
    assert "advancedSearch()" in body  # the Alpine query-builder component
    assert "Generated query" in body  # the live preview
    # A few representative controls map to engine filters.
    for marker in ("Oracle text contains", "Color identity", "Mana value", "Legal in format"):
        assert marker in body
