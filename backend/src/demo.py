"""Seed a rich sample collection for the public demo.

Builds a ~12,000-card collection from already-ingested cards with a deliberate spread — colour
balance, a price mix around $5, a sampling of each format's banned list (plus Vintage's restricted
list), and cards drawn at random across many sets — so the demo shows off search, stats, decks, and
price tracking at a realistic scale. Ownership is spread from **October 8, 2005** (Ravnica: City of
Guilds) to today, with synthesized monthly price history, so the value-over-time, growth, and
acquisition P/L views have two decades of data. Foil / etched finishes are assigned only to
printings that actually offer them.

Run after ingesting card data; pair with SCRYME_READ_ONLY=true. Every step is idempotent — safe to
re-run on each restart (it skips when the demo collection is already populated).
"""

from __future__ import annotations

import datetime
import random
from pathlib import Path

import structlog
from sqlalchemy import Float, and_, cast, func, or_, select, text, update

from src.checklists import create_checklist
from src.db import SessionLocal
from src.decks import create_deck
from src.models import Box, Card, CardPricePoint, Checklist, CollectionCard, Deck, PriceSnapshot
from src.wishlist import add_to_wishlist

log = structlog.get_logger()

DEFAULT_LIMIT = 12000  # retained for the CLI flag; the curated build uses its own targets
_DECK_DIR = Path(__file__).resolve().parent / "seed_data" / "decks"
EXAMPLE_DECKS = {
    "Heavenly Inferno (Commander)": "heavenly_inferno.txt",
    "Elves (Duel Decks)": "elves.txt",
    "Goblins (Duel Decks)": "goblins.txt",
}

# Collection shape (~12,000 cards).
MONO_COLORS = {"W": 2000, "U": 2000, "B": 2000, "R": 2000, "G": 2000}
COLORLESS_TARGET = 1000
MULTI_TARGET = 1000
PRICE_SPLIT = 5.0        # a chunk at/above $5, the rest below (topped up from any price)
_ABOVE_FRACTION = 0.35   # aim ~35% of each bucket at/above $5 (expensive cards are scarcer)

# At least this many cards from each format's banned list, plus Vintage's restricted list.
BANNED_FORMATS = [
    "standard", "pioneer", "modern", "legacy", "vintage", "commander", "pauper", "brawl",
]
MIN_BANNED = 3
MIN_RESTRICTED = 6  # restricted is a Vintage concept; Legacy uses a banned list (covered above)

IMPORT_YEAR = 2019  # RNG seed (deterministic selection)
COLLECTION_START = datetime.date(2005, 10, 8)  # Ravnica: City of Guilds — collecting begins here
# Fraction of finish-capable printings to make foil / etched (only where the printing offers it).
_FOIL_FRACTION = 0.18
_ETCHED_FRACTION = 0.45
_SEED_GUARD = 5000  # consider the demo already built when this many demo cards exist

_USD = cast(Card.prices["usd"].astext, Float)
# Only own cards that have actually been released (Default Cards includes upcoming/spoiled sets).
_RELEASED = or_(Card.released_at.is_(None), Card.released_at <= func.current_date())

# Showcase data seeded on a fresh demo build (in addition to cards/decks/boxes).
_TRADE_TAG_COUNT = 40  # how many owned cards to flag "for-trade" so the Trade tab is populated
_WISHLIST_COUNT = 8    # how many pricey unowned cards to put on the wishlist
# Oracle-text → tag rules the user asked to showcase.
_ORACLE_TAGS = [("removal", "%destroy target%"), ("boardwipe", "%destroy all%")]
_DEMO_CHECKLISTS = {
    "Commander Staples": (
        "Sol Ring\nArcane Signet\nCommand Tower\nSwiftfoot Boots\nLightning Greaves\n"
        "Cultivate\nKodama's Reach\nCounterspell\nSwords to Plowshares\nBeast Within\n"
        "Rhystic Study\nSmothering Tithe\nCyclonic Rift\nPath to Exile\nFellwar Stone"
    ),
    "Original Dual Lands": (
        "Tundra\nUnderground Sea\nBadlands\nTaiga\nSavannah\n"
        "Scrubland\nVolcanic Island\nBayou\nPlateau\nTropical Island"
    ),
}


