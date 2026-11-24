"""Coverage for src/demo.py helpers + guard/edge branches not hit by the happy-path seed test."""

import datetime
import random
import uuid

import pytest
from sqlalchemy import func, select
from src.demo import (
    _collect_rows,
    _ensure_status,
    _seed_price_history,
    _take,
    seed_demo,
    seed_demo_decks,
)
from src.models import Card, CollectionCard, PriceSnapshot


async def _card(session, *, colors=None, usd=None, legalities=None, released=None):
    card = Card(
        scryfall_id=uuid.uuid4(),
        oracle_id=uuid.uuid4(),
        name=f"Demo {uuid.uuid4().hex[:6]}",
        set_code="tst",
        collector_number="1",
        color_identity=colors or [],
        prices={"usd": usd} if usd else {},
        legalities=legalities or {},
        released_at=released,
        raw={"name": "Demo"},
    )
    session.add(card)
    await session.flush()
    return card


def test_collect_rows_dedupes_and_stops_at_limit():
    used: set = set()
    out: list = []
    rows = [
        ("s1", "o1", "1.0"),
        ("s2", "o1", "2.0"),   # duplicate oracle -> skipped
        ("s3", "o3", None),    # priceless -> 0.0
        ("s4", "o4", "5.0"),   # never reached: limit hit first
    ]
    added = _collect_rows(rows, 2, used, out)
    assert added == 2
    assert out == [("s1", 1.0), ("s3", 0.0)]


@pytest.mark.asyncio
async def test_take_zero_count_is_noop(session):
    out: list = []
    await _take(session, Card.color_identity == ["R"], 0, set(), out)
    assert out == []


@pytest.mark.asyncio
async def test_take_small_count_fills_from_below_split_band(session):
    # count=1 -> "above $5" band wants 0 (need<=0 -> continue), below-split band fills it.
    await _card(session, colors=["R"], usd="2.00", released=datetime.date(2010, 1, 1))
    out: list = []
    await _take(session, Card.color_identity == ["R"], 1, set(), out)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_ensure_status_adds_and_counts_existing(session):
    banned = await _card(session, colors=["B"], usd="4.00",
                         legalities={"modern": "banned"}, released=datetime.date(2015, 1, 1))
    # Fresh `used` -> the card is added via the else branch.
    out: list = []
    await _ensure_status(session, "modern", "banned", 1, set(), out)
    assert len(out) == 1

    # Pre-owned key -> counts toward the guarantee without re-adding.
    out2: list = []
    await _ensure_status(session, "modern", "banned", 1, {banned.oracle_id}, out2)
    assert out2 == []


@pytest.mark.asyncio
async def test_seed_price_history_skips_priceless_cards(session):
    priced = await _card(session, colors=["R"], usd="10.00")
    priceless = await _card(session, colors=["G"], usd=None)
    when = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    for c in (priced, priceless):
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1,
                                   source_format="demo", added_at=when))
    await session.commit()

    await _seed_price_history(session, random.Random(1))
    # Monthly snapshots were created (many months from 2005 to now).
    snaps = await session.scalar(select(func.count()).select_from(PriceSnapshot))
    assert snaps > 12


@pytest.mark.asyncio
async def test_seed_demo_skips_when_guarded(session, monkeypatch):
    import src.demo as demo
    monkeypatch.setattr(demo, "_SEED_GUARD", 1)
    card = await _card(session, colors=["R"], usd="1.00")
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1, source_format="demo"))
    await session.commit()
    # Already at/above the (patched) guard -> skip without adding.
    assert await seed_demo() == 0


@pytest.mark.asyncio
async def test_seed_demo_decks_creates_then_skips(session):
    first = await seed_demo_decks()
    assert first == 3  # heavenly_inferno / elves / goblins
    second = await seed_demo_decks()
    assert second == 0  # all already exist -> skipped


# --- featured sets --------------------------------------------------------------------------------

async def _set_card(session, code, *, oracle=None, rarity="common", finishes=("nonfoil",),
                    released=datetime.date(2026, 10, 2), usd="2.00"):
    card = Card(
        scryfall_id=uuid.uuid4(), oracle_id=oracle or uuid.uuid4(),
        name=f"Featured {uuid.uuid4().hex[:6]}", set_code=code, collector_number="1",
        color_identity=[], prices={"usd": usd} if usd else {}, legalities={}, rarity=rarity,
        released_at=released, raw={"finishes": list(finishes)},
    )
    session.add(card)
    await session.flush()
    return card


