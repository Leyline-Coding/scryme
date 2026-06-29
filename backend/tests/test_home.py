import uuid

import pytest
from src.models import Card


def _seed_card(session):
    """A single card so the card DB looks 'ready' (not a fresh first-run install)."""
    session.add(
        Card(
            scryfall_id=uuid.uuid4(),
            name="Black Lotus",
            set_code="lea",
            collector_number="232",
            raw={"name": "Black Lotus"},
        )
    )


@pytest.mark.asyncio
async def test_home_no_cards_shows_first_run(client):
    """A fresh install (empty card DB) gates on the one-time ingest setup screen."""
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Setting up scryme" in resp.text


@pytest.mark.asyncio
async def test_home_empty_shows_upload(client, session):
    """With cards ingested but no collection, the home page prompts for an upload."""
    _seed_card(session)
    await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Upload a collection" in resp.text


@pytest.mark.asyncio
async def test_home_with_collection_shows_search(client, session):
    """Once a card is owned, the home page shows the search bar instead."""
    import uuid

    from src.models import Card, CollectionCard

    card = Card(
        scryfall_id=uuid.uuid4(),
        name="Black Lotus",
        set_code="lea",
        collector_number="232",
        raw={"name": "Black Lotus"},
    )
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1))
    await session.commit()

    resp = await client.get("/")
    assert resp.status_code == 200
    assert 'name="q"' in resp.text
