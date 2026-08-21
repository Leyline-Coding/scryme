"""Settling a trade: card-level atomicity, partial trades, and an honest report (#332).

The demanding requirement here comes from ADR 0002 (D3) rather than from the local feature: apply
a set of in/out moves atomically *at the card level*, reporting exactly which ones applied. These
tests pin that behaviour, because the cross-device commit will be built on it.
"""

import uuid

import pytest
from sqlalchemy import func, select
from src.config import get_settings
from src.models import Card, CollectionCard, TradePool, TradePoolItem
from src.scryfall.mapping import card_to_columns
from src.trade_commit import commit_trade, outstanding_items
from src.trade_pool import (
    create_pool,
    pool_view,
    remove_item,
    stage_printing,
    stage_stack,
    update_item,
)


async def _card(session, name, usd="1.00", cn="1"):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
           "collector_number": cn, "rarity": "rare", "prices": {"usd": usd}}
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


async def _owned(session, card, **kw):
    """Total copies of a card in the collection, optionally narrowed to one copy kind."""
    stmt = select(func.coalesce(func.sum(CollectionCard.quantity), 0)).where(
        CollectionCard.scryfall_id == card.scryfall_id
    )
    for col, value in kw.items():
        stmt = stmt.where(getattr(CollectionCard, col) == value)
    return int(await session.scalar(stmt))


# --- the happy path ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_settling_moves_cards_both_ways(session):
    pool = await create_pool(session, "Friday")
    give = await _card(session, "Given Away", cn="1")
    get = await _card(session, "Received", cn="2")
    stack = await _stack(session, give, qty=3)
    await stage_stack(session, pool.id, stack.id, 2)
    await stage_printing(session, pool.id, "in", get.scryfall_id, 1)

    result = await commit_trade(session, pool.id)
    assert result.ok and result.completed
    assert (result.moved_out, result.moved_in) == (2, 1)
    assert await _owned(session, give) == 1
    assert await _owned(session, get) == 1


@pytest.mark.asyncio
async def test_a_settled_pool_closes_and_is_stamped(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=1)
    await stage_stack(session, pool.id, stack.id, 1)

    await commit_trade(session, pool.id)
    await session.refresh(pool)
    assert pool.status == "closed" and pool.committed_at is not None
    assert await outstanding_items(session, pool.id) == []


@pytest.mark.asyncio
async def test_incoming_cards_inherit_the_traded_copys_attributes(session):
    """A foil Japanese card traded in must not land in the collection as a plain English one."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Received")
    await stage_printing(session, pool.id, "in", card.scryfall_id, 2,
                         finish="foil", condition="LP", language="ja")

    await commit_trade(session, pool.id)
    stack = (await session.execute(select(CollectionCard))).scalar_one()
    assert (stack.quantity, stack.finish, stack.condition, stack.language) == (2, "foil", "LP",
                                                                              "ja")


@pytest.mark.asyncio
async def test_incoming_cards_can_be_filed_on_the_way_in(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Received")
    await stage_printing(session, pool.id, "in", card.scryfall_id, 1)

    await commit_trade(session, pool.id, binder="Trades", location="Shoebox")
    stack = (await session.execute(select(CollectionCard))).scalar_one()
    assert stack.binder_name == "Trades" and stack.location == "Shoebox"


@pytest.mark.asyncio
async def test_incoming_merges_into_an_existing_stack(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Received")
    await _stack(session, card, qty=1)
    await stage_printing(session, pool.id, "in", card.scryfall_id, 2)

    await commit_trade(session, pool.id)
    assert len((await session.execute(select(CollectionCard))).scalars().all()) == 1
    assert await _owned(session, card) == 3


# --- re-resolution rather than row ids (ADR 0002 D4) ----------------------------------------------

@pytest.mark.asyncio
async def test_outgoing_is_resolved_by_copy_kind_not_by_the_staged_stack(session):
    """The staged stack is deleted and the copies re-filed elsewhere; the trade still settles."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=2)
    await stage_stack(session, pool.id, stack.id, 2)

    await session.delete(stack)
    await session.commit()
    replacement = CollectionCard(scryfall_id=card.scryfall_id, quantity=2, finish="normal",
                                 language="en", binder_name="Elsewhere")
    session.add(replacement)
    await session.commit()

    result = await commit_trade(session, pool.id)
    assert result.ok and await _owned(session, card) == 0


@pytest.mark.asyncio
async def test_outgoing_only_takes_the_copy_kind_that_was_staged(session):
    """Trading away your plain copy must never quietly consume the foil."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    plain = await _stack(session, card, qty=1)
    await _stack(session, card, qty=1, finish="foil")
    await stage_stack(session, pool.id, plain.id, 1)

    await commit_trade(session, pool.id)
    assert await _owned(session, card, finish="normal") == 0
    assert await _owned(session, card, finish="foil") == 1


@pytest.mark.asyncio
async def test_outgoing_draws_from_the_smallest_stack_first(session):
    """Use up the odd single before breaking into a bigger stack."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    small = await _stack(session, card, qty=1, language="en")
    big = CollectionCard(scryfall_id=card.scryfall_id, quantity=5, finish="normal", language="en",
                         binder_name="Bulk")
    session.add(big)
    await session.commit()
    await stage_stack(session, pool.id, small.id, 1)

    await commit_trade(session, pool.id)
    assert await session.get(CollectionCard, small.id) is None
    await session.refresh(big)
    assert big.quantity == 5


