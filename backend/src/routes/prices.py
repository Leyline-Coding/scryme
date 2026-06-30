"""Price history page: collection value over time and biggest movers."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.price_watch import list_targets
from src.prices import biggest_movers, collection_pl, value_series
from src.templating import templates

router = APIRouter(tags=["prices"])


@router.get("/prices", response_class=HTMLResponse)
async def prices(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    series = await value_series(session)
    return templates.TemplateResponse(
        request,
        "prices.html",
        {
            "series": series,
            "movers": await biggest_movers(session),
            "pl": await collection_pl(session),
            "targets": await list_targets(session),
            "read_only": get_settings().read_only,
        },
    )
