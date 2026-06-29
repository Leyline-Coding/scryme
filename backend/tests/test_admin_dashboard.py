"""Admin status dashboard + Prometheus metrics."""

import uuid

import pytest
from src.admin_stats import collect_admin_stats, render_metrics
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _seed(session):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "tst",
           "collector_number": "1", "rarity": "rare", "prices": {"usd": "1.00"}}
    c = Card(**card_to_columns(raw))
    c.image_status = "cached"
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=3))
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_collect_admin_stats(session):
    await _seed(session)
    stats = await collect_admin_stats(session)
    assert stats.card_count == 1
    assert stats.images_cached == 1
    assert stats.collection_cards == 3
    assert stats.collection_printings == 1
    assert stats.db_size_bytes > 0
    assert stats.version


@pytest.mark.asyncio
async def test_render_metrics_prometheus_format(session):
    await _seed(session)
    stats = await collect_admin_stats(session)
    text = render_metrics(stats)
    assert "# TYPE scryme_cards_total gauge" in text
    assert "scryme_cards_total 1" in text
    assert "scryme_collection_cards_total 3" in text
    assert f'scryme_info{{version="{stats.version}"}} 1' in text


@pytest.mark.asyncio
async def test_admin_dashboard_route(client, session):
    await _seed(session)
    resp = await client.get("/admin")
    assert resp.status_code == 200
    assert "Status" in resp.text and "Card database" in resp.text


@pytest.mark.asyncio
async def test_metrics_route(client, session):
    await _seed(session)
    resp = await client.get("/metrics")
    assert resp.status_code == 200
    assert "text/plain" in resp.headers["content-type"]
    assert "scryme_cards_total" in resp.text


@pytest.mark.asyncio
async def test_admin_status_json_still_works(client, session):
    await _seed(session)
    resp = await client.get("/admin/status")
    assert resp.status_code == 200
    assert resp.json()["card_count"] == 1
