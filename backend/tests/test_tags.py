"""Card tags: normalization, add/remove across stacks, `tag:` search, and the routes."""

import uuid

import pytest
from sqlalchemy import select
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.search import SearchScope
from src.search.engine import run_search
from src.tags import add_card_tag, card_tags, normalize_tag, remove_card_tag


def _card(name, n):
    return {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
            "set": "TST", "collector_number": str(n), "rarity": "rare", "prices": {"usd": "1.00"}}


async def _own(session, raw, **stack):
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1, **stack))
    await session.commit()
    return card


def test_normalize_tag():
    assert normalize_tag("  For Trade ") == "for trade"
    assert normalize_tag("Deck:Goblins") == "deck:goblins"
    assert normalize_tag("") is None
    assert normalize_tag("   ") is None
    assert len(normalize_tag("x" * 200)) == 64


@pytest.mark.asyncio
async def test_add_and_remove_tag_spans_all_stacks(session):
    card = _card("Aaa", 1)
    c = Card(**card_to_columns(card))
    session.add(c)
    await session.flush()
    # Two stacks of the same printing (normal + foil).
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="normal"))
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="foil"))
    await session.commit()

    tags = await add_card_tag(session, c.scryfall_id, "For Trade")
    assert tags == ["for trade"]
    # Applied to every stack.
    stored = (await session.execute(
        select(CollectionCard.tags).where(CollectionCard.scryfall_id == c.scryfall_id)
    )).all()
    assert all(row[0] == ["for trade"] for row in stored)

    await add_card_tag(session, c.scryfall_id, "for trade")  # idempotent
    assert await card_tags(session, c.scryfall_id) == ["for trade"]

    assert await remove_card_tag(session, c.scryfall_id, "for trade") == []


@pytest.mark.asyncio
async def test_tag_search_finds_and_excludes(session):
    a = await _own(session, _card("Aaa", 1), finish="normal")
    await _own(session, _card("Bbb", 2), finish="normal")
    await add_card_tag(session, a.scryfall_id, "trade")

    hit = await run_search(session, "tag:trade", scope=SearchScope.COLLECTION)
    assert [c.name for c in hit.cards] == ["Aaa"]
    # Tags are surfaced on the result for chip rendering.
    assert hit.tags[str(a.scryfall_id)] == ["trade"]

    neg = await run_search(session, "-tag:trade", scope=SearchScope.COLLECTION)
    assert [c.name for c in neg.cards] == ["Bbb"]


@pytest.mark.asyncio
async def test_tag_routes(client, session):
    card = await _own(session, _card("Aaa", 1), finish="normal")
    cid = str(card.scryfall_id)

    add = await client.post(f"/card/{cid}/tags", data={"tag": "Deck:Goblins"})
    assert add.status_code == 200
    assert "deck:goblins" in add.text

    detail = await client.get(f"/card/{cid}")
    assert "deck:goblins" in detail.text

    rm = await client.post(f"/card/{cid}/tags/delete", data={"tag": "deck:goblins"})
    assert rm.status_code == 200
    assert "deck:goblins" not in rm.text


@pytest.mark.asyncio
async def test_tag_routes_blocked_when_read_only(client, session, monkeypatch):
    from src.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "read_only", True)
    card = await _own(session, _card("Aaa", 1), finish="normal")
    resp = await client.post(f"/card/{card.scryfall_id}/tags", data={"tag": "x"})
    assert resp.status_code == 403