@pytest.mark.asyncio
async def test_an_emptied_stack_is_deleted_not_left_at_zero(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=2)
    await stage_stack(session, pool.id, stack.id, 2)

    await commit_trade(session, pool.id)
    assert await session.get(CollectionCard, stack.id) is None


# --- partial and failed lines ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_short_line_moves_what_it_can_and_leaves_the_rest_staged(session):
    """Card-level atomicity: fewer copies than agreed is a partial line, not a failed trade."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=3)
    await stage_stack(session, pool.id, stack.id, 3)
    stack.quantity = 1                      # two were sold after staging
    await session.commit()

    result = await commit_trade(session, pool.id)
    line = result.lines[0]
    assert line.status == "partial" and line.applied == 1 and line.short == 2
    assert not result.ok and not result.completed
    assert await _owned(session, card) == 0
    # The outstanding two stay staged, so the discrepancy is visible rather than absorbed.
    item = (await session.execute(select(TradePoolItem))).scalar_one()
    assert item.quantity == 3 and item.applied_quantity == 1


@pytest.mark.asyncio
async def test_a_line_with_nothing_on_hand_fails_without_touching_the_others(session):
    pool = await create_pool(session, "Friday")
    have = await _card(session, "On Hand", cn="1")
    gone = await _card(session, "Sold Already", cn="2")
    stack = await _stack(session, have, qty=1)
    await stage_stack(session, pool.id, stack.id, 1)
    await stage_printing(session, pool.id, "out", gone.scryfall_id, 1)

    result = await commit_trade(session, pool.id)
    by_name = {line.name: line for line in result.lines}
    assert by_name["On Hand"].status == "applied"
    assert by_name["Sold Already"].status == "failed" and by_name["Sold Already"].applied == 0
    assert await _owned(session, have) == 0   # the good line still went through
    assert len(result.problems) == 1


@pytest.mark.asyncio
async def test_settling_the_rest_later_finishes_the_trade(session):
    """The follow-up commit picks up exactly what was left outstanding."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=3)
    await stage_stack(session, pool.id, stack.id, 3)
    stack.quantity = 1
    await session.commit()
    await commit_trade(session, pool.id)          # 1 of 3

    stack2 = CollectionCard(scryfall_id=card.scryfall_id, quantity=2, finish="normal",
                            language="en")
    session.add(stack2)
    await session.commit()

    result = await commit_trade(session, pool.id)  # the remaining 2
    assert result.ok and result.completed and result.lines[0].requested == 2
    await session.refresh(pool)
    assert pool.status == "closed"


# --- committing a subset (a trade that falls through mid-session) ---------------------------------

@pytest.mark.asyncio
async def test_only_the_chosen_lines_are_settled(session):
    pool = await create_pool(session, "Friday")
    a = await _card(session, "Agreed", cn="1")
    b = await _card(session, "Backed Out", cn="2")
    sa = await _stack(session, a, qty=1)
    sb = await _stack(session, b, qty=1)
    item_a = await stage_stack(session, pool.id, sa.id, 1)
    await stage_stack(session, pool.id, sb.id, 1)

    result = await commit_trade(session, pool.id, [item_a.id])
    assert len(result.lines) == 1 and result.lines[0].name == "Agreed"
    assert await _owned(session, a) == 0 and await _owned(session, b) == 1
    # The trade isn't finished, so it stays open with the other card still staged.
    await session.refresh(pool)
    assert pool.status == "open" and pool.committed_at is None
    assert len(await outstanding_items(session, pool.id)) == 1


@pytest.mark.asyncio
async def test_committing_an_empty_pool_changes_nothing(session):
    pool = await create_pool(session, "Friday")
    result = await commit_trade(session, pool.id)
    assert result.lines == [] and not result.completed and not result.ok
    await session.refresh(pool)
    assert pool.status == "open"
    assert await commit_trade(session, 999) is None


# --- the pool keeps an honest record afterwards ---------------------------------------------------

