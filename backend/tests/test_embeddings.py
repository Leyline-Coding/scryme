"""Semantic similarity (#176): backfill with a fake embedding client + nearest-neighbor ordering."""

import uuid

import pytest
from src.embeddings import backfill_embeddings, run_backfill, similar_to_oracle
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns

# Toy 3-d embeddings keyed by a name substring: two "flying" cards cluster, "Bolt" is far.
_VECS = {"Serra": [1.0, 0.0, 0.0], "Shivan": [0.92, 0.1, 0.0], "Bolt": [0.0, 1.0, 0.0]}


class FakeClient:
    model = "fake-embed"

    async def embed(self, texts):
        out = []
        for t in texts:
            out.append(next((v for k, v in _VECS.items() if k in t), [0.0, 0.0, 1.0]))
        return out


async def _seed(session):
    cards = {}
    specs = [("Serra Angel", "Flying, vigilance"), ("Shivan Dragon", "Flying. {R}: +1/+0"),
             ("Lightning Bolt", "Lightning Bolt deals 3 damage to any target")]
    for i, (name, text) in enumerate(specs, 1):
        c = Card(**card_to_columns(
            {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
             "collector_number": str(i), "type_line": "Creature", "oracle_text": text}
        ))
        session.add(c)
        await session.flush()
        session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1))
        cards[name] = c
    await session.commit()
    return cards


@pytest.mark.asyncio
async def test_backfill_is_idempotent(session):
    await _seed(session)
    assert await backfill_embeddings(session, scope="owned", client=FakeClient()) == 3
    # Second run: all oracles already embedded for this model -> nothing new.
    assert await backfill_embeddings(session, scope="owned", client=FakeClient()) == 0


@pytest.mark.asyncio
async def test_similar_ranks_by_meaning(session):
    cards = await _seed(session)
    await backfill_embeddings(session, scope="owned", client=FakeClient())
    sim = await similar_to_oracle(session, cards["Serra Angel"].oracle_id, scope="owned")
    order = [str(oid) for oid, _ in sim]
    # The other flying creature ranks first; the burn spell is present but lower.
    assert order[0] == str(cards["Shivan Dragon"].oracle_id)
    assert str(cards["Lightning Bolt"].oracle_id) in order
    assert str(cards["Serra Angel"].oracle_id) not in order  # never returns itself


@pytest.mark.asyncio
async def test_similar_empty_without_embedding(session):
    cards = await _seed(session)  # no backfill
    assert await similar_to_oracle(session, cards["Serra Angel"].oracle_id) == []


@pytest.mark.asyncio
async def test_run_backfill_requires_config(monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "llm_base_url", "")
    with pytest.raises(RuntimeError):
        await run_backfill("owned")


@pytest.mark.asyncio
async def test_api_similar_endpoint(client, session):
    cards = await _seed(session)
    await backfill_embeddings(session, scope="owned", client=FakeClient())
    serra = cards["Serra Angel"].scryfall_id
    resp = await client.get(f"/api/v1/cards/{serra}/similar?scope=owned")
    assert resp.status_code == 200
    data = resp.json()
    assert data and data[0]["name"] == "Shivan Dragon"
    assert "score" in data[0] and data[0]["quantity"] == 1
