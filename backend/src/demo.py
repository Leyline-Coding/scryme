"""Seed sample data for the public demo.

Marks a handful of already-ingested cards as owned (so the demo shows a populated, searchable
collection) and creates a few example decks. Run after ingesting card data; intended to be paired
with SCRYME_READ_ONLY=true. Both steps are idempotent — safe to re-run on every restart.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from sqlalchemy import func, select

from src.db import SessionLocal
from src.decks import create_deck
from src.models import Card, CollectionCard, Deck

log = structlog.get_logger()

DEFAULT_LIMIT = 60
_DECK_DIR = Path(__file__).resolve().parent / "seed_data" / "decks"
# Display name -> decklist file. Example decks shown on the demo.
EXAMPLE_DECKS = {
    "Heavenly Inferno (Commander)": "heavenly_inferno.txt",
    "Elves (Duel Decks)": "elves.txt",
    "Goblins (Duel Decks)": "goblins.txt",
}


async def seed_demo(limit: int = DEFAULT_LIMIT) -> int:
    """Add up to ``limit`` ingested cards to the collection. Idempotent: skips if already seeded."""
    async with SessionLocal() as session:
        existing = await session.scalar(
            select(func.count())
            .select_from(CollectionCard)
            .where(CollectionCard.source_format == "demo")
        )
        if existing >= limit:
            log.info("demo.seed_skipped", reason="already seeded", collection_size=existing)
            return 0

        owned = select(CollectionCard.scryfall_id)
        cards = (
            await session.execute(
                select(Card)
                .where(Card.scryfall_id.not_in(owned))
                .order_by(Card.name)
                .limit(limit - existing)
            )
        ).scalars().all()
        for card in cards:
            session.add(
                CollectionCard(scryfall_id=card.scryfall_id, quantity=1, source_format="demo")
            )
        await session.commit()
        total = await session.scalar(select(func.count()).select_from(CollectionCard))
    log.info("demo.seeded", added=len(cards), collection_size=total)
    return len(cards)


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