@pytest.mark.asyncio
async def test_applied_copies_cannot_be_unstaged(session):
    """Unstaging a settled line would erase the record of cards that really moved."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=3)
    item = await stage_stack(session, pool.id, stack.id, 3)
    stack.quantity = 1
    await session.commit()
    await commit_trade(session, pool.id)          # 1 of 3 applied

    assert await remove_item(session, item.id) is True
    await session.refresh(item)
    assert item.quantity == 1 and item.applied_quantity == 1   # trimmed, not deleted
    assert (await session.execute(select(TradePoolItem))).scalar_one() is not None


@pytest.mark.asyncio
async def test_quantity_cannot_be_set_below_what_already_moved(session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=2)
    item = await stage_stack(session, pool.id, stack.id, 2)
    await commit_trade(session, pool.id, [item.id])

    await update_item(session, item.id, quantity=1)
    await session.refresh(item)
    assert item.quantity == 2 and item.applied_quantity == 2


@pytest.mark.asyncio
async def test_the_view_reports_progress_and_stops_calling_moved_cards_short(session):
    """Cards already given away are gone *because* the trade moved them — not a discrepancy."""
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=2)
    item = await stage_stack(session, pool.id, stack.id, 2)
    await commit_trade(session, pool.id, [item.id])

    view = await pool_view(session, await session.get(TradePool, pool.id))
    row = view.giving.rows[0]
    assert row.applied == 2 and row.outstanding == 0 and row.short == 0
    assert view.settled and view.shortfalls == []


# --- routes ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_review_page_lists_what_would_move(client, session):
    pool = await create_pool(session, "Friday")
    give = await _card(session, "Given Away", cn="1")
    get = await _card(session, "Received", cn="2")
    stack = await _stack(session, give, qty=1)
    await stage_stack(session, pool.id, stack.id, 1)
    await stage_printing(session, pool.id, "in", get.scryfall_id, 1)

    page = (await client.get(f"/trade/pool/{pool.id}/commit")).text
    assert "Given Away" in page and "Received" in page
    assert "Leaving your collection" in page and "Joining your collection" in page
    assert "File the incoming cards" in page   # intake only shown when something is incoming
    assert (await client.get("/trade/pool/999/commit")).status_code == 404


@pytest.mark.asyncio
async def test_review_page_when_there_is_nothing_left_to_settle(client, session):
    pool = await create_pool(session, "Friday")
    assert "Nothing is staged" in (await client.get(f"/trade/pool/{pool.id}/commit")).text

    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=1)
    await stage_stack(session, pool.id, stack.id, 1)
    await commit_trade(session, pool.id)
    assert "already moved" in (await client.get(f"/trade/pool/{pool.id}/commit")).text


@pytest.mark.asyncio
async def test_settling_over_http_reports_what_happened(client, session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=1)
    item = await stage_stack(session, pool.id, stack.id, 1)

    resp = await client.post(f"/trade/pool/{pool.id}/commit",
                             data={"item_ids": [str(item.id)]})
    assert resp.status_code == 200
    assert "Trade settled" in resp.text and "1 card(s) left your collection" in resp.text
    assert await _owned(session, card) == 0
    assert (await client.post("/trade/pool/999/commit")).status_code == 404


@pytest.mark.asyncio
async def test_settling_a_short_trade_over_http_says_so(client, session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=2)
    await stage_stack(session, pool.id, stack.id, 2)
    stack.quantity = 1
    await session.commit()

    page = (await client.post(f"/trade/pool/{pool.id}/commit")).text
    assert "Partly settled" in page and "1 left staged" in page
    assert "still staged" in page and "Settle the rest" in page


@pytest.mark.asyncio
async def test_settling_only_some_lines_does_not_claim_the_trade_is_done(client, session):
    """Every chosen line applied, but cards are still staged — that is not "Trade settled"."""
    pool = await create_pool(session, "Friday")
    a = await _card(session, "Agreed", cn="1")
    b = await _card(session, "Later", cn="2")
    sa = await _stack(session, a, qty=1)
    sb = await _stack(session, b, qty=1)
    item_a = await stage_stack(session, pool.id, sa.id, 1)
    await stage_stack(session, pool.id, sb.id, 1)

    page = (await client.post(f"/trade/pool/{pool.id}/commit",
                              data={"item_ids": [str(item_a.id)]})).text
    assert "Cards moved" in page and "Trade settled" not in page
    assert "Settle the rest" in page   # offered even though nothing went wrong
    assert await _owned(session, a) == 0 and await _owned(session, b) == 1


@pytest.mark.asyncio
async def test_the_pool_page_offers_and_then_stops_offering_settling(client, session):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=1)
    await stage_stack(session, pool.id, stack.id, 1)

    assert "Settle this trade" in (await client.get(f"/trade/pool/{pool.id}")).text
    await commit_trade(session, pool.id)
    page = (await client.get(f"/trade/pool/{pool.id}")).text
    assert "Every card in this trade has moved" in page and "Settle this trade →" not in page
    assert "settled" in (await client.get("/collection?tab=trade")).text


@pytest.mark.asyncio
async def test_settling_is_blocked_read_only(client, session, monkeypatch):
    pool = await create_pool(session, "Friday")
    card = await _card(session, "Spare")
    stack = await _stack(session, card, qty=1)
    await stage_stack(session, pool.id, stack.id, 1)
    monkeypatch.setattr(get_settings(), "read_only", True)

    assert (await client.post(f"/trade/pool/{pool.id}/commit")).status_code == 403
    # The review page still renders, but without a way to settle.
    page = (await client.get(f"/trade/pool/{pool.id}/commit")).text
    assert "read-only" in page and "Settle this trade" not in page
    assert await _owned(session, card) == 1
