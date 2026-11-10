"""The consolidated "My Collection" page: Stats / Decks / Binders / Wishlist / Checklists / Trade
as tabs on a single ``/collection`` route, with content swapped server-side by ``?tab=``.

Each tab loads only its own data (reusing the same services the standalone pages used) and renders
a ``_col_<tab>`` partial inside the shared shell. The old per-feature routes redirect here.
"""

from __future__ import annotations
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select, desc, asc
from sqlalchemy.ext.asyncio import AsyncSession

from src.binder_service import all_binders
from src.box_service import all_boxes
from src.checklists import Checklist
from src.config import get_settings
from src.currency import get_currency, info, unit_price
from src.decks import all_decks
from src.db import get_session
from src.locations import get_location_summaries_remapped, find_or_create_grouped_locations
from src.models import Deck, DeckCard, PriceSnapshot, Card, CollectionCard, Location
from src.collection_edit import color_identity_group
from src.perfcache import memoize
from src.prices import CHART_RANGES, DEFAULT_RANGE, build_value_chart, range_days, value_series
from src.pricing import resolve_prices, get_price_source
from src.routes.wishlist import _image as wishlist_image
from src.sell import sell_list
from src.sets import set_progress
from src.stats import collection_growth, collection_stats
from src.tags import tag_summaries
from src.templating import templates
from src.trade import trade_binder
from src.trade_pool import pool_summaries
from src.wishlist import list_wishlist

router = APIRouter(tags=["collection"])

TABS = [
    ("stats", "Stats", "fa-solid fa-chart-column"),
    ("locations", "Locations", "fa-solid fa-compass"),
    ("binders", "Binders", "fa-solid fa-book-open"),
    ("decks", "Decks", "fa-solid fa-layer-group"),
    ("tags", "Tags", "fa-solid fa-tags"),
    ("sleeves", "Sleeves", "fa-solid fa-shield-halved"),
    ("wishlist", "Wishlist", "fa-solid fa-heart"),
    ("checklists", "Checklists", "fa-solid fa-list-check"),
    ("trade", "Trade", "fa-solid fa-handshake"),
    ("sell", "Sell", "fa-solid fa-money-bill"),
]
_TAB_KEYS = {t for t, _, _ in TABS}


