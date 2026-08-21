"""Trade pools: staging, merging, valuation on the pool's own basis, and shortfalls (#331)."""

import uuid

import pytest
from sqlalchemy import select
from src.config import get_settings
from src.models import Card, CollectionCard, TradePool, TradePoolItem
from src.scryfall.mapping import card_to_columns
from src.trade_pool import (
    clear_pool,
    create_pool,
    delete_pool,
    open_pools,
    pool_summaries,
    pool_view,
    remove_item,
    stage_printing,
    stage_selection,
    stage_stack,
    update_item,
    update_pool,
)


async def _card(session, name, usd="1.00", eur=None, cn="1"):
    prices = {"usd": usd, "usd_foil": str(float(usd) * 3)}
    if eur:
        prices["eur"] = eur
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
           "collector_number": cn, "rarity": "rare", "prices": prices}
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.commit()
    return card


async def _stack(session, card, qty=1, finish="normal", condition=None, language="en"):
    stack = CollectionCard(scryfall_id=card.scryfall_id, quantity=qty, finish=finish,
                           condition=condition, language=language)
    session.add(stack)
    await session.commit()
    await session.refresh(stack)
    return stack


# --- staging -------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_stage_stack_inherits_the_copy_that_was_pointed_at(session):
    """The stack's finish/condition/language are what make it worth what it is."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=3, finish="foil", condition="NM", language="ja")

    item = await stage_stack(session, pool.id, stack.id, 2)
    assert item.direction == "out" and item.quantity == 2
    assert (item.finish, item.condition, item.language) == ("foil", "NM", "ja")
    # The stack id is kept only as a breadcrumb — nothing downstream addresses it.
    assert item.collection_card_id == stack.id


@pytest.mark.asyncio
async def test_staging_the_same_copy_twice_merges(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=9)

    await stage_stack(session, pool.id, stack.id, 2)
    await stage_stack(session, pool.id, stack.id, 3)
    items = list((await session.execute(select(TradePoolItem))).scalars().all())
    assert len(items) == 1 and items[0].quantity == 5


@pytest.mark.asyncio
async def test_different_copies_of_one_printing_stay_separate(session):
    """A foil and a non-foil of the same card are different goods, not the same line."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    plain = await _stack(session, card, qty=2)
    foil = await _stack(session, card, qty=1, finish="foil")

    await stage_stack(session, pool.id, plain.id, 1)
    await stage_stack(session, pool.id, foil.id, 1)
    assert len((await session.execute(select(TradePoolItem))).scalars().all()) == 2


@pytest.mark.asyncio
async def test_the_two_sides_are_independent(session):
    """Giving and getting the same printing is a legitimate trade (upgrading a copy)."""
    pool = await create_pool(session, "Upgrade")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=1)

    await stage_stack(session, pool.id, stack.id, 1)
    await stage_printing(session, pool.id, "in", card.scryfall_id, 1, finish="foil")
    view = await pool_view(session, await session.get(TradePool, pool.id))
    assert view.giving.cards == 1 and view.getting.cards == 1


