"""Saved searches: create, run, and delete named searches.

Single-user, so a name is the unique key — saving an existing name overwrites it. Mutations are
blocked in read-only (demo) mode, mirroring uploads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.models import SavedSearch
from src.search import SearchScope
from src.search.engine import DEFAULT_SORT, SORT_KEYS

router = APIRouter(tags=["saved"])


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


def _run_url(query: str, scope: str, sort: str, direction: str) -> str:
    from urllib.parse import urlencode

    return "/search?" + urlencode(
        {"q": query, "scope": scope, "sort": sort, "dir": direction}
    )


async def list_saved(session: AsyncSession) -> list[SavedSearch]:
    """All saved searches, newest first (used by the search page header)."""
    rows = await session.execute(select(SavedSearch).order_by(SavedSearch.created_at.desc()))
    return list(rows.scalars().all())


async def upsert_saved(
    session: AsyncSession, name: str, query: str, scope: str, sort: str, direction: str
) -> SavedSearch:
    """Create or overwrite a saved search by name, normalizing scope/sort/direction.

    Single-user, so the name is the unique key: saving an existing name overwrites it rather
    than erroring. Shared by the HTML form and the JSON API. Raises 400 on a blank name.
    """
    name = name.strip()[:128]
    if not name:
        raise HTTPException(status_code=400, detail="A name is required.")

    scope = scope if scope == SearchScope.ALL.value else SearchScope.COLLECTION.value
    sort = sort if sort in SORT_KEYS else DEFAULT_SORT
    direction = "desc" if direction == "desc" else "asc"

    obj = await session.scalar(select(SavedSearch).where(SavedSearch.name == name))
    if obj is None:
        obj = SavedSearch(name=name, query=query, scope=scope, sort=sort, direction=direction)
        session.add(obj)
    else:  # same name overwrites (single-user)
        obj.query = query
        obj.scope = scope
        obj.sort = sort
        obj.direction = direction
    await session.commit()
    await session.refresh(obj)
    return obj


async def delete_saved_by_id(session: AsyncSession, saved_id: int) -> bool:
    """Delete a saved search; True if one was removed. Missing is not an error (idempotent)."""
    obj = await session.get(SavedSearch, saved_id)
    if obj is None:
        return False
    await session.delete(obj)
    await session.commit()
    return True


@router.post("/saved")
async def create_saved(
    name: str = Form(...),
    q: str = Form(""),
    scope: str = Form(SearchScope.COLLECTION.value),
    sort: str = Form(DEFAULT_SORT),
    dir: str = Form("asc"),
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    obj = await upsert_saved(session, name, q, scope, sort, dir)
    return RedirectResponse(
        url=_run_url(obj.query, obj.scope, obj.sort, obj.direction), status_code=303
    )


@router.post("/saved/{saved_id}/delete")
async def delete_saved(
    saved_id: int,
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    await delete_saved_by_id(session, saved_id)
    return RedirectResponse(url="/search", status_code=303)


@router.get("/saved/alerts")
async def saved_alerts(session: AsyncSession = Depends(get_session)):
    """Total unviewed new matches across saved searches (polled by the notification script)."""
    from src.saved_alerts import total_new_matches

    return {"total": await total_new_matches(session)}


@router.get("/saved/{saved_id}/open")
async def open_saved(saved_id: int, session: AsyncSession = Depends(get_session)):
    """Run a saved search and mark its new matches as seen (clears the alert badge)."""
    from src.saved_alerts import clear_new

    obj = await session.get(SavedSearch, saved_id)
    if obj is None:
        return RedirectResponse(url="/", status_code=303)
    await clear_new(session, saved_id)
    return RedirectResponse(
        url=_run_url(obj.query, obj.scope, obj.sort, obj.direction), status_code=303
    )
