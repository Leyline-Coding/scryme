"""Universal search filter (#143) + card-image hover endpoint."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.routes.search import _apply_universal
from src.scryfall.mapping import card_to_columns


def test_apply_universal():
    assert _apply_universal("c:r", "-is:ub") == "(-is:ub) (c:r)"
    assert _apply_universal("", "-is:ub") == "-is:ub"
    assert _apply_universal("c:r", "") == "c:r"
    assert _apply_universal("  ", "  ") == ""


async def _own(session, name, type_line, *, raw_extra=None):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
           "set": "tst", "collector_number": str(abs(hash(name)) % 9999), "type_line": type_line}
    if raw_extra:
        raw.update(raw_extra)
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_universal_filter_applied(client, session):
    await _own(session, "Counterspell", "Instant")
    await _own(session, "Grizzly Bears", "Creature — Bear")

    # Cookie ANDs `t:instant` into an empty query → only the instant.
    resp = await client.get("/search", headers={"Cookie": "scryme_search_filter=t:instant"})
    assert resp.status_code == 200
    assert "Counterspell" in resp.text and "Grizzly Bears" not in resp.text
    assert "Always applied" in resp.text


@pytest.mark.asyncio
async def test_bad_universal_filter_falls_back(client, session):
    await _own(session, "Counterspell", "Instant")

    resp = await client.get("/search", headers={"Cookie": "scryme_search_filter=zzz:1"})
    # A broken universal filter is ignored (flagged), not fatal — the bare query still runs.
    assert resp.status_code == 200
    assert "Counterspell" in resp.text
    assert "invalid" in resp.text


@pytest.mark.asyncio
async def test_card_image_redirect(client, session):
    card = await _own(session, "Sol Ring", "Artifact",
                      raw_extra={"image_uris": {"normal": "https://cdn.example/sol.jpg"}})
    resp = await client.get(f"/card/{card.scryfall_id}/image", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "https://cdn.example/sol.jpg"
