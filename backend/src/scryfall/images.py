"""Local card-image cache.

Images are stored under the data volume sharded by the first two hex chars of the Scryfall id
(``images/<ab>/<id>.jpg``) to avoid huge flat directories, and served at ``/images/...``.
Downloads honor the same throttle/headers as the rest of the Scryfall client. Per Scryfall's
policy, mass image needs are satisfied by caching rather than repeated hotlinking.
"""

from __future__ import annotations

import structlog
from sqlalchemy import select

from src.config import Settings, get_settings
from src.db import SessionLocal
from src.models import Card, CollectionCard
from src.scryfall.client import ScryfallClient
from src.scryfall.mapping import image_url

log = structlog.get_logger()


class ImageCache:
    def __init__(self, settings: Settings | None = None):
        self._settings = settings or get_settings()

    def _rel(self, scryfall_id: str, size: str) -> str:
        sid = str(scryfall_id)
        ext = "png" if size == "png" else "jpg"
        return f"{sid[:2]}/{sid}_{size}.{ext}"

    def local_path(self, scryfall_id: str, size: str = "normal"):
        return self._settings.image_cache_dir / self._rel(scryfall_id, size)

    def url_path(self, scryfall_id: str, size: str = "normal") -> str:
        return f"/images/{self._rel(scryfall_id, size)}"

    def is_cached(self, scryfall_id: str, size: str = "normal") -> bool:
        return self.local_path(scryfall_id, size).exists()

    async def ensure(
        self, card: Card, client: ScryfallClient, size: str = "normal"
    ) -> str:
        """Download the card's image if not already cached. Returns the image_status."""
        path = self.local_path(card.scryfall_id, size)
        if path.exists():
            return "cached"
        url = image_url(card.raw, size)
        if not url:
            return "none"
        await client.download_to_file(url, path)
        return "cached"

    async def backfill_owned(self, size: str = "normal", limit: int | None = None) -> int:
        """Cache images for owned cards that don't have one yet. Returns the count fetched."""
        async with SessionLocal() as session:
            stmt = (
                select(Card)
                .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
                .where(Card.image_status != "cached")
                .distinct()
            )
            if limit:
                stmt = stmt.limit(limit)
            cards = (await session.execute(stmt)).scalars().all()

        fetched = 0
        async with ScryfallClient(self._settings) as client:
            for card in cards:
                status = await self.ensure(card, client, size)
                async with SessionLocal() as session:
                    db_card = await session.get(Card, card.scryfall_id)
                    if db_card is not None:
                        db_card.image_status = status
                        await session.commit()
                if status == "cached":
                    fetched += 1
        log.info("scryfall.images.backfill", fetched=fetched, candidates=len(cards))
        return fetched
