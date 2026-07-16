"""FX-rate layer for converted display currencies (#232). No network — Frankfurter is mocked."""

import datetime

import httpx
import pytest
import pytest_asyncio
from sqlalchemy import delete, select
from src import fx
from src.models import FxRate

_FULL = {"GBP": 0.80, "CAD": 1.40, "AUD": 1.50, "JPY": 150.0}


def _frankfurter(rates: dict) -> httpx.AsyncClient:
    def handler(_request):
        return httpx.Response(200, json={"amount": 1, "base": "USD", "rates": rates})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest_asyncio.fixture(autouse=True)
async def _clean_fx(session):
    # fx_rate isn't in conftest's truncation list, so isolate it here.
    await session.execute(delete(FxRate))
    await session.commit()
    fx.FX_RATES.clear()
    yield
    fx.FX_RATES.clear()


async def _codes(session) -> dict:
    return {r.code: r.rate for r in (await session.execute(select(FxRate))).scalars()}


@pytest.mark.asyncio
async def test_refresh_populates_rows_and_cache(session):
    # Empty table -> stale -> fetches without needing force.
    n = await fx.refresh_fx_rates(session, _frankfurter(_FULL))
    assert n == 4
    assert fx.rate("gbp") == 0.80
    assert await _codes(session) == {"gbp": 0.80, "cad": 1.40, "aud": 1.50, "jpy": 150.0}


@pytest.mark.asyncio
async def test_staleness_guard_skips_fresh(session):
    session.add(FxRate(code="gbp", rate=0.9, updated_at=datetime.datetime.now(datetime.UTC)))
    await session.commit()
    n = await fx.refresh_fx_rates(session, _frankfurter({"GBP": 0.80}))  # not forced
    assert n == 0
    assert fx.rate("gbp") == 0.9  # unchanged; loaded from DB


@pytest.mark.asyncio
async def test_stale_rows_refresh_and_update_existing(session):
    session.add(FxRate(code="gbp", rate=0.9,
                       updated_at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)))
    await session.commit()
    n = await fx.refresh_fx_rates(session, _frankfurter(_FULL))  # stale -> refreshes
    assert n == 4
    assert fx.rate("gbp") == 0.80  # existing row updated
    assert fx.rate("cad") == 1.40  # new row added


@pytest.mark.asyncio
async def test_fetch_failure_keeps_old_rows(session):
    session.add(FxRate(code="gbp", rate=0.9,
                       updated_at=datetime.datetime(2000, 1, 1, tzinfo=datetime.UTC)))
    await session.commit()
    bad = httpx.AsyncClient(transport=httpx.MockTransport(lambda _r: httpx.Response(500)))
    n = await fx.refresh_fx_rates(session, bad, force=True)
    assert n == 0
    row = await session.get(FxRate, "gbp")
    assert row.rate == 0.9  # untouched


@pytest.mark.asyncio
async def test_refresh_without_client_uses_httpx(session, monkeypatch):
    # Exercise the client-is-None path by pointing httpx.AsyncClient at a MockTransport. Capture the
    # real class first so the mock builder doesn't recurse into the patched one.
    real_client = httpx.AsyncClient

    def fake_client(*_a, **_k):
        return real_client(transport=httpx.MockTransport(
            lambda _r: httpx.Response(200, json={"amount": 1, "base": "USD", "rates": _FULL})
        ))

    monkeypatch.setattr(fx.httpx, "AsyncClient", fake_client)
    n = await fx.refresh_fx_rates(session, None, force=True)
    assert n == 4


@pytest.mark.asyncio
async def test_refresh_opens_own_session(monkeypatch):
    # session=None opens a SessionLocal; pass a client so no network happens.
    n = await fx.refresh_fx_rates(client=_frankfurter(_FULL), force=True)
    assert n == 4
    assert fx.rate("jpy") == 150.0


@pytest.mark.asyncio
async def test_load_rates_and_rate(session):
    session.add(FxRate(code="cad", rate=1.33, updated_at=datetime.datetime.now(datetime.UTC)))
    await session.commit()
    await fx.load_rates(session)
    assert fx.rate("cad") == 1.33
    assert fx.rate("xxx") is None


@pytest.mark.asyncio
async def test_load_rates_opens_own_session(session):
    session.add(FxRate(code="aud", rate=1.51, updated_at=datetime.datetime.now(datetime.UTC)))
    await session.commit()
    await fx.load_rates()  # no session arg -> opens its own
    assert fx.rate("aud") == 1.51


@pytest.mark.asyncio
async def test_lifespan_survives_fx_load_failure(monkeypatch):
    # The startup FX load is best-effort: a failure is logged, not raised.
    import src.main as main
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "environment", "test")
    monkeypatch.setattr(main, "shutdown_scheduler", lambda: None)

    async def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(fx, "load_rates", boom)
    async with main.lifespan(main.app):
        pass  # did not raise despite load_rates failing
