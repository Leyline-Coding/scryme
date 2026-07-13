"""LLM foundation (#163): config encryption/resolution, connection test, grounded deck features."""

import uuid

import httpx
import pytest
from src.decks import create_deck
from src.llm import (
    LLMConfig,
    analyze_deck,
    get_config,
    save_config,
    suggest_from_collection,
)
from src.llm import test_connection as check_connection
from src.models import Card, CollectionCard, LLMSettings
from src.scryfall.mapping import card_to_columns


class FakeChat:
    """Stands in for ChatClient: returns a canned reply, or raises."""

    def __init__(self, reply="ok", raise_exc=None):
        self.reply = reply
        self.raise_exc = raise_exc

    async def chat(self, messages, **kwargs):
        if self.raise_exc:
            raise self.raise_exc
        return self.reply


async def _own(session, name, identity=("R",)):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
         "collector_number": str(abs(hash(name)) % 999), "type_line": "Instant",
         "color_identity": list(identity), "oracle_text": f"{name} does something."}
    ))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_config_save_encrypts_and_keeps_key(session):
    await save_config(session, base_url="http://x/v1", api_key="secret-key",
                      chat_model="m", embed_model="e", enabled=True)
    cfg = await get_config(session)
    assert cfg.base_url == "http://x/v1" and cfg.api_key == "secret-key" and cfg.ready
    row = await session.get(LLMSettings, 1)
    assert row.api_key_enc and "secret-key" not in row.api_key_enc  # encrypted at rest

    # A blank api_key on re-save keeps the existing key; enabled=False -> not ready.
    await save_config(session, base_url="http://y/v1", api_key="",
                      chat_model="m", embed_model="e", enabled=False)
    cfg2 = await get_config(session)
    assert cfg2.api_key == "secret-key" and cfg2.base_url == "http://y/v1" and not cfg2.ready


@pytest.mark.asyncio
async def test_config_env_fallback(session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "llm_base_url", "http://env/v1")
    cfg = await get_config(session)  # no DB row -> env
    assert cfg.base_url == "http://env/v1" and cfg.ready


@pytest.mark.asyncio
async def test_connection_reports_ok_and_failure(session):
    cfg = LLMConfig(base_url="http://x/v1", chat_model="m", enabled=True)
    ok, _ = await check_connection(cfg, client=FakeChat())
    assert ok
    bad, msg = await check_connection(cfg, client=FakeChat(raise_exc=httpx.ConnectError("no")))
    assert not bad and "reach" in msg.lower()
    off, _ = await check_connection(LLMConfig())
    assert not off


@pytest.mark.asyncio
async def test_analyze_deck_grounds_and_returns_text(session):
    await _own(session, "Lightning Bolt")
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    text = await analyze_deck(session, deck, FakeChat(reply="This is an aggressive red deck."))
    assert "aggressive" in text


@pytest.mark.asyncio
async def test_suggest_only_returns_owned_candidates(session):
    await _own(session, "Lightning Bolt")
    await _own(session, "Shock")
    await _own(session, "Fireball")
    await _own(session, "Counterspell", identity=("U",))  # out of identity -> excluded
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    reply = "Shock — cheap removal\nDefinitely Not A Real Card — nope\nFireball — finisher"
    result = await suggest_from_collection(session, deck, FakeChat(reply=reply))
    names = {s.name for s in result.suggestions}
    assert names == {"Shock", "Fireball"}          # hallucination + off-color dropped
    assert result.considered == 2                   # Shock, Fireball (not the deck's Bolt / blue)


@pytest.mark.asyncio
async def test_ai_settings_route_and_analyze(client, session, monkeypatch):
    # Save config (enabled) and confirm the page renders it.
    save = await client.post("/ai", data={"base_url": "http://x/v1", "api_key": "k",
                                           "chat_model": "m", "embed_model": "e", "enabled": "1"})
    assert save.status_code in (200, 303)
    page = await client.get("/ai")
    assert "AI settings" in page.text and "(set)" in page.text

    await _own(session, "Lightning Bolt")
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    monkeypatch.setattr("src.routes.ai.ChatClient", lambda cfg: FakeChat(reply="Aggro."))
    ok = await client.post(f"/decks/{deck.id}/analyze")
    assert ok.status_code == 200 and "Aggro." in ok.text


@pytest.mark.asyncio
async def test_ai_feature_hidden_when_not_configured(client, session):
    deck = await create_deck(session, "Empty", "1 Nonexistent")
    resp = await client.post(f"/decks/{deck.id}/analyze")
    assert resp.status_code == 200 and "configured" in resp.text  # apostrophe is HTML-escaped


class SeqChat:
    """Returns a scripted sequence of replies across successive .chat() calls."""

    def __init__(self, replies):
        self.replies = list(replies)
        self.i = 0

    async def chat(self, messages, **kwargs):
        reply = self.replies[min(self.i, len(self.replies) - 1)]
        self.i += 1
        return reply


@pytest.mark.asyncio
async def test_nl_to_query_validates_and_retries():
    from src.llm import nl_to_query, validate_query
    assert validate_query("c:r mv<=2") and not validate_query("bogusfield:x")
    # Valid on the first try.
    assert await nl_to_query("cheap red removal", SeqChat(["c:r t:instant mv<=2"])) \
        == "c:r t:instant mv<=2"
    # Invalid then corrected on retry.
    assert await nl_to_query("blue fliers", SeqChat(["notafilter:zzz", "c:u o:flying"])) \
        == "c:u o:flying"
    # Never valid -> empty (caller falls back).
    assert await nl_to_query("gibberish", SeqChat(["notafilter:zzz"])) == ""


@pytest.mark.asyncio
async def test_search_nl_route_translates_and_falls_back(client, session, monkeypatch):
    async def fake_nl(text, cl):
        return "c:r"
    monkeypatch.setattr("src.routes.search.nl_to_query", fake_nl)

    # Not configured -> fall back to the raw text as the query.
    off = await client.post("/search/nl", data={"q": "lightning bolt", "scope": "collection"},
                            follow_redirects=False)
    assert off.status_code == 303 and "q=lightning" in off.headers["location"].lower()

    # Configured -> translated query + nl passthrough.
    await save_config(session, base_url="http://x/v1", api_key="k", chat_model="m",
                      embed_model="e", enabled=True)
    on = await client.post("/search/nl", data={"q": "red cards", "scope": "all"},
                           follow_redirects=False)
    loc = on.headers["location"]
    assert on.status_code == 303 and "q=c%3Ar" in loc and "scope=all" in loc and "nl=" in loc


@pytest.mark.asyncio
async def test_api_search_nl(client, session, monkeypatch):
    off = await client.get("/api/v1/search/nl?q=dragons")
    assert off.json() == {"query": "", "ok": False}

    async def fake_nl(text, cl):
        return "t:dragon"
    monkeypatch.setattr("src.routes.api.nl_to_query", fake_nl)
    await save_config(session, base_url="http://x/v1", api_key="k", chat_model="m",
                      embed_model="e", enabled=True)
    on = await client.get("/api/v1/search/nl?q=dragons")
    assert on.status_code == 200 and on.json() == {"query": "t:dragon", "ok": True}
