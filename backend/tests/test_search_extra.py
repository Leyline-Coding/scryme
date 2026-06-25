"""Tests for the Phase 5 JSONB filters: watermark, border, frame, game, set_type."""

import uuid

import pytest
import pytest_asyncio
from src.models import Card
from src.scryfall.mapping import card_to_columns
from src.search import SearchScope
from src.search.engine import run_search

_RAW = [
    {"id": str(uuid.uuid4()), "name": "Watermarked Card", "set": "abc", "collector_number": "1",
     "watermark": "izzet", "border_color": "black", "frame": "2015",
     "games": ["paper", "mtgo"], "set_type": "expansion"},
    {"id": str(uuid.uuid4()), "name": "Goldborder Card", "set": "def", "collector_number": "2",
     "border_color": "gold", "frame": "1997", "games": ["paper"], "set_type": "funny"},
]


@pytest_asyncio.fixture
async def seeded(session):
    for raw in _RAW:
        session.add(Card(**card_to_columns(raw)))
    await session.commit()


async def _names(session, query):
    result = await run_search(session, query, scope=SearchScope.ALL, page_size=50)
    return {c.name for c in result.cards}


@pytest.mark.asyncio
async def test_watermark(seeded, session):
    assert await _names(session, "wm:izzet") == {"Watermarked Card"}


@pytest.mark.asyncio
async def test_border_and_frame(seeded, session):
    assert await _names(session, "border:gold") == {"Goldborder Card"}
    assert await _names(session, "frame:2015") == {"Watermarked Card"}


@pytest.mark.asyncio
async def test_game_membership(seeded, session):
    assert await _names(session, "game:mtgo") == {"Watermarked Card"}
    assert await _names(session, "game:paper") == {"Watermarked Card", "Goldborder Card"}


@pytest.mark.asyncio
async def test_set_type(seeded, session):
    assert await _names(session, "st:funny") == {"Goldborder Card"}
