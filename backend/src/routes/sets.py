"""Set completion tracker + set-release calendar."""

from __future__ import annotations

import datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.set_calendar import refresh_sets, set_calendar
from src.sets import set_detail
from src.templating import templates

router = APIRouter(tags=["sets"])


@router.get("/sets")
async def list_sets() -> RedirectResponse:
    # The set-completion index is now the Sets view of the /collection Stats tab.
    return RedirectResponse(url="/collection?tab=stats&view=sets", status_code=307)


@router.get("/calendar", response_class=HTMLResponse)
async def calendar(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Upcoming + recently-released sets (#178). Syncs from Scryfall on a 24h cache."""
    await refresh_sets(session)  # cache-guarded; no-op if fresh or offline
    return templates.TemplateResponse(
        request, "set_calendar.html",
        {"cal": await set_calendar(session), "today": datetime.date.today()},
    )


@router.get("/sets/{set_code}", response_class=HTMLResponse)
async def set_page(
    set_code: str, request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    detail = await set_detail(session, set_code)
    if detail is None:
        raise HTTPException(status_code=404, detail="Unknown set")
    return templates.TemplateResponse(request, "set_detail.html", {"set": detail})