@pytest.mark.asyncio
async def test_staging_rejects_unknown_pools_cards_and_directions(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    assert await stage_printing(session, 999, "out", card.scryfall_id) is None
    assert await stage_printing(session, pool.id, "out", uuid.uuid4()) is None
    assert await stage_printing(session, pool.id, "sideways", card.scryfall_id) is None
    assert await stage_stack(session, pool.id, 999) is None
    assert (await session.execute(select(TradePoolItem))).scalars().all() == []


# --- adjusting -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_quantity_zero_unstages(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    item = await stage_printing(session, pool.id, "in", card.scryfall_id, 2)
    assert await update_item(session, item.id, quantity=0) is True
    assert (await session.execute(select(TradePoolItem))).scalars().all() == []
    assert await remove_item(session, 999) is False


@pytest.mark.asyncio
async def test_re_keying_an_item_onto_an_existing_one_merges_them(session):
    """Marking a second line foil when a foil line already exists must not duplicate it."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    foil = await stage_printing(session, pool.id, "in", card.scryfall_id, 1, finish="foil")
    plain = await stage_printing(session, pool.id, "in", card.scryfall_id, 2)

    assert await update_item(session, plain.id, finish="foil") is True
    items = list((await session.execute(select(TradePoolItem))).scalars().all())
    assert len(items) == 1 and items[0].id == foil.id and items[0].quantity == 3


@pytest.mark.asyncio
async def test_update_item_normalizes_and_ignores_unknown_items(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    item = await stage_printing(session, pool.id, "in", card.scryfall_id, 1)
    await update_item(session, item.id, finish="holographic", condition="  ", language=" DE ")
    await session.refresh(item)
    assert (item.finish, item.condition, item.language) == ("normal", None, "de")
    assert await update_item(session, 999, quantity=1) is False


@pytest.mark.asyncio
async def test_clear_one_side_or_all(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=4)
    await stage_stack(session, pool.id, stack.id, 1)
    await stage_printing(session, pool.id, "in", card.scryfall_id, 1, finish="foil")

    assert await clear_pool(session, pool.id, "in") == 1
    view = await pool_view(session, await session.get(TradePool, pool.id))
    assert view.getting.rows == [] and view.giving.cards == 1
    assert await clear_pool(session, pool.id) == 1
    assert (await pool_view(session, await session.get(TradePool, pool.id))).is_empty


# --- reading -------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_values_use_the_pools_frozen_basis_not_the_viewers(session):
    """The pool was opened in EUR; reading it later must not silently switch to USD."""
    pool = await create_pool(session, "Friday", currency="eur")
    card = await _card(session, "Spare", usd="10.00", eur="4.00")
    await stage_printing(session, pool.id, "in", card.scryfall_id, 2)

    view = await pool_view(session, await session.get(TradePool, pool.id))
    assert view.getting.rows[0].unit == 4.00 and view.getting.value == 8.00


@pytest.mark.asyncio
async def test_foil_copies_are_valued_as_foils(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare", usd="10.00")
    await stage_printing(session, pool.id, "in", card.scryfall_id, 1, finish="foil")
    view = await pool_view(session, await session.get(TradePool, pool.id))
    assert view.getting.rows[0].unit == 30.00


@pytest.mark.asyncio
async def test_delta_favours_whoever_is_getting_more(session):
    pool = await create_pool(session, "Friday")
    cheap = await _card(session, "Cheap", usd="1.00", cn="1")
    dear = await _card(session, "Dear", usd="5.00", cn="2")
    stack = await _stack(session, cheap, qty=2)
    await stage_stack(session, pool.id, stack.id, 2)                      # give $2
    await stage_printing(session, pool.id, "in", dear.scryfall_id, 1)     # get $5

    view = await pool_view(session, await session.get(TradePool, pool.id))
    assert view.giving.value == 2.00 and view.getting.value == 5.00
    assert view.delta == 3.00


@pytest.mark.asyncio
async def test_shortfall_when_the_staged_copy_is_no_longer_owned(session):
    """A pool is a proposal: the collection can change under it, and that has to be visible."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=3)
    await stage_stack(session, pool.id, stack.id, 3)

    stack.quantity = 1
    await session.commit()
    view = await pool_view(session, await session.get(TradePool, pool.id))
    row = view.giving.rows[0]
    assert row.owned == 1 and row.short == 2
    assert view.shortfalls == [row]


@pytest.mark.asyncio
async def test_a_deleted_stack_leaves_the_item_standing(session):
    """The stack id is advisory — losing it must not lose the staged card (ADR 0002 D4)."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=2)
    await stage_stack(session, pool.id, stack.id, 2)

    await session.delete(stack)
    await session.commit()
    view = await pool_view(session, await session.get(TradePool, pool.id))
    assert view.giving.cards == 2 and view.giving.rows[0].short == 2


@pytest.mark.asyncio
async def test_incoming_rows_are_never_short(session):
    """You don't own what you're receiving; that isn't a shortfall."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    await stage_printing(session, pool.id, "in", card.scryfall_id, 4)
    view = await pool_view(session, await session.get(TradePool, pool.id))
    assert view.getting.rows[0].owned == 0 and view.shortfalls == []


# --- pools ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_pool_defaults_and_edits(session):
    pool = await create_pool(session, "   ", partner="  ")
    assert pool.name == "Untitled trade" and pool.partner is None and pool.status == "open"

    await update_pool(session, pool.id, name="Friday", partner="Dan", note="at the LGS")
    await session.refresh(pool)
    assert (pool.name, pool.partner, pool.note) == ("Friday", "Dan", "at the LGS")
    # A blank name is ignored rather than blanking the pool.
    await update_pool(session, pool.id, name="   ", status="closed")
    await session.refresh(pool)
    assert pool.name == "Friday" and pool.status == "closed"
    assert await update_pool(session, 999, name="x") is None


@pytest.mark.asyncio
async def test_only_open_pools_are_offered_for_staging(session):
    a = await create_pool(session, "Open one")
    b = await create_pool(session, "Done one")
    await update_pool(session, b.id, status="closed")
    assert [p.id for p in await open_pools(session)] == [a.id]


@pytest.mark.asyncio
async def test_summaries_count_both_sides(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=5)
    await stage_stack(session, pool.id, stack.id, 3)
    await stage_printing(session, pool.id, "in", card.scryfall_id, 2, finish="foil")

    empty = await create_pool(session, "Empty")
    summaries = {s.pool.id: s for s in await pool_summaries(session)}
    assert (summaries[pool.id].out_cards, summaries[pool.id].in_cards) == (3, 2)
    assert (summaries[empty.id].out_cards, summaries[empty.id].in_cards) == (0, 0)


@pytest.mark.asyncio
async def test_deleting_a_pool_leaves_the_collection_alone(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=2)
    await stage_stack(session, pool.id, stack.id, 2)

    assert await delete_pool(session, pool.id) is True
    assert (await session.execute(select(TradePoolItem))).scalars().all() == []
    assert (await session.get(CollectionCard, stack.id)).quantity == 2
    assert await delete_pool(session, 999) is False


# --- bulk staging from the results grid ----------------------------------------------------------

@pytest.mark.asyncio
async def test_bulk_out_stages_the_largest_owned_stack(session):
    """A bulk add means "the copies I have", so it picks the stack with the most in it."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    await _stack(session, card, qty=1, finish="foil")
    await _stack(session, card, qty=6, condition="LP")

    assert await stage_selection(session, pool.id, "out", [str(card.scryfall_id)]) == 1
    row = (await pool_view(session, await session.get(TradePool, pool.id))).giving.rows[0]
    assert (row.finish, row.condition) == ("normal", "LP")


@pytest.mark.asyncio
async def test_bulk_in_stages_a_plain_copy_and_bad_ids_are_skipped(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    staged = await stage_selection(
        session, pool.id, "in", [str(card.scryfall_id), "not-a-uuid", str(uuid.uuid4())]
    )
    assert staged == 1
    row = (await pool_view(session, await session.get(TradePool, pool.id))).getting.rows[0]
    assert (row.finish, row.language, row.owned) == ("normal", "en", 0)


@pytest.mark.asyncio
async def test_bulk_out_of_an_unowned_printing_shows_as_short(session):
    """Selecting a card you don't own is staged honestly rather than silently dropped."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Unowned")
    assert await stage_selection(session, pool.id, "out", [str(card.scryfall_id)]) == 1
    view = await pool_view(session, await session.get(TradePool, pool.id))
    assert view.giving.rows[0].short == 1


# --- routes --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_create_view_and_discard_a_pool_over_http(client, session):
    resp = await client.post("/trade/pools", data={"name": "Friday", "partner": "Dan"})
    assert resp.status_code == 303 and resp.headers["location"].startswith("/trade/pool/")
    pool_id = int(resp.headers["location"].rsplit("/", 1)[1])

    page = await client.get(f"/trade/pool/{pool_id}")
    assert page.status_code == 200
    assert "Friday" in page.text and "Dan" in page.text
    assert (await client.get("/trade/pool/999")).status_code == 404

    assert (await client.post(f"/trade/pool/{pool_id}/delete")).status_code == 303
    assert await session.get(TradePool, pool_id) is None


@pytest.mark.asyncio
async def test_pool_page_shows_both_sides_and_the_difference(client, session):
    pool = await create_pool(session, "Friday")
    give = await _card(session, "Given Away", usd="1.00", cn="1")
    get = await _card(session, "Received", usd="5.00", cn="2")
    stack = await _stack(session, give, qty=1)
    await stage_stack(session, pool.id, stack.id, 1)
    await stage_printing(session, pool.id, "in", get.scryfall_id, 1)

    page = (await client.get(f"/trade/pool/{pool.id}")).text
    assert "Given Away" in page and "Received" in page
    assert "+$4.00" in page and "in your favour" in page


@pytest.mark.asyncio
async def test_a_losing_trade_reads_as_minus_dollars_not_dollars_minus(client, session):
    """The sign belongs outside the currency symbol — "−$4.00", never "$-4.00"."""
    pool = await create_pool(session, "Friday")
    give = await _card(session, "Given Away", usd="5.00", cn="1")
    get = await _card(session, "Received", usd="1.00", cn="2")
    stack = await _stack(session, give, qty=1)
    await stage_stack(session, pool.id, stack.id, 1)
    await stage_printing(session, pool.id, "in", get.scryfall_id, 1)

    page = (await client.get(f"/trade/pool/{pool.id}")).text
    assert "\u2212$4.00" in page and "$-4.00" not in page
    assert "in theirs" in page


@pytest.mark.asyncio
async def test_staging_and_unstaging_over_http(client, session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    await client.post(f"/trade/pool/{pool.id}/stage",
                      data={"scryfall_id": str(card.scryfall_id), "direction": "in",
                            "quantity": 2, "finish": "foil"})
    item = (await session.execute(select(TradePoolItem))).scalar_one()
    assert item.quantity == 2 and item.finish == "foil"

    await client.post(f"/trade/pool/{pool.id}/items/{item.id}",
                      data={"quantity": 5, "finish": "normal", "condition": "NM",
                            "language": "en"})
    await session.refresh(item)
    assert item.quantity == 5 and item.finish == "normal" and item.condition == "NM"

    await client.post(f"/trade/pool/{pool.id}/items/{item.id}/delete")
    assert (await session.execute(select(TradePoolItem))).scalars().all() == []


@pytest.mark.asyncio
async def test_clear_and_edit_pool_over_http(client, session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    await stage_printing(session, pool.id, "in", card.scryfall_id, 1)

    assert (await client.post(f"/trade/pool/{pool.id}/clear",
                              data={"direction": "in"})).status_code == 303
    assert (await session.execute(select(TradePoolItem))).scalars().all() == []

    await client.post(f"/trade/pool/{pool.id}/update",
                      data={"name": "Saturday", "partner": "", "note": "", "status": "closed"})
    await session.refresh(pool)
    assert pool.name == "Saturday" and pool.status == "closed"
    assert (await client.post("/trade/pool/999/update", data={"name": "x"})).status_code == 404


@pytest.mark.asyncio
async def test_stage_from_the_card_page(client, session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=2, finish="foil")

    resp = await client.post(f"/collection/stack/{stack.id}/trade",
                             data={"pool_id": str(pool.id), "quantity": 1})
    assert resp.status_code == 200 and "card-collection" in resp.text
    item = (await session.execute(select(TradePoolItem))).scalar_one()
    assert item.direction == "out" and item.finish == "foil"

    # The picker's placeholder posts an empty pool id — a no-op, not a 422.
    assert (await client.post(f"/collection/stack/{stack.id}/trade",
                              data={"pool_id": ""})).status_code == 200
    assert len((await session.execute(select(TradePoolItem))).scalars().all()) == 1
    assert (await client.post("/collection/stack/999/trade",
                              data={"pool_id": str(pool.id)})).status_code == 404


@pytest.mark.asyncio
async def test_the_card_page_offers_open_pools(client, session):
    card = await _card(session, "Spare")
    await _stack(session, card, qty=1)
    await create_pool(session, "Friday")
    page = (await client.get(f"/card/{card.scryfall_id}")).text
    assert "🤝 trade…" in page and "Friday" in page


@pytest.mark.asyncio
async def test_bulk_staging_from_the_results_grid(client, session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    await _stack(session, card, qty=3)

    for action, direction, expected in (("trade_out", "out", 1), ("trade_in", "in", 1)):
        resp = await client.post("/collection/bulk", data={
            "bulk_action": action, "scryfall_ids": [str(card.scryfall_id)],
            "pool_id": str(pool.id), "q": "", "scope": "collection",
        })
        assert resp.status_code == 303
        items = (await session.execute(
            select(TradePoolItem).where(TradePoolItem.direction == direction)
        )).scalars().all()
        assert len(items) == expected

    # A non-numeric pool id from a hand-rolled form is ignored rather than crashing.
    assert (await client.post("/collection/bulk", data={
        "bulk_action": "trade_out", "scryfall_ids": [str(card.scryfall_id)], "pool_id": "x",
    })).status_code == 303


@pytest.mark.asyncio
async def test_pools_are_listed_on_the_trade_tab(client, session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    await stage_printing(session, pool.id, "in", card.scryfall_id, 2)
    page = (await client.get("/collection?tab=trade")).text
    assert "Friday" in page and "Start a trade" in page


@pytest.mark.asyncio
async def test_writes_are_blocked_read_only(client, session, monkeypatch):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=1)
    monkeypatch.setattr(get_settings(), "read_only", True)

    for url, data in (
        ("/trade/pools", {"name": "x"}),
        (f"/trade/pool/{pool.id}/update", {"name": "x"}),
        (f"/trade/pool/{pool.id}/delete", {}),
        (f"/trade/pool/{pool.id}/clear", {}),
        (f"/trade/pool/{pool.id}/stage", {"scryfall_id": str(card.scryfall_id)}),
        (f"/trade/pool/{pool.id}/items/1", {"quantity": 1}),
        (f"/trade/pool/{pool.id}/items/1/delete", {}),
        (f"/collection/stack/{stack.id}/trade", {"pool_id": str(pool.id)}),
    ):
        assert (await client.post(url, data=data)).status_code == 403, url
    # Reading a pool still works, so a demo instance can show one.
    assert (await client.get(f"/trade/pool/{pool.id}")).status_code == 200