async def _take(session, where, count: int, used: set, out: list) -> None:
    """Pick ``count`` distinct cards matching ``where``, biased ~35% toward $5+ then topped up from
    any price so the target is reliably met (expensive cards are too scarce to fill it alone)."""
    if count <= 0:
        return
    above = int(count * _ABOVE_FRACTION)
    bands = [
        (_USD >= PRICE_SPLIT, above),
        (and_(_USD > 0, _USD < PRICE_SPLIT), count - above),
        (None, count),  # top-up from anything matching (incl. price-less cards)
    ]
    taken = 0
    for band, want in bands:
        need = min(want, count - taken)
        if need <= 0:
            continue
        clause = where if band is None else and_(where, band)
        rows = (
            await session.execute(
                select(Card.scryfall_id, Card.oracle_id, _USD)
                .where(clause, _RELEASED)
                .order_by(func.random())
                .limit(need * 4 + 100)
            )
        ).all()
        for sid, oracle, usd in rows:
            key = oracle or sid
            if key in used:
                continue
            used.add(key)
            out.append((sid, float(usd) if usd else 0.0))
            taken += 1
            if taken >= count:
                return


async def _ensure_status(session, fmt: str, status: str, count: int, used: set, out: list) -> None:
    """Guarantee at least ``count`` cards with ``legalities[fmt] == status`` are owned."""
    rows = (
        await session.execute(
            select(Card.scryfall_id, Card.oracle_id, _USD)
            .where(Card.legalities[fmt].astext == status, _RELEASED)
            .order_by(func.random())
            .limit(count * 5 + 20)
        )
    ).all()
    have = 0
    for sid, oracle, usd in rows:
        key = oracle or sid
        if key in used:
            have += 1  # already in the collection — counts toward the guarantee
        else:
            used.add(key)
            out.append((sid, float(usd) if usd else 0.0))
            have += 1
        if have >= count:
            break


def _import_date(rng: random.Random) -> datetime.datetime:
    """A random day between COLLECTION_START (2005-10-08) and today, so the collection looks
    gradually acquired over two decades. (A later pass bumps any date earlier than a card's own
    release so nothing is 'owned' before it existed.)"""
    start = COLLECTION_START.toordinal()
    end = datetime.date.today().toordinal()
    day = datetime.date.fromordinal(rng.randint(start, end))
    return datetime.datetime(
        day.year, day.month, day.day, rng.randint(0, 23), rng.randint(0, 59), tzinfo=datetime.UTC,
    )


def _month_starts(start: datetime.datetime, end: datetime.datetime) -> list[datetime.datetime]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append(datetime.datetime(y, m, 1, tzinfo=datetime.UTC))
        m += 1
        if m > 12:
            m, y = 1, y + 1
    return out


async def _seed_price_history(session, rng: random.Random) -> None:
    """Build monthly value snapshots from the collection's actual acquisition dates: each month's
    total is the value of everything owned by then, so the curve grows as cards are added (and jumps
    when pricey cards arrive) instead of ramping linearly. Read from the DB after acquisition dates
    are assigned.

    The two most recent months also get per-card points so the "biggest movers" view works in the
    read-only demo (where the live scheduler doesn't run).
    """
    if await session.scalar(select(func.count()).select_from(PriceSnapshot)):
        return
    owned = (
        await session.execute(
            select(CollectionCard.scryfall_id, CollectionCard.added_at, _USD)
            .join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
            .where(CollectionCard.source_format == "demo")
        )
    ).all()
    # (added_at, usd) sorted by acquisition, for a running cumulative total.
    dated = sorted(((a, float(u) if u else 0.0) for _, a, u in owned if a), key=lambda r: r[0])

    months = _month_starts(
        datetime.datetime(COLLECTION_START.year, COLLECTION_START.month, 1, tzinfo=datetime.UTC),
        datetime.datetime.now(datetime.UTC),
    )
    snaps: list[PriceSnapshot] = []
    idx, cum, cnt = 0, 0.0, 0
    for when in months:
        while idx < len(dated) and dated[idx][0] <= when:  # cards owned by the start of this month
            cum += dated[idx][1]
            cnt += 1
            idx += 1
        snaps.append(PriceSnapshot(
            captured_at=when, total_usd=round(cum * rng.uniform(0.99, 1.01), 2), card_count=cnt
        ))
    session.add_all(snaps)
    await session.flush()

    # Per-card points for the last two months → movers has something to compare.
    if len(snaps) >= 2:
        prev, last = snaps[-2], snaps[-1]
        for sid, _added, usd in owned:
            if not usd:
                continue
            usd = float(usd)
            session.add(CardPricePoint(snapshot_id=last.id, scryfall_id=sid, usd=round(usd, 2)))
            session.add(CardPricePoint(
                snapshot_id=prev.id, scryfall_id=sid, usd=round(usd * rng.uniform(0.8, 1.15), 2)
            ))


