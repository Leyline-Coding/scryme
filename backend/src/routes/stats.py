"""Collection stats / insights dashboard."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.currency import get_currency, info
from src.db import get_session
from src.prices import build_value_chart, value_series
from src.stats import collection_growth, collection_stats
from src.templating import templates

router = APIRouter(tags=["stats"])


@router.get("/stats", response_class=HTMLResponse)
async def stats(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    currency = get_currency(request)
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "stats": await collection_stats(session, currency),
            "value_chart": build_value_chart(await value_series(session)),
            "growth": await collection_growth(session, currency),
            "cur": info(currency),
            "read_only": get_settings().read_only,
        },
    )
