"""Coverage tests for src/valuation.py: value overrides and multi-stack aggregation."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.valuation import valuation_report


async def _card(session, name, usd, *, set_code="tst", cn="1", rarity="rare"):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": set_code, "collector_number": cn, "rarity": rarity, "prices": {"usd": usd}}
    ))
    session.add(c)
    await session.flush()
    return c


@pytest.mark.asyncio
async def test_valuation_report_empty(session):
    r = await valuation_report(session, "usd")
    assert r.is_empty
    assert r.total_cards == 0 and r.total_value == 0.0


@pytest.mark.asyncio
async def test_value_override_wins_over_market(session):
    c = await _card(session, "Graded Mox", "10.00")
    # A manual override (e.g. a graded card) overrides market for the whole stack.
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, finish="normal",
                               value_override=300.00))
    await session.commit()

    r = await valuation_report(session, "usd")
    assert r.total_cards == 2
    assert r.total_value == pytest.approx(300.00)   # override, not 2 * 10
    assert r.top_cards[0].name == "Graded Mox"
    # unit = override / qty = 150; stack value = 300
    assert r.top_cards[0].unit == pytest.approx(150.00)


@pytest.mark.asyncio
async def test_multiple_stacks_same_printing_aggregate(session):
    c = await _card(session, "Dual Land", "8.00")
    # Two stacks of the same printing (different finishes) -> _update_best "existing" branch.
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="normal"))
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=3, finish="foil"))
    await session.commit()

    r = await valuation_report(session, "usd")
    assert r.printings == 1
    assert r.total_cards == 4
    # best card aggregates quantity across the two stacks
    top = r.top_cards[0]
    assert top.name == "Dual Land"
    assert top.quantity == 4


@pytest.mark.asyncio
async def test_override_with_zero_quantity(session):
    """A zero-qty stack with an override exercises the ``override/qty if qty else override``
    fallback (no ZeroDivisionError)."""
    c = await _card(session, "Empty Stack", "5.00")
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=0, finish="normal",
                               value_override=50.00))
    await session.commit()
    r = await valuation_report(session, "usd")
    # qty 0 contributes 0 cards but the override value is still added.
    assert r.total_cards == 0
    assert r.total_value == pytest.approx(50.00)
