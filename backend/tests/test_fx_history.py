"""Historical FX rates for the per-card price-history currency chart (#233). Frankfurter mocked."""

import datetime
from datetime import UTC, date
from datetime import datetime as dt

import httpx
import pytest
from sqlalchemy import select
from src import fx
from src.models import FxRateHistory
from src.prices import _CardPoint, convert_card_series


def _ts_client(series: dict[str, float]) -> httpx.AsyncClient:
    """Mock Frankfurter time-series: {"YYYY-MM-DD": value} keyed under the requested symbol."""
    def handler(request):
        symbol = request.url.params.get("symbols")
        rates = {day: {symbol: val} for day, val in series.items()}
        return httpx.Response(200, json={"base": "USD", "rates": rates})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


_D1, _D2, _D3 = date(2024, 1, 2), date(2024, 1, 3), date(2024, 3, 1)


# --- download_fx_history --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_populates_rows(session):
    client = _ts_client({"2024-01-02": 0.79, "2024-01-03": 0.80})
    n = await fx.download_fx_history(session, "gbp", date(2024, 1, 1), date(2024, 1, 3), client)
    assert n == 2
    rows = (
        await session.execute(select(FxRateHistory).order_by(FxRateHistory.date))
    ).scalars().all()
    got = [(r.date.isoformat(), r.rate) for r in rows]
    assert got == [("2024-01-02", 0.79), ("2024-01-03", 0.80)]


@pytest.mark.asyncio
async def test_download_ignores_conflicts(session):
    await fx.download_fx_history(
        session, "gbp", _D1, _D2, _ts_client({"2024-01-02": 0.79})
    )
    # A second fetch re-covering 01-02 with a different value + a new day: the old day is untouched.
    await fx.download_fx_history(
        session, "gbp", _D1, _D2, _ts_client({"2024-01-02": 9.99, "2024-01-03": 0.81})
    )
    rows = dict(
        (r.date.isoformat(), r.rate)
        for r in (await session.execute(select(FxRateHistory))).scalars()
    )
    assert rows == {"2024-01-02": 0.79, "2024-01-03": 0.81}  # 01-02 kept, 01-03 added


@pytest.mark.asyncio
async def test_download_failure_returns_zero(session):
    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(500)))
    n = await fx.download_fx_history(session, "gbp", _D1, _D2, bad)
    assert n == 0
    assert (await session.execute(select(FxRateHistory))).first() is None


@pytest.mark.asyncio
async def test_download_empty_series_returns_zero(session):
    n = await fx.download_fx_history(session, "gbp", _D1, _D2, _ts_client({}))
    assert n == 0


@pytest.mark.asyncio
async def test_download_without_client_uses_httpx(session, monkeypatch):
    real_client = httpx.AsyncClient

    def fake_client(*_a, **_k):
        return real_client(transport=httpx.MockTransport(
            lambda r: httpx.Response(200, json={
                "base": "USD",
                "rates": {"2024-01-02": {r.url.params.get("symbols"): 0.79}},
            })
        ))

    monkeypatch.setattr(fx.httpx, "AsyncClient", fake_client)
    n = await fx.download_fx_history(session, "gbp", _D1, _D2)  # no client -> opens its own
    assert n == 1


# --- ensure_fx_history ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_ensure_downloads_when_missing(session):
    ok = await fx.ensure_fx_history(
        session, "gbp", date(2024, 1, 1), _ts_client({"2024-01-02": 0.79})
    )
    assert ok
    assert (await session.execute(select(FxRateHistory))).first() is not None


@pytest.mark.asyncio
async def test_ensure_unknown_and_usd_codes_return_false(session):
    assert await fx.ensure_fx_history(session, "usd", date(2024, 1, 1)) is False
    assert await fx.ensure_fx_history(session, "xxx", date(2024, 1, 1)) is False


@pytest.mark.asyncio
async def test_ensure_skips_download_when_fresh(session):
    today = datetime.datetime.now(UTC).date()
    session.add(FxRateHistory(code="gbp", date=today, rate=0.5))
    await session.commit()
    # A client that would fail if hit — but a fresh newest row means no download happens.
    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(500)))
    ok = await fx.ensure_fx_history(session, "gbp", date(2024, 1, 1), bad)
    assert ok
    row = await session.get(FxRateHistory, ("gbp", today))
    assert row.rate == 0.5  # untouched


@pytest.mark.asyncio
async def test_ensure_tops_up_stale_tail(session):
    today = datetime.datetime.now(UTC).date()
    stale = today - datetime.timedelta(days=30)
    session.add(FxRateHistory(code="gbp", date=stale, rate=0.5))
    await session.commit()
    ok = await fx.ensure_fx_history(
        session, "gbp", date(2024, 1, 1), _ts_client({today.isoformat(): 0.8})
    )
    assert ok
    assert await session.get(FxRateHistory, ("gbp", today)) is not None  # tail fetched


