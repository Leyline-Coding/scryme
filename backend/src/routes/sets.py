"""Set completion tracker: how complete each owned set is, and what's missing."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.db import get_session
from src.sets import set_detail, set_progress
from src.templating import templates

router = APIRouter(tags=["sets"])


@router.get("/sets", response_class=HTMLResponse)
async def list_sets(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "sets.html", {"sets": await set_progress(session)}
    )


@router.get("/sets/{set_code}", response_class=HTMLResponse)
async def set_page(
    set_code: str, request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    detail = await set_detail(session, set_code)
    if detail is None:
        raise HTTPException(status_code=404, detail="Unknown set")
    return templates.TemplateResponse(request, "set_detail.html", {"set": detail})
