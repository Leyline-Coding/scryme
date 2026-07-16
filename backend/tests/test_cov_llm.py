"""Coverage for src/llm.py: secret store, ChatClient HTTP, parsers, and grounded-feature edges.

All model access is faked (deterministic fakes or an httpx MockTransport) — no network calls."""

import uuid
from collections import Counter

import httpx
import pytest
from src import llm
from src.decks import create_deck
from src.llm import (
    ChatClient,
    DeckContext,
    LLMConfig,
    _clean_query,
    _deck_themes,
    _parse_decklines,
    _parse_named_reasons,
    _parse_suggestions,
    _scan_deck_cards,
    _validate_upgrade,
    answer_card_question,
    build_from_prompt,
    decrypt_secret,
    encrypt_secret,
    find_commanders,
    has_config_row,
    save_config,
    suggest_from_collection,
)
from src.llm import test_connection as check_connection
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


class FakeChat:
    def __init__(self, reply="ok", raise_exc=None):
        self.reply = reply
        self.raise_exc = raise_exc

    async def chat(self, messages, **kwargs):
        if self.raise_exc:
            raise self.raise_exc
        return self.reply


def _mkcard(name, ci=("R",), usd="1.00", legal=True, type_line="Instant"):
    legalities = {"commander": "legal" if legal else "not_legal"}
    return Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
         "collector_number": str(abs(hash(name)) % 99999), "type_line": type_line,
         "color_identity": list(ci), "prices": {"usd": usd},
         "oracle_text": f"{name} text", "legalities": legalities}))


async def _own(session, name, ci=("R",), usd="1.00", type_line="Instant"):
    c = _mkcard(name, ci=ci, usd=usd, type_line=type_line)
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


# --- secret store ---------------------------------------------------------------------------------

def test_secret_roundtrip_and_bad_token():
    token = encrypt_secret("hunter2")
    assert token != "hunter2" and decrypt_secret(token) == "hunter2"
    assert decrypt_secret(None) == ""          # line 55: empty token
    assert decrypt_secret("not-a-token") == ""  # lines 58-59: InvalidToken -> ""


def test_fernet_generates_key_when_absent(tmp_path, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "data_dir", str(tmp_path))
    token = encrypt_secret("hello")  # lines 42-45: generate + write the key file
    assert (tmp_path / "llm.key").exists()
    assert decrypt_secret(token) == "hello"


# --- ChatClient.chat over a mocked transport ------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_client_posts_and_returns_content(monkeypatch):
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"choices": [{"message": {"content": " hi there "}}]})

    transport = httpx.MockTransport(handler)
    orig = httpx.AsyncClient
    monkeypatch.setattr(llm.httpx, "AsyncClient",
                        lambda **kw: orig(transport=transport, **kw))

    cfg = LLMConfig(base_url="http://x/v1/", api_key="secret", chat_model="m", enabled=True)
    out = await ChatClient(cfg).chat([{"role": "user", "content": "hey"}])
    assert out == "hi there"
    assert captured["url"] == "http://x/v1/chat/completions"
    assert captured["auth"] == "Bearer secret"


@pytest.mark.asyncio
async def test_connection_http_status_error():
    req = httpx.Request("POST", "http://x/v1/chat/completions")
    resp = httpx.Response(503, request=req)
    exc = httpx.HTTPStatusError("boom", request=req, response=resp)
    cfg = LLMConfig(base_url="http://x/v1", chat_model="m", enabled=True)
    ok, msg = await check_connection(cfg, client=FakeChat(raise_exc=exc))
    assert not ok and "HTTP 503" in msg


# --- DeckContext.block ----------------------------------------------------------------------------

def test_block_multiple_commanders_and_themes():
    ctx = DeckContext(
        name="D", identity={"R"}, commanders=["Kaalia", "Talrand"],
        curve="1:2", colors="R:3", themes=["tokens", "sacrifice"],
        key_cards=["Sol Ring"], decklist="1 Sol Ring")
    block = ctx.block()
    assert "Possible commanders" in block and "Kaalia" in block   # line 209
    assert "Themes/keywords: tokens, sacrifice" in block          # line 216
    assert "Notable cards: Sol Ring" in block


# --- deck scan / themes ---------------------------------------------------------------------------

def test_scan_counts_keywords_and_finds_commander():
    info = {
        "s1": ("Kaalia", "Legendary Creature — Angel", ["W", "B", "R"],
               "Flying\nWhenever ...", ["Flying", "Haste"], {"usd": "5.00"}),
        "s2": ("Bolt", "Instant", ["R"], "deal 3", ["Flying"], {"usd": "1.00"}),
        "s3": ("Forest", "Basic Land", ["G"], "", [], {"usd": "0.10"}),
    }
    scan = _scan_deck_cards(info)  # exercises keyword counting (line 244)
    assert scan.kw_counts["Flying"] == 2 and scan.commander == "Kaalia"
    assert "W" in scan.identity and len(scan.valued) == 2  # land excluded from valued


def test_deck_themes_text_signal():
    kw = Counter()
    text_cards = ["create a token", "make a token now", "another token effect", "token token"]
    themes = _deck_themes(kw, text_cards)
    assert "tokens" in themes  # line 262: text-signal theme seen in >=4 cards