async def _seed_finishes(session) -> None:
    """Give demo stacks foil / etched finishes — but only where the printing offers them (foil flag,
    'etched' in the finishes array). Etched first so the foil pass can't overwrite it."""
    await session.execute(
        text(
            "UPDATE collection_card cc SET finish = 'etched' "
            "FROM cards c WHERE c.scryfall_id = cc.scryfall_id AND cc.source_format = 'demo' "
            "AND cc.finish = 'normal' AND c.raw -> 'finishes' @> '[\"etched\"]' AND random() < :p"
        ),
        {"p": _ETCHED_FRACTION},
    )
    await session.execute(
        text(
            "UPDATE collection_card cc SET finish = 'foil' "
            "FROM cards c WHERE c.scryfall_id = cc.scryfall_id AND cc.source_format = 'demo' "
            "AND cc.finish = 'normal' AND (c.raw ->> 'foil') = 'true' AND random() < :p"
        ),
        {"p": _FOIL_FRACTION},
    )
    await session.commit()


async def _acquire_dates(session) -> None:
    """Set each card's acquisition date to a random 1–320 days after it was released (or after the
    2005-10-08 collection start, for cards older than that), capped at today — so ownership
    accumulates gradually over the years instead of everything appearing at once."""
    await session.execute(
        text(
            "UPDATE collection_card cc SET added_at = LEAST("
            "  GREATEST(c.released_at, DATE '2005-10-08')::timestamptz "
            "    + ((1 + floor(random() * 320))::int) * interval '1 day', now()) "
            "FROM cards c "
            "WHERE c.scryfall_id = cc.scryfall_id AND cc.source_format = 'demo' "
            "AND c.released_at IS NOT NULL"
        )
    )
    await session.commit()


async def _seed_showcase(session) -> None:
    """Demo-only showcase data: oracle-text tags, a trade list, a wishlist, and checklists.

    Runs only on a fresh build (its caller is past the already-seeded guard). Every step is
    idempotent — array tags de-dupe, wishlist adds are upserts, and checklists skip existing names.
    """
    # Oracle-text tags the user asked for: 'removal' on "destroy target", 'boardwipe' on
    # "destroy all". De-duped so a re-run can't stack copies.
    for tag, pattern in _ORACLE_TAGS:
        await session.execute(
            text(
                "UPDATE collection_card cc "
                "SET tags = (SELECT array_agg(DISTINCT t) "
                "            FROM unnest(coalesce(cc.tags, '{}') || ARRAY[:tag]) AS t) "
                "FROM cards c "
                "WHERE c.scryfall_id = cc.scryfall_id AND c.oracle_text ILIKE :pat"
            ),
            {"tag": tag, "pat": pattern},
        )
    # Trade list: flag a deterministic slice of owned cards "for-trade" to populate the Trade tab.
    await session.execute(
        text(
            "UPDATE collection_card "
            "SET tags = (SELECT array_agg(DISTINCT t) "
            "            FROM unnest(coalesce(tags, '{}') || ARRAY['for-trade']) AS t) "
            "WHERE id IN (SELECT id FROM collection_card ORDER BY scryfall_id LIMIT :n)"
        ),
        {"n": _TRADE_TAG_COUNT},
    )
    await session.commit()

    # Wishlist: a handful of the priciest cards you don't own.
    unowned = (
        await session.execute(
            select(Card.scryfall_id)
            .where(Card.scryfall_id.notin_(select(CollectionCard.scryfall_id)), _USD.isnot(None))
            .order_by(_USD.desc())
            .limit(_WISHLIST_COUNT)
        )
    ).scalars().all()
    for sid in unowned:
        await add_to_wishlist(session, sid, note="On my radar")

    # Checklists (skip any that already exist by name).
    existing = set(await session.scalars(select(Checklist.name)))
    for name, cards in _DEMO_CHECKLISTS.items():
        if name not in existing:
            await create_checklist(session, name, cards)


