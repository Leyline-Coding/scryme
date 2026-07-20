"""Foreign-exchange rates for display-currency conversion (#232).

Scryfall gives only USD/EUR prices. Other display currencies (GBP/CAD/AUD/JPY) are shown by
converting the USD price with rates from Frankfurter (``api.frankfurter.dev`` — free, no API key,
ECB reference rates, refreshed daily). Rates are cached in the ``fx_rate`` table and mirrored into
an in-process dict so ``currency.unit_price`` can convert without a per-call DB query.

This module must NOT import ``src.currency`` — ``currency`` imports ``fx.rate``, so keeping the
converted-currency list here avoids a circular import.
"""

from __future__ import annotations

import bisect
import datetime

import httpx
import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import SessionLocal
from src.models import FxRate, FxRateHistory

log = structlog.get_logger()

# Display currencies shown by converting from USD. usd/eur are native Scryfall keys — never here.
_FX_CODES = ["gbp", "cad", "aud", "jpy"]
# Currencies the *historical* per-card chart can convert USD into (#233). EUR joins the FX codes
# here because per-card history is stored USD-only — even EUR needs USD->EUR rates for past points.
HIST_CODES = ["eur", *_FX_CODES]
_FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
# Time-series endpoint: /v1/<start>..<end>?base=USD&symbols=<CODE> -> {"rates": {"<day>": {..}}}.
_FRANKFURTER_TS = "https://api.frankfurter.dev/v1/{start}..{end}"
_TIMEOUT = 15.0
# Re-fetch the recent tail once the newest stored day is older than this (snapshots are ~monthly).
_TAIL_STALE_DAYS = 7

# 1 USD -> code. Populated by load_rates() at startup and refresh_fx_rates() on the daily schedule.
FX_RATES: dict[str, float] = {}


def rate(code: str) -> float | None:
    """USD->``code`` multiplier from the in-memory cache, or None if unknown/not yet loaded."""
    return FX_RATES.get(code)


async def load_rates(session: AsyncSession | None = None) -> None:
    """Mirror the ``fx_rate`` table into ``FX_RATES`` (cheap; called at startup)."""
    if session is None:
        async with SessionLocal() as s:
            await load_rates(s)
        return
    rows = (await session.execute(select(FxRate.code, FxRate.rate))).all()
    FX_RATES.clear()
    FX_RATES.update(dict(rows))


def _is_stale(rows: list[FxRate], min_hours: int) -> bool:
    newest = max((r.updated_at for r in rows), default=None)
    if newest is None:
        return True
    return datetime.datetime.now(datetime.UTC) - newest >= datetime.timedelta(hours=min_hours)


async def _fetch_with(client: httpx.AsyncClient) -> dict[str, float]:
    resp = await client.get(
        _FRANKFURTER_URL, params={"base": "USD", "symbols": ",".join(c.upper() for c in _FX_CODES)}
    )
    resp.raise_for_status()
    rates = resp.json().get("rates") or {}
    return {c: float(rates[c.upper()]) for c in _FX_CODES if c.upper() in rates}


async def _fetch_rates(client: httpx.AsyncClient | None) -> dict[str, float]:
    """Fetch USD->code rates from Frankfurter; return {} on any failure (caller keeps old rows)."""
    try:
        if client is not None:
            return await _fetch_with(client)
        ua = get_settings().scryfall_user_agent
        async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": ua}) as c:
            return await _fetch_with(c)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("fx.refresh.failed", error=str(exc))
        return {}


async def refresh_fx_rates(
    session: AsyncSession | None = None,
    client: httpx.AsyncClient | None = None,
    *,
    force: bool = False,
) -> int:
    """Refresh rates from Frankfurter unless recently updated. Returns the number of rates written.

    Best-effort: a fetch failure leaves existing rows untouched and returns 0.
    """
    if session is None:
        async with SessionLocal() as s:
            return await refresh_fx_rates(s, client, force=force)

    existing = list((await session.execute(select(FxRate))).scalars())
    if not force and not _is_stale(existing, get_settings().fx_refresh_min_hours):
        await load_rates(session)
        return 0

    fetched = await _fetch_rates(client)
    if not fetched:
        return 0

    by_code = {r.code: r for r in existing}
    now = datetime.datetime.now(datetime.UTC)
    for code, value in fetched.items():
        row = by_code.get(code)
        if row is None:
            session.add(FxRate(code=code, rate=value, updated_at=now))
        else:
            row.rate, row.updated_at = value, now
    await session.commit()
    await load_rates(session)
    return len(fetched)


# --- Historical rates (per-card price-history chart, #233) --------------------------------------


