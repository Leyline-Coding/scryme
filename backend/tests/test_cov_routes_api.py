"""Coverage for the JSON API (/api/v1).

The handlers run in a greenlet context when driven over HTTP, which the repo's default coverage
config does not trace — so these tests call the endpoint functions *directly* (as test_stats /
test_admin_dashboard do for their modules) to exercise the handler bodies.
"""

import uuid
from types import SimpleNamespace

import httpx
import pytest
import src.routes.api as api
from fastapi import HTTPException
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _card(session, name="Aaa", n=1, owned=0, oracle=None, prices=None):
    raw = {"id": str(uuid.uuid4()), "oracle_id": oracle or str(uuid.uuid4()), "name": name,
           "set": "tst", "collector_number": str(n), "rarity": "rare", "type_line": "Instant",
           "oracle_text": "Deal damage.", "colors": ["R"], "color_identity": ["R"],
           "legalities": {"modern": "legal"},
           "prices": prices or {"usd": "2.00", "eur": "1.50"},
           "image_uris": {"normal": "http://img/x.jpg"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=owned))
    await session.commit()
    return c


# --- token guard ---------------------------------------------------------------------------

def test_require_api_token_noop_when_unset(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "api_token", "")
    # No token configured -> returns without raising regardless of headers.
    api.require_api_token(SimpleNamespace(headers={}))


def test_require_api_token_accepts_bearer_and_apikey(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "api_token", "secret")
    api.require_api_token(SimpleNamespace(headers={"Authorization": "Bearer secret"}))
    api.require_api_token(SimpleNamespace(headers={"X-API-Key": "secret"}))
    with pytest.raises(HTTPException) as exc:
        api.require_api_token(SimpleNamespace(headers={"X-API-Key": "wrong"}))
    assert exc.value.status_code == 401


