"""Demo seed tests."""

import pytest
from sqlalchemy import func, select
from src.demo import seed_demo
from src.models import CollectionCard

from tests.seed_cards import seed_cards


@pytest.mark.asyncio
async def test_seed_demo_populates_collection(session):
    await seed_cards(session)  # 4 cards in the DB, none owned
    added = await seed_demo(limit=3)
    assert added == 3
    total = await session.scalar(select(func.count()).select_from(CollectionCard))
    assert total == 3


@pytest.mark.asyncio
async def test_seed_demo_skips_already_owned(session):
    await seed_cards(session)
    await seed_demo(limit=2)
    # Running again only adds the cards not already owned.
    added = await seed_demo(limit=10)
    total = await session.scalar(select(func.count()).select_from(CollectionCard))
    assert added == 2  # the remaining two cards
    assert total == 4
