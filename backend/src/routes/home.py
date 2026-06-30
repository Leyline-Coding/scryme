"""Home page.

Shows the upload prompt when the collection is empty, otherwise the Scryfall-style search
bar. The actual search and upload behavior land in later phases; Phase 0 renders the shell
and picks the state from the collection count.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.templating import templates

router = APIRouter(tags=["home"])


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    from src.models import Card, CollectionCard

    count = await session.scalar(select(func.count()).select_from(CollectionCard))
    settings = get_settings()
    # When the card database itself is empty (a fresh desktop install), the collection can't be
    # searched or matched against — gate the home page on a one-time first-run ingest instead.
    card_count = await session.scalar(select(func.count()).select_from(Card))
    needs_cards = not card_count and not settings.read_only

    # Saved-search alerts (#58) + price-watch alerts (#88), surfaced on the home page.
    from src.price_watch import triggered_targets
    from src.saved_alerts import searches_with_new

    alerts = [] if settings.read_only else await searches_with_new(session)
    price_alerts = [] if settings.read_only else await triggered_targets(session)
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "has_collection": bool(count),
            "read_only": settings.read_only,
            "needs_cards": needs_cards,
            "alerts": alerts,
            "price_alerts": price_alerts,
        },
    )
