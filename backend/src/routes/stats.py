"""Collection stats / insights dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.stats import collection_stats
from src.templating import templates

router = APIRouter(tags=["stats"])


@router.get("/stats", response_class=HTMLResponse)
async def stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "stats.html",
        {"stats": await collection_stats(session), "read_only": get_settings().read_only},
    )