@router.get("/collection", response_class=HTMLResponse)
async def collection(
    request: Request,
    tab: str = "stats",
    view: str = "overview",
    sort_by: str = "name",
    sort_dir: str = "asc",
    keep: int = 1,
    chart_range: str = Query(DEFAULT_RANGE, alias="range"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    tab = tab.lower() if tab else "stats"
    currency = get_currency(request)
    source = get_price_source(request)
    
    sort_by = sort_by.lower() if sort_by else "name"
    sort_dir = sort_dir.lower() if sort_dir else "asc"

    ctx: dict = {
        "tabs": TABS, "tab": tab, "view": view, "keep": keep,
        "cur": info(currency), "read_only": get_settings().read_only,
        "sort_by": sort_by, "sort_dir": sort_dir, 'now': datetime.now(timezone.utc)
    }

    if tab == "stats":
        async def _chart():
            return build_value_chart(await value_series(session, range_days(chart_range), currency))

        ctx["stats"] = await memoize(("stats", currency, source),
                                     lambda: collection_stats(session, currency, source))
        ctx["value_chart"] = await memoize(("vchart", chart_range), _chart)
        ctx["chart_range"] = chart_range
        ctx["chart_ranges"] = CHART_RANGES
        ctx["has_price_history"] = bool(
            await session.scalar(select(PriceSnapshot.id).limit(1))
        )
        ctx["growth"] = await memoize(("growth", currency, source),
                                       lambda: collection_growth(session, currency, source=source))
        if view == "sets":
            ctx["sets"] = await memoize("setprog", lambda: set_progress(session))
    elif tab == "decks":
        from src.models.sleeve import Sleeve
        # Load decks with their sleeve information
        rows = await session.execute(
            select(Deck, func.count()).outerjoin(Deck.cards)
            .group_by(Deck.id)
        )
        
        ctx["decks"] = []

        for d, n in rows.all():
            # Calculate total value for the deck
            card_rows = (
                await session.execute(
                    select(DeckCard.quantity, DeckCard.finish, Card.prices, Card.market_prices, Card.color_identity)
                    .join(Card, Card.scryfall_id == DeckCard.scryfall_id)
                    .where(DeckCard.deck_id == d.id)
                )
            ).all()
            
            total_val = 0.0
            deck_color_identity = set()
            for qty, finish, prices, market_prices, color_identity in card_rows:
                total_val += (qty or 0) * unit_price(
                    resolve_prices(prices, market_prices, source), finish, currency)
                if color_identity:
                    deck_color_identity.update(color_identity)
            
            symbols = [f"ms-{c.lower()}" for c in sorted(list(deck_color_identity))]
            group_name = color_identity_group(list(deck_color_identity))
            
            # Get the sleeve associated with this deck (if any) - by filtering sleeves where deck_id matches
            sleeve = None
            if d.id:  # Only try to fetch if deck has an ID
                sleeve_result = await session.execute(
                    select(Sleeve).where(Sleeve.deck_id == d.id)
                )
                sleeve = sleeve_result.scalars().first()
            
            ctx["decks"].append((d, n, round(total_val, 2), symbols, group_name, d.updated_at, sleeve))

        if sort_by == "price":
            ctx["decks"].sort(key=lambda x: x[2], reverse=(sort_dir == "desc"))
        elif sort_by == "name":
            ctx["decks"].sort(key=lambda x: x[0].name, reverse=(sort_dir == "desc"))
        elif sort_by == "updated_at":
            ctx["decks"].sort(key=lambda x: x[0].updated_at, reverse=(sort_dir == "desc"))


    elif tab == "locations":
        decks = await session.execute(
            select(Deck, func.count()).outerjoin(Deck.cards)
            .group_by(Deck.id).order_by(Deck.name)
        )
        ctx["locations"] = await get_location_summaries_remapped(session)
        ctx["binders"] = await all_binders(session)
        ctx["boxes"] = await all_boxes(session)
        
        # Count unfiled cards
        ctx["unfiled"] = (await session.scalars(
            select(CollectionCard)
            .where(CollectionCard.location.is_(None))
        )).all()
        ctx["unfiled_card_count"] = sum([x.quantity for x in ctx["unfiled"]])
        ctx["unfiled_stack_count"] = len(ctx["unfiled"])
        
        # Get all non-deck, non-unfiled locations for the dropdown
        location_rows = await session.execute(
            select(Location.id, Location.type, Location.foreign_id)
            .where(Location.type != "deck")
        )
        ctx["available_locations"] = [
            {"id": row.id, "type": row.type, "foreign_id": row.foreign_id}
            for row in location_rows.all()
        ]
        
        # Apply sorting to boxes
        if sort_by == "updated_at":
            # Boxes don't have updated_at, so we'll sort by name as fallback
            ctx["boxes"].sort(key=lambda x: x.created_at, reverse=(sort_dir == "desc"))
        else:
            ctx["boxes"].sort(key=lambda x: x.name, reverse=(sort_dir == "desc"))
        
        ctx["decks"] = await all_decks(session)
        ctx["deck_sizes"] = {d.id: {board: sum(c.quantity for c in d.cards if c.board == board) for board in ['main','side']} for d in ctx["decks"]}

        ctx["grouped_locations"] = await find_or_create_grouped_locations(session, binders=True, boxes=True, decks=False)
    

    elif tab == "binders":
        ctx["locations"] = await get_location_summaries_remapped(session)
        ctx["binders"] = await all_binders(session)

        # Apply sorting
        if sort_by == "updated_at":
            ctx["binders"].sort(key=lambda x: x.updated_at, reverse=(sort_dir == "desc"))
        else:
            ctx["binders"].sort(key=lambda x: x.name, reverse=(sort_dir == "desc"))

    elif tab == "tags":
        ctx["tags"] = await tag_summaries(session)
    elif tab == "sleeves":
        from src.models.sleeve import Sleeve
        # Load sleeves with their associated deck names
        rows = await session.execute(
            select(Sleeve, Deck.name.label("deck_name"))
            .outerjoin(Deck, Sleeve.deck_id == Deck.id)
        )
        
        # Convert to list of tuples and then create sleeve objects with deck_name attribute
        sleeves_with_names = []
        for sleeve, deck_name in rows.all():
            # Add the deck_name as an attribute to the sleeve object
            sleeve.deck_name = deck_name
            sleeves_with_names.append(sleeve)
        
        # Apply sorting
        if sort_by == "updated_at":
            sleeves_with_names.sort(key=lambda x: x.added_at, reverse=(sort_dir == "desc"))
        else:
            # Default to sorting by name
            sleeves_with_names.sort(key=lambda x: x.name or "", reverse=(sort_dir == "desc"))
        
        ctx["sleeves"] = sleeves_with_names
        # Also load decks for the new sleeve form
        decks = await session.execute(
            select(Deck).order_by(Deck.name)
        )
        ctx["decks"] = decks.scalars().all()
    elif tab == "wishlist":
        wl = await list_wishlist(session, currency, source, sort_by=sort_by, sort_dir=sort_dir)
        ctx["view_obj"] = wl
        ctx["rows"] = [(item, wishlist_image(item.card)) for item in wl.items]
    elif tab == "checklists":
        # Handle sorting for checklists
        if sort_by == "updated_at":
            order_by = Checklist.created_at
        else:
            order_by = Checklist.name
        
        if sort_dir == "desc":
            order_by = order_by.desc()
        else:
            order_by = order_by.asc()
            
        rows = await session.execute(
            select(Checklist, func.count()).outerjoin(Checklist.items)
            .group_by(Checklist.id).order_by(order_by)
        )
        ctx["checklists"] = [(c, n) for c, n in rows.all()]
    elif tab == "trade":
        ctx["binder"] = await trade_binder(session, currency, keep=keep, source=source)
        ctx["pools"] = await pool_summaries(session)
    elif tab == "sell":
        ctx["sell"] = await sell_list(session, currency, source)

    return templates.TemplateResponse(request, "collection.html", ctx)


# New routes for cleaner paths
@router.get("/collection/stats", response_class=HTMLResponse)
async def collection_stats_redirect(request: Request, view: str = "overview", range: str = DEFAULT_RANGE, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await collection(request, tab="stats", view=view, chart_range=range, session=session)

@router.get("/collection/decks", response_class=HTMLResponse)
async def collection_decks_redirect(request: Request, sort_by: str = "name", sort_dir: str = "asc", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await collection(request, tab="decks", sort_by=sort_by, sort_dir=sort_dir, session=session)

@router.get("/collection/binders", response_class=HTMLResponse)
async def collection_binders_redirect(request: Request, sort_by: str = "name", sort_dir: str = "asc", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await collection(request, tab="binders", sort_by=sort_by, sort_dir=sort_dir, session=session)

@router.get("/collection/tags", response_class=HTMLResponse)
async def collection_tags_redirect(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await collection(request, tab="tags", session=session)

@router.get("/collection/sleeves", response_class=HTMLResponse)
async def collection_sleeves_redirect(request: Request, sort_by: str = "name", sort_dir: str = "asc", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await collection(request, tab="sleeves", sort_by=sort_by, sort_dir=sort_dir, session=session)

@router.get("/collection/wishlist", response_class=HTMLResponse)
async def collection_wishlist_redirect(request: Request, sort_by: str = "name", sort_dir: str = "asc", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await collection(request, tab="wishlist", sort_by=sort_by, sort_dir=sort_dir, session=session)

@router.get("/collection/checklists", response_class=HTMLResponse)
async def collection_checklists_redirect(request: Request, sort_by: str = "created_at", sort_dir: str = "desc", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await collection(request, tab="checklists", sort_by=sort_by, sort_dir=sort_dir, session=session)

@router.get("/collection/trade", response_class=HTMLResponse)
async def collection_trade_redirect(request: Request, keep: int = 1, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await collection(request, tab="trade", keep=keep, session=session)

@router.get("/collection/sell", response_class=HTMLResponse)
async def collection_sell_redirect(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await collection(request, tab="sell", session=session)

@router.get("/collection/locations", response_class=HTMLResponse)
async def collection_locations_redirect(request: Request, sort_by: str = "name", sort_dir: str = "asc", session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    return await collection(request, tab="locations", sort_by=sort_by, sort_dir=sort_dir, session=session)
