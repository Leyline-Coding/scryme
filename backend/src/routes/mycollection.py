"""The consolidated "My Collection" page: Stats / Decks / Binders / Wishlist / Checklists / Trade
as tabs on a single ``/collection`` route, with content swapped server-side by ``?tab=``.

Each tab loads only its own data (reusing the same services the standalone pages used) and renders
a ``_col_<tab>`` partial inside the shared shell. The old per-feature routes redirect here.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.binder_service import binder_summaries
from src.box_service import box_summaries, other_locations
from src.checklists import Checklist
from src.config import get_settings
from src.currency import get_currency, info
from src.db import get_session
from src.models import Deck
from src.prices import build_value_chart, value_series
from src.routes.wishlist import _image as wishlist_image
from src.sets import set_progress
from src.stats import collection_growth, collection_stats
from src.tags import tag_summaries
from src.templating import templates
from src.trade import trade_binder
from src.wishlist import list_wishlist

router = APIRouter(tags=["collection"])

TABS = [
    ("stats", "Stats"),
    ("locations", "Locations"),
    ("binders", "Binders"),
    ("decks", "Decks"),
    ("tags", "Tags"),
    ("wishlist", "Wishlist"),
    ("checklists", "Checklists"),
    ("trade", "Trade"),
]
_TAB_KEYS = {t for t, _ in TABS}


@router.get("/collection", response_class=HTMLResponse)
async def collection(
    request: Request,
    tab: str = "stats",
    view: str = "overview",
    keep: int = 1,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    tab = tab if tab in _TAB_KEYS else "stats"
    currency = get_currency(request)
    ctx: dict = {
        "tabs": TABS, "tab": tab, "view": view, "keep": keep,
        "cur": info(currency), "read_only": get_settings().read_only,
    }

    if tab == "stats":
        ctx["stats"] = await collection_stats(session, currency)
        ctx["value_chart"] = build_value_chart(await value_series(session))
        ctx["growth"] = await collection_growth(session, currency)
        if view == "sets":
            ctx["sets"] = await set_progress(session)
    elif tab == "decks":
        rows = await session.execute(
            select(Deck, func.count()).outerjoin(Deck.cards)
            .group_by(Deck.id).order_by(Deck.created_at.desc())
        )
        ctx["decks"] = [(d, n) for d, n in rows.all()]
    elif tab == "locations":
        decks = await session.execute(
            select(Deck, func.count()).outerjoin(Deck.cards)
            .group_by(Deck.id).order_by(Deck.name)
        )
        ctx["boxes"] = await box_summaries(session)
        ctx["others"] = await other_locations(session)
        ctx["loc_binders"] = await binder_summaries(session)
        ctx["loc_decks"] = [(d, n) for d, n in decks.all()]
    elif tab == "binders":
        ctx["binders"] = await binder_summaries(session)
    elif tab == "tags":
        ctx["tags"] = await tag_summaries(session)
    elif tab == "wishlist":
        wl = await list_wishlist(session, currency)
        ctx["view_obj"] = wl
        ctx["rows"] = [(item, wishlist_image(item.card)) for item in wl.items]
    elif tab == "checklists":
        rows = await session.execute(
            select(Checklist, func.count()).outerjoin(Checklist.items)
            .group_by(Checklist.id).order_by(Checklist.created_at.desc())
        )
        ctx["checklists"] = [(c, n) for c, n in rows.all()]
    elif tab == "trade":
        ctx["binder"] = await trade_binder(session, currency, keep=keep)

    return templates.TemplateResponse(request, "collection.html", ctx)
