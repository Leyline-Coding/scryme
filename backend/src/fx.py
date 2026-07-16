"""Foreign-exchange rates for display-currency conversion (#232).

Scryfall gives only USD/EUR prices. Other display currencies (GBP/CAD/AUD/JPY) are shown by
converting the USD price with rates from Frankfurter (``api.frankfurter.dev`` — free, no API key,
ECB reference rates, refreshed daily). Rates are cached in the ``fx_rate`` table and mirrored into
an in-process dict so ``currency.unit_price`` can convert without a per-call DB query.

This module must NOT import ``src.currency`` — ``currency`` imports ``fx.rate``, so keeping the
converted-currency list here avoids a circular import.
"""

from __future__ import annotations

import datetime

import httpx
import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import SessionLocal
from src.models import FxRate

log = structlog.get_logger()

# Display currencies shown by converting from USD. usd/eur are native Scryfall keys — never here.
_FX_CODES = ["gbp", "cad", "aud", "jpy"]
_FRANKFURTER_URL = "https://api.frankfurter.dev/v1/latest"
_TIMEOUT = 15.0

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
