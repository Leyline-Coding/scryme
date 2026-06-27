"""Set completion tests: per-set progress, the missing-card drill-in, and the routes."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.sets import _cn_key, set_detail, set_progress


async def _seed(session):
    """Set TST has 5 printings (cn 1, 2, 3, 10, 100); the collection owns #1 and #10."""
    cards = [("Aaa", "1"), ("Bbb", "2"), ("Ddd", "3"), ("Ccc", "10"), ("Eee", "100")]
    made = {}
    for name, cn in cards:
        raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
               "set": "TST", "set_name": "Test Set", "set_type": "expansion",
               "collector_number": cn, "rarity": "common", "released_at": "2020-01-01"}
        card = Card(**card_to_columns(raw))
        session.add(card)
        await session.flush()
        made[cn] = card
    session.add(CollectionCard(scryfall_id=made["1"].scryfall_id, quantity=1, finish="normal"))
    session.add(CollectionCard(scryfall_id=made["10"].scryfall_id, quantity=1, finish="foil"))
    await session.commit()


def test_cn_key_natural_order():
    assert sorted(["10", "2", "1", "100", "3"], key=_cn_key) == ["1", "2", "3", "10", "100"]
    # Suffixed / non-numeric collector numbers still sort sensibly.
    assert _cn_key("12a") < _cn_key("100")
    assert _cn_key("T1")[0] == 1  # first embedded integer drives the order


@pytest.mark.asyncio
async def test_set_progress_counts(session):
    await _seed(session)
    progress = await set_progress(session)
    assert len(progress) == 1
    s = progress[0]
    assert s.code == "tst"
    assert s.name == "Test Set"
    assert s.set_type == "expansion"
    assert s.total == 5
    assert s.owned == 2  # distinct printings owned (foil still counts once)
    assert s.missing == 3
    assert s.pct == 40.0
    assert not s.complete


@pytest.mark.asyncio
async def test_set_progress_empty(session):
    assert await set_progress(session) == []


@pytest.mark.asyncio
async def test_set_detail_lists_missing_in_order(session):
    await _seed(session)
    detail = await set_detail(session, "TST")  # case-insensitive
    assert detail is not None
    assert detail.total == 5 and detail.owned == 2 and detail.missing == 3
    # Missing = everything except #1 and #10, naturally ordered by collector number.
    assert [(m.collector_number, m.name) for m in detail.missing_cards] == [
        ("2", "Bbb"), ("3", "Ddd"), ("100", "Eee")
    ]


@pytest.mark.asyncio
async def test_set_detail_unknown_returns_none(session):
    assert await set_detail(session, "zzz") is None


@pytest.mark.asyncio
async def test_sets_routes_render(client, session):
    await _seed(session)
    listing = await client.get("/sets")
    assert listing.status_code == 200
    assert "Set completion" in listing.text
    assert "Test Set" in listing.text
    assert "2/5" in listing.text

    detail = await client.get("/sets/tst")
    assert detail.status_code == 200
    assert "Missing" in detail.text
    assert "Bbb" in detail.text

    assert (await client.get("/sets/zzz")).status_code == 404
