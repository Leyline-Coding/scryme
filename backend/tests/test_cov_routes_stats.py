"""Coverage for src/routes/stats.py — the legacy /stats redirect."""

import pytest
import src.routes.stats as stats_mod


@pytest.mark.asyncio
async def test_stats_redirect():
    resp = await stats_mod.stats()
    assert resp.status_code == 307
    assert resp.headers["location"] == "/collection?tab=stats"
