"""Image cache tests: path layout, ensure() download, and owned-card backfill."""

import json
import uuid
from pathlib import Path

import pytest
from src.config import get_settings
from src.models import Card, CollectionCard
from src.scryfall.images import ImageCache

FIXTURES = Path(__file__).parent / "fixtures"
CARDS = json.loads((FIXTURES / "scryfall_sample.json").read_text())


class FakeClient:
    """Writes a placeholder file instead of hitting the network."""

    def __init__(self):
        self.downloaded: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def download_to_file(self, url, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\xff\xd8\xff")  # tiny fake JPEG header
        self.downloaded.append(url)
        return dest


def test_path_and_url_layout():
    cache = ImageCache()
    sid = "b0faa7f2-b547-42c4-a810-839da50dadfe"
    assert cache.url_path(sid, "normal") == f"/images/b0/{sid}_normal.jpg"
    assert cache.url_path(sid, "png").endswith(".png")
    assert str(cache.local_path(sid)).startswith(str(get_settings().image_cache_dir))


@pytest.mark.asyncio
async def test_ensure_downloads_then_caches():
    cache = ImageCache()
    card = Card(**_columns(CARDS[0]))
    client = FakeClient()

    status = await cache.ensure(card, client)
    assert status == "cached"
    assert cache.is_cached(card.scryfall_id)
    assert len(client.downloaded) == 1

    # Already on disk -> no second download.
    status = await cache.ensure(card, client)
    assert status == "cached"
    assert len(client.downloaded) == 1


@pytest.mark.asyncio
async def test_ensure_returns_none_when_no_image():
    cache = ImageCache()
    card = Card(scryfall_id=uuid.uuid4(), name="x", set_code="x", collector_number="1", raw={})
    assert await cache.ensure(card, FakeClient()) == "none"


@pytest.mark.asyncio
async def test_backfill_owned(session, monkeypatch):
    card = Card(**_columns(CARDS[1]))
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1))
    await session.commit()

    # Patch the client used inside backfill to the fake one.
    monkeypatch.setattr("src.scryfall.images.ScryfallClient", lambda *a, **k: FakeClient())
    fetched = await ImageCache().backfill_owned()
    assert fetched == 1

    # backfill committed in its own session; refresh to drop the stale identity-map value.
    await session.refresh(card)
    assert card.image_status == "cached"


def _columns(raw: dict) -> dict:
    from src.scryfall.mapping import card_to_columns

    return card_to_columns(raw)
