"""Set-release calendar (#178): sync Scryfall's set list and surface upcoming/recent releases.

Scryfall's ``/sets`` endpoint is one small request, cached ≥24h (per API policy) in ``set_release``.
The calendar view then reads locally, so it works offline once synced.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass, field

import structlog
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.models import SetRelease
from src.scryfall.client import ScryfallClient, ScryfallError

log = structlog.get_logger()

_CACHE_HOURS = 24
_RECENT_LIMIT = 24  # how many already-released sets to show


def _parse_date(value) -> datetime.date | None:
    try:
        return datetime.date.fromisoformat(value) if value else None
    except (ValueError, TypeError):
        return None


async def _is_fresh(session: AsyncSession) -> bool:
    last = await session.scalar(select(func.max(SetRelease.synced_at)))
    if last is None:
        return False
    age = datetime.datetime.now(datetime.UTC) - last
    return age < datetime.timedelta(hours=_CACHE_HOURS)


async def refresh_sets(session: AsyncSession, *, force: bool = False, client=None) -> int:
    """Sync the set list from Scryfall (respecting the 24h cache). Returns rows upserted.

    Gracefully returns 0 (keeping any cached rows) if the fetch fails / we're offline.
    """
    if not force and await _is_fresh(session):
        return 0
    base = get_settings().scryfall_api_base
    try:
        if client is not None:
            payload = await client.get_json(f"{base}/sets")
        else:
            async with ScryfallClient() as c:
                payload = await c.get_json(f"{base}/sets")
    except ScryfallError as exc:
        log.warning("set_calendar.refresh_failed", error=str(exc))
        return 0

    now = datetime.datetime.now(datetime.UTC)
    count = 0
    for s in payload.get("data", []):
        code = s.get("code")
        if not code:
            continue
        values = {
            "code": code,
            "name": s.get("name") or code.upper(),
            "released_at": _parse_date(s.get("released_at")),
            "set_type": s.get("set_type"),
            "card_count": int(s.get("card_count") or 0),
            "digital": bool(s.get("digital")),
            "icon_uri": s.get("icon_svg_uri"),
            "synced_at": now,
        }
        stmt = pg_insert(SetRelease).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SetRelease.code],
            set_={k: values[k] for k in values if k != "code"},
        )
        await session.execute(stmt)
        count += 1
    await session.commit()
    log.info("set_calendar.synced", sets=count)
    return count


@dataclass
class SetCalendar:
    upcoming: list[SetRelease] = field(default_factory=list)  # released_at >= today, ascending
    recent: list[SetRelease] = field(default_factory=list)    # released_at < today, descending
    synced_at: datetime.datetime | None = None


async def set_calendar(session: AsyncSession, *, today: datetime.date | None = None) -> SetCalendar:
    """Upcoming and recently-released paper sets, from the local ``set_release`` cache."""
    today = today or datetime.date.today()
    rows = (
        await session.execute(
            select(SetRelease)
            .where(SetRelease.digital.is_(False), SetRelease.released_at.is_not(None))
            .order_by(SetRelease.released_at)
        )
    ).scalars().all()
    upcoming = [s for s in rows if s.released_at >= today]
    recent = [s for s in reversed(rows) if s.released_at < today][:_RECENT_LIMIT]
    synced = await session.scalar(select(func.max(SetRelease.synced_at)))
    return SetCalendar(upcoming=upcoming, recent=recent, synced_at=synced)
