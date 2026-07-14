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


async def _seed(session, name, ci=("R",), usd="1.00", owned=True, legal=True,
                type_line="Creature"):
    legalities = {"commander": "legal" if legal else "not_legal"}
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
         "collector_number": str(abs(hash(name)) % 9999), "type_line": type_line,
         "color_identity": list(ci), "prices": {"usd": usd}, "legalities": legalities}
    ))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=4))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_build_from_prompt_uses_only_owned_in_color(session):
    from src.llm import build_from_prompt
    await _seed(session, "Lightning Bolt", ci=("R",), usd="5.00", type_line="Instant")
    await _seed(session, "Shock", ci=("R",), usd="0.50", type_line="Instant")
    await _seed(session, "Mountain", ci=(), usd="0.10", type_line="Basic Land")
    await _seed(session, "Counterspell", ci=("U",), usd="1.00")  # blue -> excluded by "red"
    reply = "4 Lightning Bolt\n4 Shock\n20 Mountain\n3 Counterspell\n1 Totally Fake Card"
    built = await build_from_prompt(session, "a red aggro deck", FakeChat(reply=reply))
    names = {ln.name for ln in built.lines}
    assert names == {"Lightning Bolt", "Shock", "Mountain"}   # blue + hallucination dropped
    assert built.considered == 3
    assert built.total_price == round(4 * 5.0 + 4 * 0.5 + 20 * 0.1, 2)


@pytest.mark.asyncio
async def test_find_commanders_ranks_by_owned_depth(session):
    from src.llm import find_commanders
    await _seed(session, "Kaalia", ci=("W", "B", "R"), type_line="Legendary Creature — Angel")
    await _seed(session, "Talrand", ci=("U",), type_line="Legendary Creature — Wizard")
    await _seed(session, "Mardu One", ci=("R", "W"))
    await _seed(session, "Mardu Two", ci=("B",))
    await _seed(session, "Blue Thing", ci=("U",))
    picks = await find_commanders(session, client=None)
    assert picks[0].name == "Kaalia"
    kaalia = next(p for p in picks if p.name == "Kaalia")
    talrand = next(p for p in picks if p.name == "Talrand")
    assert kaalia.owned_depth > talrand.owned_depth
    # With a client, pitches are attached.
    picks2 = await find_commanders(session, client=FakeChat(reply="Kaalia - aggressive angels"))
    assert next(p for p in picks2 if p.name == "Kaalia").pitch == "aggressive angels"


@pytest.mark.asyncio
async def test_plan_upgrades_validates_price_and_budget(session):
    from src.llm import plan_upgrades
    await _seed(session, "In Deck Card", ci=("R",), type_line="Instant")
    deck = await create_deck(session, "D", "1 In Deck Card")
    await _seed(session, "Sol Ring", ci=(), usd="2.00", owned=False, type_line="Artifact")
    await _seed(session, "Rhystic Study", ci=("U",), usd="30.00", owned=False)
    await _seed(session, "Owned Extra", ci=("R",), usd="1.00", owned=True)
    reply = ("Sol Ring - ramp\nRhystic Study - card draw\n"
             "Owned Extra - you already own this\nMade Up Card - not real")
    plan = await plan_upgrades(session, deck, budget=10.0, client=FakeChat(reply=reply))
    names = [it.name for it in plan.items]
    assert names == ["Sol Ring"]      # Rhystic over budget, Owned skipped, fake dropped
    assert plan.total == 2.0 and plan.budget == 10.0


@pytest.mark.asyncio
async def test_build_prompt_and_upgrade_routes(client, session, monkeypatch):
    await save_config(session, base_url="http://x/v1", api_key="k", chat_model="m",
                      embed_model="e", enabled=True)
    await _seed(session, "Lightning Bolt", ci=("R",), type_line="Instant")
    deck = await create_deck(session, "D", "1 Lightning Bolt")

    monkeypatch.setattr("src.routes.ai.ChatClient", lambda cfg: FakeChat(reply="4 Lightning Bolt"))
    built = await client.post("/decks/build/prompt", data={"prompt": "red deck"})
    assert built.status_code == 200 and "Lightning Bolt" in built.text

    up = await client.post(f"/decks/{deck.id}/upgrade", data={"budget": "25"})
    assert up.status_code == 200  # renders a partial (no upgrades needed is fine)

    finder = await client.get("/ai/commanders")
    assert finder.status_code == 200


