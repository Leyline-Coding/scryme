"""Coverage tests for src.sets: collector-number sort, per-set progress, and drill-in detail."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.sets import _cn_key, set_detail, set_progress


async def _seed(session):
    """Set TST has 5 printings (cn 1,2,3,10,100); the collection owns #1 (normal) and #10 (foil)."""
    made = {}
    for name, cn in [("Aaa", "1"), ("Bbb", "2"), ("Ddd", "3"), ("Ccc", "10"), ("Eee", "100")]:
        card = Card(**card_to_columns(
            {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
             "set": "TST", "set_name": "Test Set", "set_type": "expansion",
             "collector_number": cn, "rarity": "common", "released_at": "2020-01-01"}
        ))
        session.add(card)
        await session.flush()
        made[cn] = card
    session.add(CollectionCard(scryfall_id=made["1"].scryfall_id, quantity=1, finish="normal"))
    session.add(CollectionCard(scryfall_id=made["10"].scryfall_id, quantity=1, finish="foil"))
    await session.commit()
    return made


def test_cn_key_natural_order():
    assert sorted(["10", "2", "1", "100", "3"], key=_cn_key) == ["1", "2", "3", "10", "100"]
    assert _cn_key("12a") < _cn_key("100")
    assert _cn_key("T1")[0] == 1
    assert _cn_key(None)[0] == 1 << 30  # no digits -> sinks to the end


@pytest.mark.asyncio
async def test_set_progress_counts_and_empty(session):
    assert await set_progress(session) == []  # nothing owned
    await _seed(session)
    progress = await set_progress(session)
    assert len(progress) == 1
    s = progress[0]
    assert s.code == "tst" and s.name == "Test Set" and s.set_type == "expansion"
    assert s.total == 5 and s.owned == 2 and s.missing == 3 and s.pct == 40.0
    assert not s.complete


@pytest.mark.asyncio
async def test_set_detail_missing_in_order_and_unknown(session):
    await _seed(session)
    detail = await set_detail(session, "TST")  # case-insensitive
    assert detail is not None
    assert detail.total == 5 and detail.owned == 2 and detail.missing == 3 and detail.pct == 40.0
    assert [(m.collector_number, m.name) for m in detail.missing_cards] == [
        ("2", "Bbb"), ("3", "Ddd"), ("100", "Eee")
    ]
    assert await set_detail(session, "zzz") is None
