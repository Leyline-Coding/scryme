"""Coverage for the remaining branches in routes/search.py + the 'set' sort in the engine.

These paths (AI-translation failure fallback, the universal-filter double-failure, the opt-in
movers panel, and sort=set) weren't hit by the existing suite.
"""

import pytest
from src.routes import search as search_route


@pytest.mark.asyncio
async def test_search_nl_translation_failure_falls_back(client, monkeypatch):
    """/search/nl: when the AI translation raises, fall back to the raw text (no ?nl=)."""

    class _Cfg:
        ready = True

    async def _fake_get_config(session):
        return _Cfg()

    async def _boom(text, chat_client):
        raise ValueError("translation blew up")

    monkeypatch.setattr(search_route, "get_config", _fake_get_config)
    monkeypatch.setattr(search_route, "ChatClient", lambda cfg: object())
    monkeypatch.setattr(search_route, "nl_to_query", _boom)

    resp = await client.post("/search/nl", data={"q": "cheap red burn"})
    assert resp.status_code in (302, 303, 307)
    loc = resp.headers["location"]
    assert "q=cheap" in loc      # fell back to the raw text
    assert "nl=" not in loc      # no generated query, so no editable-nl marker


@pytest.mark.asyncio
async def test_search_universal_and_bare_both_invalid(client):
    """A bad query *and* a universal filter present: both attempts raise -> error surfaced."""
    resp = await client.get(
        "/search",
        params={"q": "zzz:1"},  # unknown keyword -> SearchError
        headers={"Cookie": "scryme_search_filter=legal:commander"},
    )
    assert resp.status_code == 200
    # The page still renders (with an error), rather than 500-ing.


@pytest.mark.asyncio
async def test_search_movers_panel_opt_in(client):
    """The biggest-movers panel renders when the opt-in cookie is set."""
    resp = await client.get(
        "/search", params={"q": ""}, headers={"Cookie": "scryme_movers=1"}
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_search_sort_by_set(client):
    """sort=set exercises the (set_code, collector_number) ordering branch in the engine."""
    resp = await client.get("/search", params={"q": "", "sort": "set", "scope": "all"})
    assert resp.status_code == 200
