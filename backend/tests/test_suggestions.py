"""Did-you-mean: trigram name suggestions on zero-result name searches."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.search import SearchScope
from src.search.engine import _is_name_query, name_suggestions


def test_is_name_query():
    assert _is_name_query("lightning bolt")
    assert not _is_name_query("")
    assert not _is_name_query("c:r")          # has a filter
    assert not _is_name_query("o:flying")     # has a filter
    assert not _is_name_query("/bolt/")       # regex
    assert not _is_name_query("mv>=3")        # comparison


async def _card(session, name, n, owned=True):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
           "collector_number": str(n), "rarity": "rare", "prices": {"usd": "1.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    if owned:
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_suggestions_find_close_name(session):
    await _card(session, "Lightning Bolt", 1)
    await _card(session, "Lightning Helix", 2)
    await _card(session, "Counterspell", 3)
    out = await name_suggestions(session, "Lightnig Bolt", SearchScope.ALL)
    assert "Lightning Bolt" in out
    assert "Counterspell" not in out  # too dissimilar


@pytest.mark.asyncio
async def test_suggestions_respect_collection_scope(session):
    await _card(session, "Lightning Bolt", 1, owned=True)
    await _card(session, "Lightning Helix", 2, owned=False)  # not owned
    owned = await name_suggestions(session, "Lightning", SearchScope.COLLECTION)
    assert owned == ["Lightning Bolt"]  # only the owned one
    all_cards = await name_suggestions(session, "Lightning", SearchScope.ALL)
    assert set(all_cards) == {"Lightning Bolt", "Lightning Helix"}


@pytest.mark.asyncio
async def test_no_suggestions_for_filter_query(session):
    await _card(session, "Lightning Bolt", 1)
    assert await name_suggestions(session, "c:r", SearchScope.ALL) == []


@pytest.mark.asyncio
async def test_zero_result_page_shows_suggestions(client, session):
    await _card(session, "Lightning Bolt", 1)
    resp = await client.get("/search?q=Lightnig+Bolt&scope=all")
    assert resp.status_code == 200
    assert "Did you mean" in resp.text
    assert 'data-q="Lightning Bolt"' in resp.text
