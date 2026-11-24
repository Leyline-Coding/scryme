"""Card tags: normalization, add/remove across stacks, `tag:` search, and the routes."""

import uuid

import pytest
from sqlalchemy import select
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.search import SearchScope
from src.search.engine import run_search
from src.tags import card_tags, modify_stack_tag, normalize_tag


def _card(name, n):
    return {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
            "set": "TST", "collector_number": str(n), "rarity": "rare", "prices": {"usd": "1.00"}}


async def _own(session, raw, **stack):
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    cc = CollectionCard(scryfall_id=c.scryfall_id, quantity=1, **stack)
    session.add(cc)
    await session.commit()
    return c, cc


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
    cc = CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="normal")
    session.add(cc)
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="foil"))
    await session.commit()

    tags = await modify_stack_tag(session, cc.id, "For Trade")
    assert tags == 1
    # Applied to every stack.
    stored = (await session.execute(
        select(CollectionCard).where(CollectionCard.scryfall_id == c.scryfall_id)
    )).scalars().all()
    assert stored[0].tags is None
    assert stored[1].tags == ["for trade"]

    await modify_stack_tag(session, cc.id, "for trade")  # idempotent
    assert await card_tags(session, c.scryfall_id) == ["for trade"]

    assert await modify_stack_tag(session, cc.id, "for trade", remove=True) == 1


@pytest.mark.asyncio
async def test_tag_search_finds_and_excludes(session):
    a_c, a_cc =  await _own(session, _card("Aaa", 1), finish="normal")
    b_c, b_cc = await _own(session, _card("Bbb", 2), finish="normal")
    await modify_stack_tag(session, a_cc.id, "trade")

    hit = await run_search(session, "tag:trade", scope=SearchScope.COLLECTION)
    assert [c.name for c in hit.cards] == ["Aaa"]
    # Tags are surfaced on the result for chip rendering.
    assert hit.tags[str(a_c.scryfall_id)] == ["trade"]

    neg = await run_search(session, "-tag:trade", scope=SearchScope.COLLECTION)
    assert [c.name for c in neg.cards] == ["Bbb"]


@pytest.mark.asyncio
async def test_tag_routes(client, session):
    card, cc = await _own(session, _card("Aaa", 1), finish="normal")
    sid = str(card.scryfall_id)

    add = await client.post(
        "/collection/bulk",
        data={"scryfall_ids": [sid], "bulk_action": "tag", "tag": "Deck:Goblins", "refresh":True}
    )
    assert add.status_code == 200

    rm = await client.post(
        "/collection/bulk",
        data={
            "scryfall_ids": [sid], "bulk_action": "tag-remove", "tag": "deck:goblins",
            "refresh":True
        }
    )
    assert rm.status_code == 200


@pytest.mark.asyncio
async def test_tag_routes_blocked_when_read_only(client, session, monkeypatch):
    from src.config import get_settings
    settings = get_settings()
    monkeypatch.setattr(settings, "read_only", True)
    card, cc = await _own(session, _card("Aaa", 1), finish="normal")
    resp = await client.post(
        "/collection/bulk",
        data={"scryfall_ids": [card.scryfall_id], "bulk_action": "tag", "tag": "x"}
    )
    assert resp.status_code == 403
