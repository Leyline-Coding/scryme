"""Coverage for src/routes/ai.py.

The AI route handlers are exercised by calling them directly with a hand-built ``Request`` (the
ASGI client path isn't traced by coverage). All LLM access is faked — no network calls."""

import uuid

import httpx
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from src.config import get_settings
from src.decks import create_deck
from src.llm import save_config
from src.models import Card, CollectionCard, DeckChatMessage
from src.routes import ai as ai_routes
from src.scryfall.mapping import card_to_columns
from starlette.requests import Request


class FakeChat:
    """Stands in for ChatClient: returns a canned reply, or raises."""

    def __init__(self, reply="ok", raise_exc=None):
        self.reply = reply
        self.raise_exc = raise_exc

    async def chat(self, messages, **kwargs):
        if self.raise_exc:
            raise self.raise_exc
        return self.reply


def _req(query_string=b"") -> Request:
    return Request({"type": "http", "method": "POST", "path": "/", "headers": [],
                    "query_string": query_string})


def _body(resp) -> str:
    return resp.body.decode()


async def _seed(session, name, ci=("R",), usd="1.00", owned=True, legal=True,
                type_line="Instant"):
    legalities = {"commander": "legal" if legal else "not_legal"}
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
         "collector_number": str(abs(hash(name)) % 99999), "type_line": type_line,
         "color_identity": list(ci), "prices": {"usd": usd},
         "oracle_text": f"{name} does something.", "legalities": legalities}
    ))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


async def _ready(session):
    await save_config(session, base_url="http://x/v1", api_key="k", chat_model="m",
                      embed_model="e", enabled=True)


def _fake_client(monkeypatch, chat):
    monkeypatch.setattr(ai_routes, "ChatClient", lambda cfg: chat)


# --- /ai settings ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ai_settings_renders_saved_flag(session):
    await _ready(session)
    resp = await ai_routes.ai_settings(_req(b"saved=1"), session)
    assert resp.status_code == 200
    assert "AI settings" in _body(resp) and "(set)" in _body(resp)


@pytest.mark.asyncio
async def test_ai_save_writes_and_redirects(session):
    resp = await ai_routes.ai_save(
        base_url="http://y/v1", api_key="secret", chat_model="cm", embed_model="em",
        enabled="1", session=session)
    assert resp.status_code == 303 and resp.headers["location"] == "/ai?saved=1"
    from src.llm import get_config
    cfg = await get_config(session)
    assert cfg.base_url == "http://y/v1" and cfg.ready and cfg.api_key == "secret"


@pytest.mark.asyncio
async def test_ai_save_blocked_when_read_only(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    with pytest.raises(HTTPException) as exc:
        await ai_routes.ai_save(base_url="http://z", api_key="", chat_model="", embed_model="",
                                enabled=None, session=session)
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_ai_test_not_configured(session):
    resp = await ai_routes.ai_test(_req(), session)
    assert resp.status_code == 200 and "endpoint" in _body(resp).lower()


@pytest.mark.asyncio
async def test_ai_test_ok(session, monkeypatch):
    await _ready(session)
    monkeypatch.setattr("src.llm.ChatClient", lambda cfg: FakeChat(reply="ok"))
    resp = await ai_routes.ai_test(_req(), session)
    assert resp.status_code == 200 and "Connected" in _body(resp)


# --- _load_deck -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_load_deck_missing_404(session):
    with pytest.raises(HTTPException) as exc:
        await ai_routes._load_deck(session, 987654)
    assert exc.value.status_code == 404


# --- deck analyze ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deck_analyze_not_configured(session):
    await _seed(session, "Lightning Bolt")
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    resp = await ai_routes.deck_analyze(_req(), deck.id, session)
    assert resp.status_code == 200 and "configured" in _body(resp)


@pytest.mark.asyncio
async def test_deck_analyze_success(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(reply="This is an aggressive red deck."))
    resp = await ai_routes.deck_analyze(_req(), deck.id, session)
    assert resp.status_code == 200 and "aggressive" in _body(resp)


@pytest.mark.asyncio
async def test_deck_analyze_unreachable(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(raise_exc=httpx.ConnectError("nope")))
    resp = await ai_routes.deck_analyze(_req(), deck.id, session)
    assert resp.status_code == 200 and "reach" in _body(resp).lower()


@pytest.mark.asyncio
async def test_deck_analyze_empty(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(reply="   "))
    resp = await ai_routes.deck_analyze(_req(), deck.id, session)
    assert resp.status_code == 200 and "empty" in _body(resp).lower()


# --- deck suggest ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deck_suggest_success(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    await _seed(session, "Shock")
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(reply="Shock - cheap removal"))
    resp = await ai_routes.deck_suggest(_req(), deck.id, session)
    assert resp.status_code == 200 and "Shock" in _body(resp)


