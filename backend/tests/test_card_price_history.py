"""Per-card price history: recording for owned + tracked cards and the card-page chart (#233)."""

import datetime
import uuid

import pytest
from sqlalchemy import select
from src import fx
from src.models import (
    Card,
    CardPricePoint,
    CollectionCard,
    FxRateHistory,
    PriceSnapshot,
    PriceTarget,
    WishlistItem,
)
from src.prices import card_value_series, snapshot_prices
from src.scryfall.mapping import card_to_columns


def _raw(name, usd, cn):
    return {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "TST",
            "collector_number": cn, "rarity": "rare", "cmc": 1, "type_line": "Creature",
            "prices": {"usd": usd}}


async def _add(session, name, usd, cn):
    c = Card(**card_to_columns(_raw(name, usd, cn)))
    session.add(c)
    await session.flush()
    return c


@pytest.mark.asyncio
async def test_snapshot_records_points_for_owned_and_tracked_only(session):
    owned = await _add(session, "Owned", "1.00", "1")
    wished = await _add(session, "Wished", "2.00", "2")
    watched = await _add(session, "Watched", "3.00", "3")
    untracked = await _add(session, "Untracked", "4.00", "4")
    session.add(CollectionCard(scryfall_id=owned.scryfall_id, quantity=1, finish="normal"))
    session.add(WishlistItem(scryfall_id=wished.scryfall_id, quantity=1))
    session.add(PriceTarget(scryfall_id=watched.scryfall_id, direction="below", threshold=1.0))
    await session.commit()

    snap = await snapshot_prices(session)
    assert snap is not None
    pts = {str(p.scryfall_id) for p in (await session.execute(select(CardPricePoint))).scalars()}
    assert str(owned.scryfall_id) in pts
    assert str(wished.scryfall_id) in pts   # wishlist card gets a point even though unowned
    assert str(watched.scryfall_id) in pts  # price-watch card too
    assert str(untracked.scryfall_id) not in pts
    # Collection total/card_count stay owned-only.
    assert snap.total_usd == 1.00
    assert snap.card_count == 1


@pytest.mark.asyncio
async def test_card_value_series_tracks_price_over_snapshots(session):
    c = await _add(session, "Hist", "1.00", "1")
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="normal"))
    await session.commit()
    await snapshot_prices(session)
    c.prices = {"usd": "2.50"}
    await session.commit()
    await snapshot_prices(session)

    series = await card_value_series(session, c.scryfall_id, None)
    assert [round(p.total_usd, 2) for p in series] == [1.00, 2.50]


@pytest.mark.asyncio
async def test_card_page_shows_history_for_owned_card(client, session):
    c = await _add(session, "Owned", "1.50", "1")
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="normal"))
    await session.commit()
    await snapshot_prices(session)
    await snapshot_prices(session)  # two points -> a trend line

    resp = await client.get(f"/card/{c.scryfall_id}")
    assert resp.status_code == 200
    assert "Price history" in resp.text
    assert f"/card/{c.scryfall_id}?range=" in resp.text  # range pills wired to this card


@pytest.mark.asyncio
async def test_card_page_empty_state_when_no_history(client, session):
    c = await _add(session, "Lonely", "1.00", "1")
    await session.commit()
    resp = await client.get(f"/card/{c.scryfall_id}")
    assert resp.status_code == 200
    assert "No price history yet" in resp.text


@pytest.mark.asyncio
async def test_card_page_currency_dropdown_present(client, session):
    c = await _add(session, "Owned", "1.50", "1")
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="normal"))
    await session.commit()
    await snapshot_prices(session)
    await snapshot_prices(session)

    resp = await client.get(f"/card/{c.scryfall_id}")
    assert 'aria-label="Price history currency"' in resp.text  # the dropdown replaces "(USD)"
    assert ">GBP</option>" in resp.text and ">JPY</option>" in resp.text


@pytest.mark.asyncio
async def test_card_page_converts_history_with_cookie(client, session):
    c = await _add(session, "Owned", "10.00", "1")
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="normal"))
    await session.commit()
    await snapshot_prices(session)
    await snapshot_prices(session)  # two $10 points -> a trend line
    # Pre-seed today's GBP rate so the route converts without any network call.
    today = datetime.datetime.now(datetime.UTC).date()
    session.add(FxRateHistory(code="gbp", date=today, rate=0.5))
    await session.commit()

    resp = await client.get(f"/card/{c.scryfall_id}", cookies={"scryme_hist_currency": "gbp"})
    assert resp.status_code == 200
    assert "£5.00" in resp.text  # 10 USD * 0.5 GBP/USD, with the £ symbol
    assert "Approximate" not in resp.text  # real historical rate, not a fallback


@pytest.mark.asyncio
async def test_card_page_approx_note_when_history_unavailable(client, session, monkeypatch):
    c = await _add(session, "Owned", "10.00", "1")
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="normal"))
    await session.commit()
    await snapshot_prices(session)
    await snapshot_prices(session)

    async def no_history(*_a, **_k):
        return False  # simulate an offline / failed download

    monkeypatch.setattr(fx, "ensure_fx_history", no_history)
    resp = await client.get(f"/card/{c.scryfall_id}", cookies={"scryme_hist_currency": "gbp"})
    assert resp.status_code == 200
    assert "Approximate — historical exchange rates unavailable" in resp.text


@pytest.mark.asyncio
async def test_fx_history_endpoint(client, session, monkeypatch):
    # USD is a no-op; a code with no snapshots is a no-op; a convertible code delegates to ensure.
    assert (await client.post("/card/fx-history", data={"code": "usd"})).json() == {
        "ok": True, "approximate": False}
    assert (await client.post("/card/fx-history", data={"code": "gbp"})).json() == {
        "ok": True, "approximate": False}  # no snapshots yet -> nothing to convert

    c = await _add(session, "Owned", "1.00", "1")
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=1, finish="normal"))
    await session.commit()
    await snapshot_prices(session)

    async def ok_ensure(*_a, **_k):
        return True

    monkeypatch.setattr(fx, "ensure_fx_history", ok_ensure)
    assert (await client.post("/card/fx-history", data={"code": "gbp"})).json() == {
        "ok": True, "approximate": False}

    async def fail_ensure(*_a, **_k):
        return False

    monkeypatch.setattr(fx, "ensure_fx_history", fail_ensure)
    assert (await client.post("/card/fx-history", data={"code": "gbp"})).json() == {
        "ok": False, "approximate": True}


@pytest.mark.asyncio
async def test_card_value_series_downsamples_over_400_points(session):
    c = await _add(session, "Big", "1.00", "1")
    base = datetime.datetime(2020, 1, 1, tzinfo=datetime.UTC)
    snaps = [
        PriceSnapshot(total_usd=0.0, card_count=0, captured_at=base + datetime.timedelta(days=i))
        for i in range(402)
    ]
    session.add_all(snaps)
    await session.flush()
    for i, snap in enumerate(snaps):
        session.add(CardPricePoint(snapshot_id=snap.id, scryfall_id=c.scryfall_id, usd=float(i)))
    await session.commit()

    series = await card_value_series(session, c.scryfall_id, None)
    assert len(series) == 401  # downsampled to 400 + the retained latest
    assert series[-1].total_usd == 401.0
