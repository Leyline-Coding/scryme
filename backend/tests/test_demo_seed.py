"""Curated demo seed (#larger-demo): colour/price selection, banned/restricted, 2019 dating."""

import datetime
import uuid

import pytest
from sqlalchemy import func, select
from src.demo import seed_demo
from src.models import (
    Card,
    CardPricePoint,
    Checklist,
    CollectionCard,
    PriceSnapshot,
    WishlistItem,
)


async def _card(session, *, colors, usd, legalities=None, oracle_text=None,
                finishes=None, foil=False, released=None):
    card = Card(
        scryfall_id=uuid.uuid4(),
        oracle_id=uuid.uuid4(),
        name=f"Demo {uuid.uuid4().hex[:6]}",
        set_code="tst",
        collector_number="1",
        colors=colors,
        color_identity=colors,
        oracle_text=oracle_text,
        released_at=released,
        prices={"usd": usd} if usd else {},
        legalities=legalities or {},
        raw={"name": "Demo", "foil": foil, "finishes": finishes or ["nonfoil"]},
    )
    session.add(card)
    await session.flush()
    return card


@pytest.mark.asyncio
async def test_curated_demo_seed(session):
    red_hi = await _card(session, colors=["R"], usd="9.00")
    red_lo = await _card(session, colors=["R"], usd="2.00")
    await _card(session, colors=["U"], usd="7.50")
    await _card(session, colors=[], usd="20.00")          # colorless
    await _card(session, colors=["R", "G"], usd="3.00")   # multicolor
    banned = await _card(session, colors=["B"], usd="40.00", legalities={"modern": "banned"})
    restricted = await _card(session, colors=["U"], usd="3000.00",
                             legalities={"vintage": "restricted"})
    future = await _card(session, colors=["W"], usd="5.00",
                         released=datetime.date(2099, 1, 1))  # unreleased — never owned
    await session.commit()

    added = await seed_demo()
    assert added >= 7

    owned = set(await session.scalars(select(CollectionCard.scryfall_id)))
    assert {red_hi.scryfall_id, red_lo.scryfall_id, banned.scryfall_id,
            restricted.scryfall_id} <= owned
    assert future.scryfall_id not in owned  # a not-yet-released printing is skipped

    rows = list((await session.execute(select(CollectionCard))).scalars())
    assert all(r.source_format == "demo" for r in rows)
    # Ownership spread from 2005-10-08 to today (test cards have no release date to clamp against).
    assert all(2005 <= r.added_at.year <= datetime.date.today().year for r in rows)
    assert any(r.purchase_price for r in rows)  # priced cards get an acquisition price

    # Synthesized price history (monthly since 2005) + movers points for the read-only demo.
    assert (await session.scalar(select(func.count()).select_from(PriceSnapshot))) > 12
    assert (await session.scalar(select(func.count()).select_from(CardPricePoint))) > 0


@pytest.mark.asyncio
async def test_finishes_reflect_availability(session, monkeypatch):
    import src.demo as demo
    monkeypatch.setattr(demo, "_FOIL_FRACTION", 1.0)
    monkeypatch.setattr(demo, "_ETCHED_FRACTION", 1.0)

    foilc = await _card(session, colors=["R"], usd="1", foil=True,
                        finishes=["nonfoil", "foil"])
    etchc = await _card(session, colors=["U"], usd="1", foil=True,
                        finishes=["nonfoil", "foil", "etched"])
    plain = await _card(session, colors=["G"], usd="1", finishes=["nonfoil"])
    for c in (foilc, etchc, plain):
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1,
                                   source_format="demo", finish="normal"))
    await session.commit()

    await demo._seed_finishes(session)
    finish = {r.scryfall_id: r.finish
              for r in (await session.execute(select(CollectionCard))).scalars()}
    assert finish[foilc.scryfall_id] == "foil"
    assert finish[etchc.scryfall_id] == "etched"   # etched takes precedence over foil
    assert finish[plain.scryfall_id] == "normal"   # no finish it doesn't offer


@pytest.mark.asyncio
async def test_demo_showcase_data(session):
    from src.demo import _seed_showcase

    async def _own(card):
        session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1, source_format="demo"))

    removal = await _card(session, colors=["B"], usd="1.00",
                          oracle_text="Destroy target creature.")
    wipe = await _card(session, colors=["W"], usd="1.00",
                       oracle_text="Destroy all creatures.")
    plain = await _card(session, colors=["G"], usd="1.00", oracle_text="Draw a card.")
    for c in (removal, wipe, plain):
        await _own(c)
    # A pricey card left UNOWNED so it lands on the wishlist.
    pricey = await _card(session, colors=["U"], usd="500.00", oracle_text="Win the game.")
    await session.commit()

    await _seed_showcase(session)

    tags_by_sid = {
        r.scryfall_id: set(r.tags or [])
        for r in (await session.execute(select(CollectionCard))).scalars()
    }
    assert "removal" in tags_by_sid[removal.scryfall_id]
    assert "boardwipe" in tags_by_sid[wipe.scryfall_id]
    assert "removal" not in tags_by_sid[plain.scryfall_id]
    # Trade list: at least one owned card flagged for-trade.
    assert any("for-trade" in t for t in tags_by_sid.values())
    # Wishlist: the unowned pricey card.
    wished = set(await session.scalars(select(WishlistItem.scryfall_id)))
    assert pricey.scryfall_id in wished
    # Checklists seeded.
    names = set(await session.scalars(select(Checklist.name)))
    assert {"Commander Staples", "Original Dual Lands"} <= names


@pytest.mark.asyncio
async def test_seed_is_idempotent(session):
    await _card(session, colors=["R"], usd="6.00")
    await session.commit()
    assert await seed_demo() >= 1
    # A second run re-evaluates (the tiny collection is under the skip guard) but must not
    # re-add cards it already owns.
    before = await session.scalar(select(func.count()).select_from(CollectionCard))
    await seed_demo()
    after = await session.scalar(select(func.count()).select_from(CollectionCard))
    assert after == before