@pytest.mark.asyncio
async def test_deck_suggest_empty(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    await _seed(session, "Shock")
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(reply=""))
    resp = await ai_routes.deck_suggest(_req(), deck.id, session)
    assert resp.status_code == 200 and "empty" in _body(resp).lower()


@pytest.mark.asyncio
async def test_deck_suggest_not_configured(session):
    await _seed(session, "Lightning Bolt")
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    resp = await ai_routes.deck_suggest(_req(), deck.id, session)
    assert resp.status_code == 200 and "configured" in _body(resp)


@pytest.mark.asyncio
async def test_deck_suggest_unreachable(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    await _seed(session, "Shock")
    deck = await create_deck(session, "Burn", "1 Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(raise_exc=httpx.ReadTimeout("slow")))
    resp = await ai_routes.deck_suggest(_req(), deck.id, session)
    assert resp.status_code == 200 and "reach" in _body(resp).lower()


# --- build from prompt ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_prompt_not_configured(session):
    resp = await ai_routes.build_prompt(_req(), prompt="red deck", session=session)
    assert resp.status_code == 200 and "configured" in _body(resp)


@pytest.mark.asyncio
async def test_build_prompt_success(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(reply="4 Lightning Bolt"))
    resp = await ai_routes.build_prompt(_req(), prompt="a red deck", session=session)
    assert resp.status_code == 200 and "Lightning Bolt" in _body(resp)


@pytest.mark.asyncio
async def test_build_prompt_unreachable(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(raise_exc=httpx.ConnectError("nope")))
    resp = await ai_routes.build_prompt(_req(), prompt="a red deck", session=session)
    assert resp.status_code == 200 and "reach" in _body(resp).lower()


@pytest.mark.asyncio
async def test_build_prompt_read_only(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    with pytest.raises(HTTPException) as exc:
        await ai_routes.build_prompt(_req(), prompt="x", session=session)
    assert exc.value.status_code == 403


# --- commander finder -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_commander_finder_with_ai(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Kaalia", ci=("W", "B", "R"),
                type_line="Legendary Creature — Angel")
    _fake_client(monkeypatch, FakeChat(reply="Kaalia - aggressive angels"))
    resp = await ai_routes.commander_finder(_req(), session)
    assert resp.status_code == 200 and "Kaalia" in _body(resp)


@pytest.mark.asyncio
async def test_commander_finder_without_ai(session):
    await _seed(session, "Kaalia", ci=("W", "B", "R"),
                type_line="Legendary Creature — Angel")
    resp = await ai_routes.commander_finder(_req(), session)
    assert resp.status_code == 200


# --- deck upgrade ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deck_upgrade_not_configured(session):
    await _seed(session, "In Deck", ci=("R",))
    deck = await create_deck(session, "D", "1 In Deck")
    resp = await ai_routes.deck_upgrade(_req(), deck.id, budget=25.0, session=session)
    assert resp.status_code == 200 and "configured" in _body(resp)


@pytest.mark.asyncio
async def test_deck_upgrade_success(session, monkeypatch):
    await _ready(session)
    await _seed(session, "In Deck", ci=("R",))
    deck = await create_deck(session, "D", "1 In Deck")
    await _seed(session, "Sol Ring", ci=(), usd="2.00", owned=False, type_line="Artifact")
    _fake_client(monkeypatch, FakeChat(reply="Sol Ring - ramp"))
    resp = await ai_routes.deck_upgrade(_req(), deck.id, budget=25.0, session=session)
    assert resp.status_code == 200 and "Sol Ring" in _body(resp)


@pytest.mark.asyncio
async def test_deck_upgrade_empty(session, monkeypatch):
    await _ready(session)
    await _seed(session, "In Deck", ci=("R",))
    deck = await create_deck(session, "D", "1 In Deck")
    _fake_client(monkeypatch, FakeChat(reply=""))
    resp = await ai_routes.deck_upgrade(_req(), deck.id, budget=25.0, session=session)
    assert resp.status_code == 200 and "empty" in _body(resp).lower()


@pytest.mark.asyncio
async def test_deck_upgrade_unreachable(session, monkeypatch):
    await _ready(session)
    await _seed(session, "In Deck", ci=("R",))
    deck = await create_deck(session, "D", "1 In Deck")
    _fake_client(monkeypatch, FakeChat(raise_exc=httpx.ConnectError("nope")))
    resp = await ai_routes.deck_upgrade(_req(), deck.id, budget=25.0, session=session)
    assert resp.status_code == 200 and "reach" in _body(resp).lower()


# --- deck chat ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deck_chat_page(session):
    await _seed(session, "Lightning Bolt")
    deck = await create_deck(session, "D", "1 Lightning Bolt")
    resp = await ai_routes.deck_chat_page(_req(), deck.id, session)
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_deck_chat_send_success(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    deck = await create_deck(session, "D", "1 Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(reply="Cut the five-drops."))
    resp = await ai_routes.deck_chat_send(_req(), deck.id, message="help me", session=session)
    body = _body(resp)
    assert resp.status_code == 200 and "Cut the five-drops." in body and "help me" in body
    n = await session.scalar(select(func.count()).select_from(DeckChatMessage))
    assert n == 2


@pytest.mark.asyncio
async def test_deck_chat_send_no_config_noop(session):
    await _seed(session, "Lightning Bolt")
    deck = await create_deck(session, "D", "1 Lightning Bolt")
    resp = await ai_routes.deck_chat_send(_req(), deck.id, message="help", session=session)
    assert resp.status_code == 200
    n = await session.scalar(select(func.count()).select_from(DeckChatMessage))
    assert n == 0  # not ready -> nothing stored


@pytest.mark.asyncio
async def test_deck_chat_send_unreachable_stores_fallback(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    deck = await create_deck(session, "D", "1 Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(raise_exc=httpx.ConnectError("nope")))
    resp = await ai_routes.deck_chat_send(_req(), deck.id, message="help", session=session)
    assert resp.status_code == 200 and "respond" in _body(resp)


@pytest.mark.asyncio
async def test_deck_chat_clear(session, monkeypatch):
    await _ready(session)
    await _seed(session, "Lightning Bolt")
    deck = await create_deck(session, "D", "1 Lightning Bolt")
    _fake_client(monkeypatch, FakeChat(reply="reply"))
    await ai_routes.deck_chat_send(_req(), deck.id, message="hi", session=session)
    resp = await ai_routes.deck_chat_clear(_req(), deck.id, session)
    assert resp.status_code == 200
    n = await session.scalar(select(func.count()).select_from(DeckChatMessage))
    assert n == 0


# --- card ask -------------------------------------------------------------------------------------

async def _no_rulings(sid, card):
    return []


@pytest.mark.asyncio
async def test_card_ask_not_configured(session):
    card = await _seed(session, "Rules Card")
    resp = await ai_routes.card_ask(_req(), str(card.scryfall_id), question="does it work?",
                                    session=session)
    assert resp.status_code == 200 and "configured" in _body(resp)


@pytest.mark.asyncio
async def test_card_ask_success(session, monkeypatch):
    await _ready(session)
    card = await _seed(session, "Rules Card")
    monkeypatch.setattr(ai_routes, "fetch_rulings", _no_rulings)
    _fake_client(monkeypatch, FakeChat(reply="Yes, it works."))
    resp = await ai_routes.card_ask(_req(), str(card.scryfall_id), question="does it work?",
                                    session=session)
    assert resp.status_code == 200 and "Yes, it works." in _body(resp)


@pytest.mark.asyncio
async def test_card_ask_with_rulings(session, monkeypatch):
    await _ready(session)
    card = await _seed(session, "Ruled Card")

    async def some_rulings(sid, c):
        return [{"comment": "It works with indestructible."}, {"comment": ""}]
    monkeypatch.setattr(ai_routes, "fetch_rulings", some_rulings)
    _fake_client(monkeypatch, FakeChat(reply="Confirmed."))
    resp = await ai_routes.card_ask(_req(), str(card.scryfall_id), question="q?", session=session)
    assert resp.status_code == 200 and "Confirmed." in _body(resp)


@pytest.mark.asyncio
async def test_card_ask_unreachable(session, monkeypatch):
    await _ready(session)
    card = await _seed(session, "Rules Card")
    monkeypatch.setattr(ai_routes, "fetch_rulings", _no_rulings)
    _fake_client(monkeypatch, FakeChat(raise_exc=httpx.ConnectError("nope")))
    resp = await ai_routes.card_ask(_req(), str(card.scryfall_id), question="q?", session=session)
    assert resp.status_code == 200 and "reach" in _body(resp).lower()


@pytest.mark.asyncio
async def test_card_ask_empty(session, monkeypatch):
    await _ready(session)
    card = await _seed(session, "Rules Card")
    monkeypatch.setattr(ai_routes, "fetch_rulings", _no_rulings)
    _fake_client(monkeypatch, FakeChat(reply="   "))
    resp = await ai_routes.card_ask(_req(), str(card.scryfall_id), question="q?", session=session)
    assert resp.status_code == 200 and "empty" in _body(resp).lower()
