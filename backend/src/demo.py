"""Seed a sample collection for the public demo.

Marks a handful of already-ingested cards as owned so the demo shows a populated, searchable
collection. Run after ingesting card data; intended to be paired with SCRYME_READ_ONLY=true.
"""

from __future__ import annotations

import structlog
from sqlalchemy import func, select

from src.db import SessionLocal
from src.models import Card, CollectionCard

log = structlog.get_logger()

DEFAULT_LIMIT = 60


async def seed_demo(limit: int = DEFAULT_LIMIT) -> int:
    """Add up to ``limit`` ingested cards to the collection (skips already-owned). Returns added."""
    async with SessionLocal() as session:
        owned = select(CollectionCard.scryfall_id)
        cards = (
            await session.execute(
                select(Card).where(Card.scryfall_id.not_in(owned)).order_by(Card.name).limit(limit)
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