def test_clean_query_balances_and_unwraps():
    from src.llm import _clean_query
    assert _clean_query('t:creature o:"draw a card') == 't:creature o:"draw a card"'  # dangling
    assert _clean_query('"c:r t:instant"') == 'c:r t:instant'  # whole query wrapped in quotes
    assert _clean_query('o:"counter target"') == 'o:"counter target"'  # already valid
    assert _clean_query("```\nc:u o:counter\n```") == 'c:u o:counter'  # code fence


@pytest.mark.asyncio
async def test_deck_chat_and_card_question_units(session):
    from src.llm import answer_card_question, deck_chat
    await _own(session, "Lightning Bolt")
    deck = await create_deck(session, "D", "1 Lightning Bolt")
    reply = await deck_chat(
        session, deck,
        [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}],
        "make it more aggressive", FakeChat(reply="Add more one-drops."),
    )
    assert "one-drops" in reply

    card = await _seed(session, "Rules Card")
    ans = await answer_card_question(
        card, ["It works with indestructible."], "does it work?",
        FakeChat(reply="Yes, per the ruling."),
    )
    assert "Yes" in ans


@pytest.mark.asyncio
async def test_deck_chat_routes(client, session, monkeypatch):
    from sqlalchemy import func, select
    from src.models import DeckChatMessage
    await save_config(session, base_url="http://x/v1", api_key="k", chat_model="m",
                      embed_model="e", enabled=True)
    await _own(session, "Lightning Bolt")
    deck = await create_deck(session, "D", "1 Lightning Bolt")
    monkeypatch.setattr("src.routes.ai.ChatClient",
                        lambda cfg: FakeChat(reply="Cut the five-drops."))

    resp = await client.post(f"/decks/{deck.id}/chat", data={"message": "help me"})
    assert resp.status_code == 200 and "Cut the five-drops." in resp.text and "help me" in resp.text
    assert await session.scalar(select(func.count()).select_from(DeckChatMessage)) == 2

    page = await client.get(f"/decks/{deck.id}/chat")
    assert page.status_code == 200 and "Cut the five-drops." in page.text

    clr = await client.post(f"/decks/{deck.id}/chat/clear")
    assert clr.status_code == 200
    assert await session.scalar(select(func.count()).select_from(DeckChatMessage)) == 0


@pytest.mark.asyncio
async def test_card_ask_route(client, session, monkeypatch):
    await save_config(session, base_url="http://x/v1", api_key="k", chat_model="m",
                      embed_model="e", enabled=True)
    card = await _seed(session, "Rules Card")

    async def no_rulings(sid, c):
        return []
    monkeypatch.setattr("src.routes.ai.fetch_rulings", no_rulings)
    monkeypatch.setattr("src.routes.ai.ChatClient", lambda cfg: FakeChat(reply="Yes, it works."))

    resp = await client.post(f"/card/{card.scryfall_id}/ask", data={"question": "does it work?"})
    assert resp.status_code == 200 and "Yes, it works." in resp.text


@pytest.mark.asyncio
async def test_deck_ai_context_detects_commander_and_identity(session):
    from src.llm import deck_ai_context
    await _seed(session, "Kaalia", ci=("W", "B", "R"), type_line="Legendary Creature — Angel")
    await _seed(session, "Some Red Card", ci=("R",))
    deck = await create_deck(session, "Mardu", "1 Kaalia\n1 Some Red Card")
    ctx = await deck_ai_context(session, deck)
    assert ctx.identity == {"W", "B", "R"} and ctx.is_commander and ctx.commander == "Kaalia"
    assert ctx.identity_str == "WBR" and "Commander" in ctx.format_note


@pytest.mark.asyncio
async def test_plan_upgrades_enforces_color_identity(session):
    from src.llm import plan_upgrades
    # Mono-red Commander deck.
    await _seed(session, "Red Legend", ci=("R",), type_line="Legendary Creature")
    deck = await create_deck(session, "Mono Red", "1 Red Legend")
    await _seed(session, "Sol Ring", ci=(), usd="2.00", owned=False, type_line="Artifact")
    await _seed(session, "Blue Bolt", ci=("U",), usd="1.00", owned=False, type_line="Instant")
    await _seed(session, "Red Ramp", ci=("R",), usd="1.00", owned=False)
    reply = "Blue Bolt - draw\nRed Ramp - ramp\nSol Ring - ramp"
    plan = await plan_upgrades(session, deck, budget=100.0, client=FakeChat(reply=reply))
    names = [it.name for it in plan.items]
    assert "Blue Bolt" not in names                      # off-color-identity -> dropped (#192)
    assert "Red Ramp" in names and "Sol Ring" in names   # in-identity + colorless kept