async def _fetch_ts(
    client: httpx.AsyncClient, code: str, start: datetime.date, end: datetime.date
) -> dict[datetime.date, float]:
    """Fetch a USD->code daily series from Frankfurter's time-series endpoint (business days)."""
    url = _FRANKFURTER_TS.format(start=start.isoformat(), end=end.isoformat())
    resp = await client.get(url, params={"base": "USD", "symbols": code.upper()})
    resp.raise_for_status()
    rates = resp.json().get("rates") or {}
    out: dict[datetime.date, float] = {}
    for day, by_symbol in rates.items():
        value = (by_symbol or {}).get(code.upper())
        if value is not None:
            out[datetime.date.fromisoformat(day)] = float(value)
    return out


async def download_fx_history(
    session: AsyncSession,
    code: str,
    start: datetime.date,
    end: datetime.date,
    client: httpx.AsyncClient | None = None,
) -> int:
    """Download USD->``code`` daily rates for ``start..end`` and upsert them; return rows written.

    Best-effort: any network/parse failure returns 0 and leaves stored rows untouched (past ECB
    rates never change, so already-stored days need no update — conflicts are ignored).
    """
    try:
        if client is not None:
            fetched = await _fetch_ts(client, code, start, end)
        else:
            ua = get_settings().scryfall_user_agent
            async with httpx.AsyncClient(timeout=_TIMEOUT, headers={"User-Agent": ua}) as c:
                fetched = await _fetch_ts(c, code, start, end)
    except (httpx.HTTPError, ValueError, KeyError) as exc:
        log.warning("fx.history.failed", code=code, error=str(exc))
        return 0
    if not fetched:
        return 0
    rows = [{"code": code, "date": day, "rate": rate} for day, rate in fetched.items()]
    stmt = pg_insert(FxRateHistory).values(rows).on_conflict_do_nothing(
        index_elements=["code", "date"]
    )
    await session.execute(stmt)
    await session.commit()
    return len(rows)


async def ensure_fx_history(
    session: AsyncSession,
    code: str,
    start_date: datetime.date,
    client: httpx.AsyncClient | None = None,
) -> bool:
    """Make sure ``code`` has historical rates back to ``start_date``; download the gap if not.

    Idempotent and cheap on the hot path: a populated code only issues one ``max(date)`` query.
    Downloads the full span on first use, then tops up the recent tail once it goes stale. Returns
    True when usable history exists afterwards (so the caller can fall back to the current rate
    when a download fails and none is stored yet). USD and unknown codes return False (no history).
    """
    if code not in HIST_CODES:
        return False
    today = datetime.datetime.now(datetime.UTC).date()
    newest = await session.scalar(
        select(func.max(FxRateHistory.date)).where(FxRateHistory.code == code)
    )
    if newest is None:
        await download_fx_history(session, code, start_date, today, client)
    elif newest < today - datetime.timedelta(days=_TAIL_STALE_DAYS):
        await download_fx_history(session, code, newest, today, client)
    exists = await session.scalar(
        select(FxRateHistory.code).where(FxRateHistory.code == code).limit(1)
    )
    return exists is not None


async def topup_fx_history(session: AsyncSession | None = None) -> None:
    """Refresh the recent tail of any *already-downloaded* history currencies (scheduler, #233).

    Only touches codes a visitor has actually viewed the chart in (rows already exist), so the daily
    job never eagerly downloads decades of data for currencies nobody uses. Best-effort.
    """
    if session is None:
        async with SessionLocal() as s:
            await topup_fx_history(s)
        return
    codes = list(
        (await session.execute(select(FxRateHistory.code).distinct())).scalars()
    )
    if not codes:
        return
    from src.prices import earliest_snapshot_date  # local: avoid an import cycle at module load

    start = await earliest_snapshot_date(session)
    if start is None:
        return
    for code in codes:
        await ensure_fx_history(session, code, start)


async def fx_history_points(
    session: AsyncSession, code: str
) -> list[tuple[datetime.date, float]]:
    """All stored (date, rate) pairs for ``code``, oldest-first, for date-matched conversion."""
    rows = (
        await session.execute(
            select(FxRateHistory.date, FxRateHistory.rate)
            .where(FxRateHistory.code == code)
            .order_by(FxRateHistory.date)
        )
    ).all()
    return [(d, r) for d, r in rows]


def rate_on(
    points: list[tuple[datetime.date, float]], on_date: datetime.date, fallback: float
) -> float:
    """USD->code rate effective on ``on_date``: the newest stored day at or before it (carry-forward
    over weekends/holidays), the oldest stored day if ``on_date`` predates the series, else
    ``fallback`` (used when nothing is stored)."""
    if not points:
        return fallback
    dates = [d for d, _ in points]
    i = bisect.bisect_right(dates, on_date)
    if i == 0:
        return points[0][1]  # before the series starts -> oldest known rate
    return points[i - 1][1]
