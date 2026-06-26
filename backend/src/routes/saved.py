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
    name = name.strip()[:128]
    if not name:
        raise HTTPException(status_code=400, detail="A name is required.")

    scope = scope if scope == SearchScope.ALL.value else SearchScope.COLLECTION.value
    sort = sort if sort in SORT_KEYS else DEFAULT_SORT
    direction = "desc" if dir == "desc" else "asc"

    existing = await session.scalar(select(SavedSearch).where(SavedSearch.name == name))
    if existing is None:
        session.add(
            SavedSearch(name=name, query=q, scope=scope, sort=sort, direction=direction)
        )
    else:  # same name overwrites (single-user)
        existing.query = q
        existing.scope = scope
        existing.sort = sort
        existing.direction = direction
    await session.commit()

    return RedirectResponse(url=_run_url(q, scope, sort, direction), status_code=303)


@router.post("/saved/{saved_id}/delete")
async def delete_saved(
    saved_id: int,
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    obj = await session.get(SavedSearch, saved_id)
    if obj is not None:
        await session.delete(obj)
        await session.commit()
    return RedirectResponse(url="/search", status_code=303)