async def _owned_in(session, code):
    return (await session.execute(
        select(CollectionCard).join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
        .where(Card.set_code == code)
    )).scalars().all()


@pytest.mark.asyncio
async def test_featured_sets_cover_the_requested_releases():
    from src.demo import FEATURED_SETS

    assert FEATURED_SETS == {
        "The Hobbit": ("hob", "hoc"),
        "Reality Fracture": ("fra", "frc", "pfra"),
        "Mystery Booster Commander Edition": ("mbc",),
        "Star Trek": ("trk", "trc", "sds"),
    }


@pytest.mark.asyncio
async def test_featured_sets_add_half_of_each_set_including_sub_sets(session):
    from src.demo import seed_demo_featured_sets

    for _ in range(10):
        await _set_card(session, "trk")
    for _ in range(6):
        await _set_card(session, "trc")       # Star Trek Commander, a sub-set
    for _ in range(4):
        await _set_card(session, "ttrk")      # tokens: not featured
    await session.commit()

    added = await seed_demo_featured_sets()

    assert added["Star Trek"] == 8            # 5 of 10 + 3 of 6
    assert added["The Hobbit"] == 0           # not in the card database yet: skipped, no error
    assert len(await _owned_in(session, "trk")) == 5
    assert len(await _owned_in(session, "trc")) == 3
    assert await _owned_in(session, "ttrk") == []
    stacks = await _owned_in(session, "trk")
    assert all(s.source_format == "demo" and s.quantity == 1 for s in stacks)
    now = datetime.datetime.now(datetime.UTC)
    assert all(datetime.datetime(2026, 10, 2, tzinfo=datetime.UTC) < s.added_at <= now
               for s in stacks)
    # The new total is recorded, since the scheduler does not run in a read-only demo.
    assert await session.scalar(select(func.count()).select_from(PriceSnapshot)) == 1


@pytest.mark.asyncio
async def test_featured_sets_are_idempotent_and_top_up_after_a_later_ingest(session):
    from src.demo import seed_demo_featured_sets

    for _ in range(10):
        await _set_card(session, "hob")
    await session.commit()
    assert (await seed_demo_featured_sets())["The Hobbit"] == 5
    first = {s.scryfall_id for s in await _owned_in(session, "hob")}

    assert (await seed_demo_featured_sets())["The Hobbit"] == 0      # nothing more to add
    assert {s.scryfall_id for s in await _owned_in(session, "hob")} == first

    for _ in range(4):                                               # more of the set arrives
        await _set_card(session, "hob")
    await session.commit()
    assert (await seed_demo_featured_sets())["The Hobbit"] == 2      # 7 of 14
    assert first <= {s.scryfall_id for s in await _owned_in(session, "hob")}


@pytest.mark.asyncio
async def test_featured_sets_take_one_printing_per_card_and_a_real_finish(session):
    from src.demo import seed_demo_featured_sets

    same = uuid.uuid4()
    for _ in range(3):                         # one card, three printings (showcase, borderless…)
        await _set_card(session, "mbc", oracle=same, finishes=("foil",))
    await _set_card(session, "mbc", finishes=("foil",))
    await session.commit()

    assert (await seed_demo_featured_sets())["Mystery Booster Commander Edition"] == 1  # 1 of 2
    (stack,) = await _owned_in(session, "mbc")
    assert stack.finish == "foil"              # foil-only printing is never filed as non-foil


@pytest.mark.asyncio
async def test_featured_sets_skip_unreleased_cards_and_file_into_the_demo_boxes(session):
    from src.demo import seed_demo_featured_sets
    from src.models import Box, Location

    box = Box(name="Rares & Mythics")
    session.add(box)
    await session.flush()
    location = Location(type="box", foreign_id=box.id)
    session.add(location)
    for _ in range(2):
        await _set_card(session, "sds", rarity="mythic")
    future = datetime.date.today() + datetime.timedelta(days=30)
    for _ in range(4):
        await _set_card(session, "sds", rarity="mythic", released=future)
    await session.commit()

    assert (await seed_demo_featured_sets())["Star Trek"] == 1       # half of the 2 released
    (stack,) = await _owned_in(session, "sds")
    assert stack.location == location.id
