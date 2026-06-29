"""Saved-search alerts (#58): re-run saved searches after ingest, surface newly-matching cards.

The card DB changes every ingest (new sets, price/legality shifts), so a saved search that matched
N cards yesterday may match more today. After each ingest we re-evaluate every saved search, diff
its match set against the baseline stored last time, and remember the newly-matching ids so the UI
can show a "What's new" badge. The first evaluation only records a baseline (no alert spam).
"""

from __future__ import annotations

import structlog
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import SessionLocal
from src.models import SavedSearch
from src.search import SearchScope
from src.search.engine import build_search

log = structlog.get_logger()

# Don't track alerts for very broad searches, and bound how many new ids we store per search.
MAX_MATCHES_TRACKED = 20_000
MAX_NEW_STORED = 500


async def _match_ids(session: AsyncSession, saved: SavedSearch) -> set[str] | None:
    """The full set of scryfall_ids a saved search currently matches (None if it's too broad)."""
    scope = SearchScope.ALL if saved.scope == SearchScope.ALL.value else SearchScope.COLLECTION
    base = build_search(saved.query, scope, sort="name", descending=False).subquery()
    rows = await session.execute(select(base.c.scryfall_id))
    ids = {str(r[0]) for r in rows}
    return None if len(ids) > MAX_MATCHES_TRACKED else ids


async def evaluate_alerts(session: AsyncSession | None = None) -> int:
    """Re-evaluate every saved search; accumulate newly-matching ids. Returns total new matches."""
    own_session = session is None
    session = session or SessionLocal()
    total_new = 0
    try:
        saveds = (await session.execute(select(SavedSearch))).scalars().all()
        for saved in saveds:
            try:
                current = await _match_ids(session, saved)
            except Exception as exc:  # noqa: BLE001 - a bad saved query shouldn't break the rest
                log.warning("saved_alerts.eval_failed", name=saved.name, error=str(exc))
                continue
            if current is None:
                continue
            if saved.seen_ids is None:
                saved.seen_ids = sorted(current)  # first run: baseline only
                continue
            newly = current - set(saved.seen_ids)
            if newly:
                merged = set(saved.new_ids or []) | newly
                saved.new_ids = sorted(merged)[:MAX_NEW_STORED]
                total_new += len(newly)
            saved.seen_ids = sorted(current)
        await session.commit()
    finally:
        if own_session:
            await session.close()
    if total_new:
        log.info("saved_alerts.new_matches", total=total_new)
    return total_new


async def total_new_matches(session: AsyncSession) -> int:
    """Sum of unviewed new matches across all saved searches (for the home badge / notification)."""
    saveds = (await session.execute(select(SavedSearch))).scalars().all()
    return sum(len(s.new_ids or []) for s in saveds)


async def searches_with_new(session: AsyncSession) -> list[dict]:
    """Saved searches that have unviewed new matches, for the home 'What's new' panel."""
    saveds = (await session.execute(select(SavedSearch))).scalars().all()
    return [
        {"id": s.id, "name": s.name, "count": len(s.new_ids)}
        for s in saveds
        if s.new_ids
    ]


async def clear_new(session: AsyncSession, saved_id: int) -> None:
    """Mark a saved search's new matches as seen (called when the user opens it)."""
    saved = await session.get(SavedSearch, saved_id)
    if saved is not None and saved.new_ids:
        saved.new_ids = []
        await session.commit()