# --- fx_history_points + rate_on ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_fx_history_points_sorted(session):
    session.add_all([
        FxRateHistory(code="gbp", date=_D3, rate=0.81),
        FxRateHistory(code="gbp", date=_D1, rate=0.79),
        FxRateHistory(code="cad", date=_D1, rate=1.40),  # other code excluded
    ])
    await session.commit()
    pts = await fx.fx_history_points(session, "gbp")
    assert [d for d, _ in pts] == [_D1, _D3]


def test_rate_on_carry_forward_and_fallback():
    pts = [(_D1, 0.79), (_D3, 0.81)]
    assert fx.rate_on(pts, date(2024, 2, 1), 9.0) == 0.79   # nearest date <= target
    assert fx.rate_on(pts, _D3, 9.0) == 0.81                # exact match
    assert fx.rate_on(pts, date(2023, 1, 1), 9.0) == 0.79   # before series -> oldest known
    assert fx.rate_on([], date(2024, 1, 1), 9.0) == 9.0     # nothing stored -> fallback


# --- convert_card_series --------------------------------------------------------------------------

def _pt(usd, y, m, d):
    return _CardPoint(total_usd=usd, captured_at=dt(y, m, d, tzinfo=UTC))


def test_convert_uses_date_matched_rate():
    pts = [_pt(10.0, 2024, 1, 2), _pt(20.0, 2024, 3, 1)]
    hist = [(date(2024, 1, 1), 0.5), (date(2024, 2, 1), 0.6)]
    out = convert_card_series(pts, "gbp", hist, 0.9)
    assert [round(p.total_usd, 2) for p in out] == [5.0, 12.0]  # 10*0.5, 20*0.6


def test_convert_usd_is_passthrough():
    pts = [_pt(10.0, 2024, 1, 2)]
    assert convert_card_series(pts, "usd", [], 1.0) is pts


def test_convert_falls_back_to_current_rate_without_history():
    pts = [_pt(10.0, 2024, 1, 2)]
    out = convert_card_series(pts, "gbp", [], 0.9)
    assert round(out[0].total_usd, 2) == 9.0  # 10 * current-rate fallback


# --- topup_fx_history + earliest_snapshot_date ----------------------------------------------------

@pytest.mark.asyncio
async def test_topup_only_touches_existing_codes(session, monkeypatch):
    from src.models import PriceSnapshot

    session.add(PriceSnapshot(total_usd=1.0, card_count=1))
    session.add(FxRateHistory(code="gbp", date=date(2024, 1, 1), rate=0.5))  # stale -> needs tail
    await session.commit()
    calls = []

    async def fake_dl(_sess, code, _start, _end, _client=None):
        calls.append(code)
        return 0

    monkeypatch.setattr(fx, "download_fx_history", fake_dl)
    await fx.topup_fx_history(session)
    assert calls == ["gbp"]  # only the currency already in use, never cad/aud/jpy/eur


@pytest.mark.asyncio
async def test_topup_noop_without_snapshots(session, monkeypatch):
    session.add(FxRateHistory(code="gbp", date=date(2024, 1, 1), rate=0.5))  # history, no snapshot
    await session.commit()
    calls = []

    async def fake_dl(*_a, **_k):
        calls.append(1)
        return 0

    monkeypatch.setattr(fx, "download_fx_history", fake_dl)
    await fx.topup_fx_history(session)  # earliest_snapshot_date is None -> returns before fetching
    assert calls == []


@pytest.mark.asyncio
async def test_topup_noop_without_history(session, monkeypatch):
    calls = []

    async def fake_dl(*_a, **_k):
        calls.append(1)
        return 0

    monkeypatch.setattr(fx, "download_fx_history", fake_dl)
    await fx.topup_fx_history(session)
    assert calls == []  # no stored history codes -> nothing fetched


@pytest.mark.asyncio
async def test_topup_opens_own_session():
    await fx.topup_fx_history()  # empty DB -> returns without opening a network client


@pytest.mark.asyncio
async def test_earliest_snapshot_date(session):
    from src.models import PriceSnapshot
    from src.prices import earliest_snapshot_date

    assert await earliest_snapshot_date(session) is None
    session.add(PriceSnapshot(total_usd=1.0, card_count=0, captured_at=dt(2020, 1, 1, tzinfo=UTC)))
    await session.commit()
    assert await earliest_snapshot_date(session) == date(2020, 1, 1)