async def seed_demo(limit: int = DEFAULT_LIMIT) -> int:
    """Build the curated demo collection. Idempotent: skips when already populated."""
    rng = random.Random(IMPORT_YEAR)  # deterministic selection/dates
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(func.count())
            .select_from(CollectionCard)
            .where(CollectionCard.source_format == "demo")
        )
        if existing >= _SEED_GUARD:
            log.info("demo.seed_skipped", reason="already seeded", collection_size=existing)
            return 0

        # Track what's already owned by the same key used during selection (oracle, else printing),
        # so re-runs don't add duplicates.
        owned = (
            await session.execute(
                select(Card.oracle_id, Card.scryfall_id).join(
                    CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id
                )
            )
        ).all()
        used: set = {oracle or sid for oracle, sid in owned}
        out: list = []

        # Bucket by color IDENTITY (includes mana symbols in rules text / kicker), not cast-cost
        # colors — so e.g. Phyrexian Warhorse ({3}{B} + Kicker {W}) is Orzhov, not mono-black.
        for color, target in MONO_COLORS.items():
            await _take(session, Card.color_identity == [color], target, used, out)
        await _take(session, func.coalesce(func.array_length(Card.color_identity, 1), 0) == 0,
                    COLORLESS_TARGET, used, out)
        await _take(session, func.array_length(Card.color_identity, 1) >= 2,
                    MULTI_TARGET, used, out)

        for fmt in BANNED_FORMATS:
            await _ensure_status(session, fmt, "banned", MIN_BANNED, used, out)
        await _ensure_status(session, "vintage", "restricted", MIN_RESTRICTED, used, out)

        for sid, usd in out:
            session.add(
                CollectionCard(
                    scryfall_id=sid, quantity=1, source_format="demo",
                    added_at=_import_date(rng),
                    purchase_price=round(usd * rng.uniform(0.4, 1.1), 2) if usd else None,
                )
            )
        await session.flush()
        await _acquire_dates(session)   # gradual acquisition: release + 1–320 days, capped at today
        await _seed_finishes(session)   # foil / etched where the printing offers it
        await _seed_price_history(session, rng)  # value curve from real acquisition dates
        # Showcase storage boxes (#160): two physical boxes, filed by rarity (idempotent).
        existing_boxes = set(await session.scalars(select(Box.name)))
        for name in ("Rares & Mythics", "Bulk Box"):
            if name not in existing_boxes:
                session.add(Box(name=name))
        for box_name, rarities in [("Rares & Mythics", ["rare", "mythic"]),
                                   ("Bulk Box", ["common", "uncommon"])]:
            await session.execute(
                update(CollectionCard)
                .where(CollectionCard.scryfall_id.in_(
                    select(Card.scryfall_id).where(Card.rarity.in_(rarities))
                ))
                .values(location=box_name)
            )
        await session.commit()
        await _seed_showcase(session)
        total = await session.scalar(select(func.count()).select_from(CollectionCard))
    log.info("demo.seeded", added=len(out), collection_size=total)
    return len(out)


async def seed_demo_decks() -> int:
    """Create the example decks from seed files. Idempotent: skips decks that already exist."""
    created = 0
    async with SessionLocal() as session:
        existing = set(await session.scalars(select(Deck.name)))
        for name, filename in EXAMPLE_DECKS.items():
            if name in existing:
                continue
            path = _DECK_DIR / filename
            if not path.exists():
                log.warning("demo.deck_missing", file=str(path))
                continue
            await create_deck(session, name, path.read_text(encoding="utf-8"))
            created += 1
    log.info("demo.decks_seeded", created=created)
    return created