# --- suggestion / decklist / named-reason parsers -------------------------------------------------

def test_parse_suggestions_blank_and_no_separator():
    pool = {"shock": ("Shock", "sid-1")}
    text = "\n   \nShock\nUnknown Card"      # blank line (378), no-separator line (384)
    out = _parse_suggestions(text, pool)
    assert [s.name for s in out] == ["Shock"]


def test_parse_decklines_skips_nonmatch_and_fixes_numbering():
    pool = {n: (n.title(), f"sid-{n}", 1.0) for n in ("aaa", "bbb", "ccc", "ddd")}
    text = "not a deck line\n1 Aaa\n2 Bbb\n3 Ccc\n4 Ddd"  # 565 skip, 577-578 numbering reset
    lines = _parse_decklines(text, pool)
    assert len(lines) == 4 and all(ln.quantity == 1 for ln in lines)


def test_parse_named_reasons_skips_blank():
    out = _parse_named_reasons("\n\nSol Ring - ramp\n")  # line 696 continue
    assert out == [("Sol Ring", "ramp")]


# --- _clean_query ---------------------------------------------------------------------------------

def test_clean_query_prefix_and_empty():
    assert _clean_query("query: c:r t:instant") == "c:r t:instant"  # line 455 prefix strip
    assert _clean_query("\n   \n") == ""                            # line 466 all-blank -> ""


# --- suggest_from_collection empty pool -----------------------------------------------------------

@pytest.mark.asyncio
async def test_suggest_empty_pool(session):
    await _own(session, "Only Card")
    deck = await create_deck(session, "D", "1 Only Card")  # the only owned card is in the deck
    result = await suggest_from_collection(session, deck, FakeChat(reply="whatever"))
    assert result.considered == 0 and result.suggestions == []


# --- has_config_row -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_has_config_row(session):
    assert await has_config_row(session) is False
    await save_config(session, base_url="http://x", api_key="k", chat_model="m",
                      embed_model="e", enabled=True)
    assert await has_config_row(session) is True


# --- build_from_prompt empty pool -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_build_from_prompt_empty_pool(session):
    built = await build_from_prompt(session, "a red deck", FakeChat(reply="4 Bolt"))
    assert built.lines == [] and built.considered == 0 and built.name == ""


# --- find_commanders edges ------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_find_commanders_none_when_no_legends(session):
    await _own(session, "Lightning Bolt")  # not a legendary creature
    assert await find_commanders(session, client=FakeChat()) == []


@pytest.mark.asyncio
async def test_find_commanders_survives_pitch_failure(session):
    await _own(session, "Kaalia", ci=("W", "B", "R"),
               type_line="Legendary Creature — Angel")
    picks = await find_commanders(session, client=FakeChat(raise_exc=httpx.ConnectError("x")))
    assert picks and picks[0].name == "Kaalia" and picks[0].pitch == ""  # exception swallowed


# --- _validate_upgrade guards ---------------------------------------------------------------------

def test_validate_upgrade_off_identity_dropped():
    ctx = DeckContext(name="d", identity={"R"}, is_commander=False)
    card = _mkcard("Blue Thing", ci=("U",))
    assert _validate_upgrade(card, "draw", ctx, set(), 100.0) is None  # line 716


def test_validate_upgrade_commander_illegal_dropped():
    ctx = DeckContext(name="d", identity={"R"}, is_commander=True)
    card = _mkcard("Banned Card", ci=("R",), legal=False)
    assert _validate_upgrade(card, "ramp", ctx, set(), 100.0) is None  # line 719


def test_validate_upgrade_over_budget_and_free_dropped():
    ctx = DeckContext(name="d", identity={"R"}, is_commander=False)
    pricey = _mkcard("Pricey", ci=("R",), usd="50.00")
    assert _validate_upgrade(pricey, "x", ctx, set(), 10.0) is None   # line 719: over remaining
    free = _mkcard("Free", ci=("R",), usd="0.00")
    assert _validate_upgrade(free, "x", ctx, set(), 100.0) is None    # line 718-719: price <= 0


def test_validate_upgrade_accepts_valid():
    ctx = DeckContext(name="d", identity={"R"}, is_commander=True)
    card = _mkcard("Good Ramp", ci=("R",), usd="2.00", legal=True)
    result = _validate_upgrade(card, "ramp", ctx, set(), 100.0)
    assert result is not None and result[0].name == "Good Ramp" and result[1] == 2.0


# --- answer_card_question with rules context ------------------------------------------------------

@pytest.mark.asyncio
async def test_answer_card_question_includes_rules_context():
    card = _mkcard("Rules Card")
    captured = {}

    class Recording:
        async def chat(self, messages, **kw):
            captured["user"] = messages[-1]["content"]
            return "Answer per 702.19."

    ans = await answer_card_question(
        card, ["some ruling"], "how does it work?", Recording(),
        rules_context=["702.19 Trample\nExcess damage carries over."])  # line 793
    assert "702.19" in ans
    assert "Comprehensive Rules" in captured["user"] and "Trample" in captured["user"]
