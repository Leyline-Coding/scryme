"""Coverage tests for src/scryfall/images.py — path layout, ensure(), and owned backfill."""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from src.config import get_settings
from src.models import Card, CollectionCard
from src.scryfall.images import ImageCache
from src.scryfall.mapping import card_to_columns


class FakeClient:
    """Async-context client that writes a placeholder file instead of hitting the network."""

    def __init__(self):
        self.downloaded: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def download_to_file(self, url, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"\xff\xd8\xff")
        self.downloaded.append(url)
        return dest


def test_path_layout_jpg_and_png():
    cache = ImageCache()
    sid = "b0faa7f2-b547-42c4-a810-839da50dadfe"
    assert cache._rel(sid, "normal") == f"b0/{sid}_normal.jpg"
    assert cache._rel(sid, "png").endswith(".png")
    assert cache.url_path(sid, "normal") == f"/images/b0/{sid}_normal.jpg"
    assert str(cache.local_path(sid)).startswith(str(get_settings().image_cache_dir))
    assert not cache.is_cached(str(uuid.uuid4()))  # a never-downloaded id isn't on disk


def _card_with_image():
    # A fresh id so the on-disk cache (which persists across tests) never pre-exists.
    raw = {"id": str(uuid.uuid4()), "name": "Art", "set": "tst", "collector_number": "1",
           "image_uris": {"normal": "https://img.test/n.jpg"}}
    return Card(**card_to_columns(raw))


@pytest.mark.asyncio
async def test_ensure_downloads_then_skips_second_time():
    cache = ImageCache()
    card = _card_with_image()
    client = FakeClient()

    assert await cache.ensure(card, client) == "cached"
    assert cache.is_cached(card.scryfall_id)
    assert len(client.downloaded) == 1

    # Cached on disk -> no second download.
    assert await cache.ensure(card, client) == "cached"
    assert len(client.downloaded) == 1


@pytest.mark.asyncio
async def test_ensure_returns_none_without_image():
    cache = ImageCache()
    card = Card(scryfall_id=uuid.uuid4(), name="x", set_code="x", collector_number="1", raw={})
    assert await cache.ensure(card, FakeClient()) == "none"


@pytest.mark.asyncio
async def test_backfill_owned_caches_and_marks(session, monkeypatch):
    card = _card_with_image()
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1))
    await session.commit()

    monkeypatch.setattr("src.scryfall.images.ScryfallClient", lambda *a, **k: FakeClient())
    fetched = await ImageCache().backfill_owned()
    assert fetched == 1

    await session.refresh(card)
    assert card.image_status == "cached"


@pytest.mark.asyncio
async def test_backfill_owned_respects_limit_and_no_image(session, monkeypatch):
    # An owned card with no image_uris -> ensure() returns "none", not counted as fetched.
    raw = {"id": str(uuid.uuid4()), "name": "No Art", "set": "tst", "collector_number": "1"}
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1))
    await session.commit()

    monkeypatch.setattr("src.scryfall.images.ScryfallClient", lambda *a, **k: FakeClient())
    fetched = await ImageCache().backfill_owned(limit=5)
    assert fetched == 0
    await session.refresh(card)
    assert card.image_status == "none"


@pytest.mark.asyncio
async def test_backfill_owned_empty_when_nothing_owned(monkeypatch):
    monkeypatch.setattr("src.scryfall.images.ScryfallClient", lambda *a, **k: FakeClient())
    assert await ImageCache().backfill_owned() == 0