def test_guard_writable(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", False)
    api._guard_writable()  # no raise
    monkeypatch.setattr(get_settings(), "read_only", True)
    with pytest.raises(HTTPException) as exc:
        api._guard_writable()
    assert exc.value.status_code == 403


# --- search --------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_search_returns_cards(session):
    await _card(session, "Bolt", 1, owned=3)
    out = await api.api_search(q="bolt", scope="all", sort="bogus", dir="desc", session=session)
    assert out.total == 1 and out.cards[0].name == "Bolt" and out.cards[0].quantity == 3


@pytest.mark.asyncio
async def test_search_error_400(session):
    with pytest.raises(HTTPException) as exc:
        await api.api_search(q="badfield:x", session=session)
    assert exc.value.status_code == 400


# --- NL search -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_nl_search_not_ready(session, monkeypatch):
    async def cfg(_s):
        return SimpleNamespace(ready=False)
    monkeypatch.setattr(api, "get_config", cfg)
    out = await api.api_search_nl(q="cheap red burn", session=session)
    assert out.query == "" and out.ok is False


@pytest.mark.asyncio
async def test_nl_search_success(session, monkeypatch):
    async def cfg(_s):
        return SimpleNamespace(ready=True)

    async def nl(prompt, cclient):
        return "c:r cmc<=2"
    monkeypatch.setattr(api, "get_config", cfg)
    monkeypatch.setattr(api, "nl_to_query", nl)
    out = await api.api_search_nl(q="cheap red burn", session=session)
    assert out.query == "c:r cmc<=2" and out.ok is True


@pytest.mark.asyncio
async def test_nl_search_swallows_error(session, monkeypatch):
    async def cfg(_s):
        return SimpleNamespace(ready=True)

    async def boom(prompt, cclient):
        raise httpx.HTTPError("down")
    monkeypatch.setattr(api, "get_config", cfg)
    monkeypatch.setattr(api, "nl_to_query", boom)
    out = await api.api_search_nl(q="anything", session=session)
    assert out.query == "" and out.ok is False


# --- card detail + similar -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_card_detail(session):
    c = await _card(session, "Bolt", 1, owned=2)
    out = await api.api_card(str(c.scryfall_id), session=session)
    assert out.quantity == 2 and len(out.owned) == 1 and out.legalities["modern"] == "legal"


@pytest.mark.asyncio
async def test_get_card_bad_uuid_and_missing(session):
    with pytest.raises(HTTPException) as e1:
        await api._get_card(session, "not-a-uuid")
    assert e1.value.status_code == 404
    with pytest.raises(HTTPException) as e2:
        await api._get_card(session, "00000000-0000-0000-0000-000000000000")
    assert e2.value.status_code == 404


@pytest.mark.asyncio
async def test_similar_no_oracle(session):
    c = Card(scryfall_id=uuid.uuid4(), name="X", set_code="tst", collector_number="9",
             raw={"name": "X"})
    session.add(c)
    await session.commit()
    assert await api.api_similar(str(c.scryfall_id), session=session) == []


@pytest.mark.asyncio
async def test_similar_with_embeddings(session):
    from src.embeddings import backfill_embeddings

    class FakeClient:
        model = "fake-embed"

        async def embed(self, texts):
            return [[1.0, 0.0, 0.0] if "Serra" in t else [0.9, 0.1, 0.0] for t in texts]

    serra = None
    for i, name in enumerate(["Serra Angel", "Shivan Dragon"], 1):
        c = Card(**card_to_columns(
            {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
             "collector_number": str(i), "type_line": "Creature", "oracle_text": "Flying"}))
        session.add(c)
        await session.flush()
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
        if name == "Serra Angel":
            serra = c
    await session.commit()
    await backfill_embeddings(session, scope="owned", client=FakeClient())
    out = await api.api_similar(str(serra.scryfall_id), scope="owned", session=session)
    assert out and out[0].name == "Shivan Dragon" and out[0].score >= 0


@pytest.mark.asyncio
async def test_representative_cards_empty():
    # Early-return branch for an empty oracle-id list.
    assert await api._representative_cards(None, []) == ({}, {})


# --- decks ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_deck_crud_export_and_404s(session):
    await _card(session, "Lightning Bolt", 1, owned=1)
    deck = await api.api_deck_create(api.DeckCreateIn(name="Burn", decklist="1 Lightning Bolt"),
                                     session=session)
    did = deck.id

    listing = await api.api_decks(session=session)
    assert listing[0].name == "Burn"
    detail = await api.api_deck(did, format="modern", session=session)
    assert detail.fmt == "modern"

    renamed = await api.api_deck_update(did, api.DeckUpdateIn(name="Burn2"), session=session)
    assert renamed.name == "Burn2"
    # blank rename keeps the old name
    kept = await api.api_deck_update(did, api.DeckUpdateIn(name="  "), session=session)
    assert kept.name == "Burn2"
    # None name is a no-op
    noop = await api.api_deck_update(did, api.DeckUpdateIn(name=None), session=session)
    assert noop.name == "Burn2"

    exp = await api.api_deck_export(did, fmt="bogus", session=session)  # bad fmt -> text
    assert b"Lightning Bolt" in exp.body

    card_id = deck.main[0].card_id
    ok = await api.api_deck_card_update(did, card_id, api.DeckCardUpdateIn(language="ja"),
                                        session=session)
    assert ok.ok is True
    with pytest.raises(HTTPException) as exc:
        await api.api_deck_card_update(did, 999999, api.DeckCardUpdateIn(language="ja"),
                                       session=session)
    assert exc.value.status_code == 404

    assert (await api.api_deck_delete(did, session=session)).ok is True
    # delete of an already-gone deck is a no-op OkOut (deck is None branch)
    assert (await api.api_deck_delete(did, session=session)).ok is True

    for coro in (
        api.api_deck(424242, session=session),
        api.api_deck_update(424242, api.DeckUpdateIn(name="x"), session=session),
        api.api_deck_export(424242, session=session),
    ):
        with pytest.raises(HTTPException) as e:
            await coro
        assert e.value.status_code == 404


# --- wishlist ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_wishlist_add_view_remove(session):
    c = await _card(session, "Bolt", 1)
    added = await api.api_wishlist_add(api.WishlistAddIn(scryfall_id=str(c.scryfall_id),
                                                         quantity=2), session=session)
    assert added.quantity == 2
    view = await api.api_wishlist(currency="eur", session=session)
    assert view.total_cards == 2 and view.items[0].price == 1.50
    with pytest.raises(HTTPException) as exc:
        await api.api_wishlist_add(
            api.WishlistAddIn(scryfall_id="00000000-0000-0000-0000-000000000000"), session=session)
    assert exc.value.status_code == 404
    assert (await api.api_wishlist_remove(str(c.scryfall_id), session=session)).ok is True


# --- stats ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stats(session):
    await _card(session, "Bolt", 1, owned=3)
    out = await api.api_stats(currency="eur", session=session)
    assert out.total_cards == 3 and any(b.label == "Red" for b in out.by_color)


# --- collection ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_collection_list_filters_and_mutations(session):
    a = await _card(session, "Alpha", 1, owned=2)
    await _card(session, "Beta", 2, owned=1)

    filtered = await api.api_collection_list(q="alph", session=session)
    assert filtered.total == 1 and filtered.items[0].name == "Alpha"

    added = await api.api_collection_add(
        api.CollectionAddIn(scryfall_id=str(a.scryfall_id), quantity=1, binder="BoxA"),
        session=session)
    assert added.quantity >= 1
    binder_view = await api.api_collection_list(binder="BoxA", session=session)
    assert binder_view.total >= 1

    with pytest.raises(HTTPException) as exc:
        await api.api_collection_add(
            api.CollectionAddIn(scryfall_id="00000000-0000-0000-0000-000000000000"),
            session=session)
    assert exc.value.status_code == 404

    row_id = filtered.items[0].id
    up = await api.api_collection_update(row_id, api.StackUpdateIn(quantity=9, binder="New"),
                                         currency="eur", session=session)
    assert up.quantity == 9
    assert (await api.api_collection_delete(row_id, session=session)).ok is True

    with pytest.raises(HTTPException):
        await api.api_collection_update(999999, api.StackUpdateIn(quantity=1), session=session)
    with pytest.raises(HTTPException):
        await api.api_collection_delete(999999, session=session)


# --- tags ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_tags_add_remove(session):
    c = await _card(session, "Bolt", 1, owned=1)
    added = await api.api_add_tag(str(c.scryfall_id), api.TagIn(tag="Trade"), session=session)
    assert added.tags == ["trade"]
    removed = await api.api_remove_tag(str(c.scryfall_id), tag="trade", session=session)
    assert removed.tags == []


# --- prices --------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_prices_empty(session):
    out = await api.api_prices(session=session)
    assert out.current_value == 0.0 and out.series == []


@pytest.mark.asyncio
async def test_prices_with_movers_and_pl(session):
    from src.prices import snapshot_prices
    ca = await _card(session, "Aaa", 1, prices={"usd": "1.00"})
    session.add(CollectionCard(scryfall_id=ca.scryfall_id, quantity=2, finish="normal",
                               purchase_price=0.50))
    await session.commit()
    await snapshot_prices(session)
    ca.raw = {**ca.raw, "prices": {"usd": "3.00"}}
    ca.prices = {"usd": "3.00"}
    await session.commit()
    await snapshot_prices(session)

    out = await api.api_prices(session=session)
    assert out.current_value > 0
    assert out.movers.gainers and out.movers.gainers[0].name == "Aaa"
    assert out.profit_loss.winners and out.profit_loss.cost_basis == 1.0


# --- sets ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sets_list_detail_and_404(session):
    await _card(session, "Card One", 1, owned=1)
    await _card(session, "Card Two", 2, owned=0)
    listing = await api.api_sets(session=session)
    assert any(s.code == "tst" for s in listing)
    detail = await api.api_set_detail("tst", session=session)
    assert detail.total == 2
    with pytest.raises(HTTPException) as exc:
        await api.api_set_detail("zzz", session=session)
    assert exc.value.status_code == 404


# --- checklists ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_checklists_list_detail_and_404(session):
    from src.checklists import create_checklist
    await _card(session, "Owned Card", 1, owned=1)
    await create_checklist(session, "My List", "Owned Card\nMissing Card")
    listing = await api.api_checklists(session=session)
    assert listing[0].total == 2
    detail = await api.api_checklist_detail(listing[0].id, session=session)
    assert detail.owned_count == 1
    with pytest.raises(HTTPException) as exc:
        await api.api_checklist_detail(999999, session=session)
    assert exc.value.status_code == 404


# --- saved ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_saved_list(session):
    from src.models import SavedSearch
    session.add(SavedSearch(name="Reds", query="c:r", scope="all", sort="name", direction="asc"))
    await session.commit()
    rows = await api.api_saved(session=session)
    assert rows[0].name == "Reds" and rows[0].new_count == 0


# --- import --------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_import_stage_confirm_and_errors(session):
    from pathlib import Path
    csv = (Path(__file__).parent / "fixtures" / "manabox_sample.csv").read_text()
    for sid, name, setc, num in [
        ("00000000-0000-0000-0000-0000000000b1", "Black Lotus", "lea", "232"),
        ("00000000-0000-0000-0000-0000000000b2", "Lightning Bolt", "mh2", "122"),
    ]:
        session.add(Card(**card_to_columns(
            {"id": sid, "oracle_id": str(uuid.uuid4()), "name": name, "set": setc,
             "collector_number": num, "type_line": "X"})))
    await session.commit()

    preview = await api.api_import_stage(api.ImportIn(text=csv), session=session)
    assert preview.token and preview.matched_count >= 2
    result = await api.api_import_confirm(
        api.ImportConfirmIn(token=preview.token, strategy="increment"), session=session)
    assert result.inserted >= 2

    with pytest.raises(HTTPException) as e1:
        await api.api_import_stage(api.ImportIn(text="just text"), session=session)
    assert e1.value.status_code == 400
    with pytest.raises(HTTPException) as e2:
        await api.api_import_confirm(api.ImportConfirmIn(token="nope"), session=session)
    assert e2.value.status_code == 404
    with pytest.raises(HTTPException) as e3:
        await api.api_import_confirm(
            api.ImportConfirmIn(token=preview.token, strategy="bogus"), session=session)
    assert e3.value.status_code == 400
