"""Binder browsing tests: grouping by binder_name and the per-binder card view."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _seed(session):
    names = [("Alpha", "Reds"), ("Beta", "Reds"), ("Gamma", None)]
    for i, (name, binder) in enumerate(names):
        raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
               "set": "TST", "collector_number": str(i), "rarity": "common", "cmc": 1,
               "type_line": "Creature", "colors": ["R"], "color_identity": ["R"]}
        c = Card(**card_to_columns(raw))
        session.add(c)
        await session.flush()
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=2, binder_name=binder))
    await session.commit()


@pytest.mark.asyncio
async def test_list_binders(client, session):
    await _seed(session)
    resp = await client.get("/binders")
    assert resp.status_code == 200
    assert "Reds" in resp.text
    assert "Unsorted" in resp.text  # the null-binder group


@pytest.mark.asyncio
async def test_view_named_binder(client, session):
    await _seed(session)
    resp = await client.get("/binders/cards", params={"name": "Reds"})
    assert resp.status_code == 200
    assert "Alpha" in resp.text and "Beta" in resp.text
    assert "Gamma" not in resp.text  # not in this binder


@pytest.mark.asyncio
async def test_view_unsorted_binder(client, session):
    await _seed(session)
    resp = await client.get("/binders/cards", params={"name": "__none__"})
    assert resp.status_code == 200
    assert "Gamma" in resp.text
    assert "Alpha" not in resp.text
