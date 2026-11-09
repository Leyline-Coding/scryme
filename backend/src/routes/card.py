"""Card detail page.

A dedicated page for a single printing: large art, full oracle text, prices, format
legalities, the stacks you own, and the card's other printings. Rulings are loaded lazily
from Scryfall (HTMX) so the page renders instantly and stays useful offline if the fetch fails.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src import currency, fx
from src.card import _image
from src.config import get_settings
from src.currency import CURRENCIES, get_currency
from src.db import get_session
from src.embeddings import similar_to_oracle
from src.llm import get_config
from src.models import Card, CardEmbedding, CardPricePoint
from src.price_watch import target_for
from src.prices import (
    CHART_RANGES,
    DEFAULT_RANGE,
    build_value_chart,
    card_series_in_currency,
    earliest_snapshot_date,
    range_days,
)
from src.pricing import SOURCES, effective_prices, get_price_source
from src.routes.collection import _collection_partial_generator
from src.scryfall.client import ScryfallClient, ScryfallError
from src.scryfall.images import ImageCache
from src.tags import add_card_tag, remove_card_tag
from src.templating import templates
from src.wishlist import is_wishlisted

router = APIRouter(tags=["card"])
_cache = ImageCache()

# Formats worth showing, in display order (Scryfall reports ~20; this is the useful subset).
LEGALITY_FORMATS = [
    "standard", "pioneer", "modern", "legacy", "vintage",
    "commander", "pauper", "brawl", "historic", "oathbreaker",
]

# Lazily-fetched rulings cached per printing for the process lifetime (single-user, polite).
_rulings_cache: dict[str, list[dict]] = {}


def _flip_rotate(card: Card) -> tuple[bool, str | None, bool]:
    """(can_flip, flip_image, can_rotate) for a card's page controls, from its faces/layout."""
    raw_faces = card.raw.get("card_faces") or []
    face_images = [
        (f.get("image_uris") or {}).get("normal") or (f.get("image_uris") or {}).get("png")
        for f in raw_faces
    ]
    can_flip = sum(1 for u in face_images if u) >= 2
    flip_image = face_images[1] if can_flip else None
    keywords = [k.lower() for k in (card.keywords or [])]
    # Sideways-read cards get a rotate button. Battles are stored with layout "transform" (so they
    # also get the flip button — front battle ⇄ back permanent), so detect them by type line;
    # planes use the "planar" layout; aftermath is a keyword.
    can_rotate = (
        "battle" in (card.type_line or "").lower()
        or card.layout == "planar"
        or "aftermath" in keywords
    )
    return can_flip, flip_image, can_rotate


def _hist_currency(request: Request) -> str:
    """Display currency for the per-card price-history chart (#233).

    Its own ``scryme_hist_currency`` preference (set by the chart dropdown) takes precedence;
    otherwise it follows the site-wide currency, so a EUR user sees the chart in EUR by default.
    """
    prefs = getattr(getattr(request, "state", None), "prefs", None)
    if prefs is not None:
        return prefs.hist_currency or get_currency(request)
    return currency.normalize(request.cookies.get("scryme_hist_currency")) or get_currency(request)


def _hist_currencies() -> list[dict]:
    """Chart-currency dropdown options: USD plus each convertible currency, in menu order."""
    return [
        {"id": c["code"], "label": c["label"], "symbol": c["symbol"]}
        for c in currency.CURRENCIES.values()
    ]


def _price_rows(request: Request, card: Card) -> dict[str, dict[str, dict[str, float]]]:
    """Non-empty (label, price) rows, led by the visitor's chosen currency and price source."""
    # Use market_prices directly instead of effective_prices
    market_prices = getattr(card, "market_prices", None) or {}
    currency = get_currency(request) ## User's chosen currency
    currency_symbol = CURRENCIES[currency]['symbol']
    default_currency_symbol = CURRENCIES['usd']['symbol']

    # Convert each source's prices to user's currency and relabel
    for source, prices in market_prices.items():
        for key in list(prices.keys()):
            price_value = float(market_prices[source][key])
            del market_prices[source][key]
            new_key = '❖ etched' if 'etched' in key else '✦ foil' if 'foil' in key else 'normal'
            ## Check collision on new key
            if(new_key in prices.keys()): new_key+='_'+key
            try:
                # Convert to user's currency if not already in USD/EUR (which are native)
                if currency != "usd":
                    fx_rate = fx.rate(currency)
                    if fx_rate:
                        market_prices[source][new_key] = {'amount': price_value * fx_rate, 'currency_symbol': currency_symbol}
                    else:
                        market_prices[source][new_key] = {'amount': float(price_value), 'currency_symbol': default_currency_symbol}
                else:
                    market_prices[source][new_key] = {'amount': float(price_value), 'currency_symbol': default_currency_symbol}
                    
            except (ValueError, TypeError):
                market_prices[source][new_key] = {'amount': float(price_value), 'currency_symbol': default_currency_symbol}
            

    # Get TIX prices from the card's raw prices (not market_prices) since TIX is a native Scryfall field
    market_prices['TIX'] = {'': {'amount': float((getattr(card, "prices", None) or {}).get("tix", 0.0)), 'currency_symbol': ''}}
    return market_prices


async def _price_history_ctx(request: Request, session: AsyncSession, card: Card,
                             chart_range: str) -> dict:
    """Per-card price-history chart context (#233): the series in the chosen currency + range meta.

    Extracted from ``card_detail`` to keep that view under the cognitive-complexity limit.
    """
    has_card_history = (await session.scalar(
        select(CardPricePoint.id).where(CardPricePoint.scryfall_id == card.scryfall_id).limit(1)
    )) is not None
    # The series is stored in USD and converted into the visitor's chosen chart currency with
    # date-matched historical FX rates; `hist_approx` flags a current-rate fallback.
    hist_code = _hist_currency(request)
    series, hist_approx = await card_series_in_currency(
        session, card.scryfall_id, range_days(chart_range), hist_code
    )
    return {
        "has_card_history": has_card_history,
        "price_chart": build_value_chart(series),
        "chart_range": chart_range,
        "chart_ranges": CHART_RANGES,
        "hist_currencies": _hist_currencies(),
        "hist_currency": hist_code,
        "hist_symbol": currency.info(hist_code)["symbol"],
        "hist_decimals": 0 if hist_code == "jpy" else 2,
        "hist_approx": hist_approx,
    }


@router.get("/card/{scryfall_id}", response_class=HTMLResponse)
async def card_detail(
    request: Request,
    scryfall_id: str,
    chart_range: str = Query(DEFAULT_RANGE, alias="range"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    
    collection_partial_data = await _collection_partial_generator(request, session, uuid.UUID(scryfall_id))
    card = collection_partial_data['card']
    
    # Double-faced cards (transform / modal DFC / battles / reversible) carry a separate image per
    # face — offer a Scryfall-style flip button. Battles, Planes/Phenomena, and Aftermath cards read
    # sideways, so offer a rotate button. Both get playful animations on the card page.
    can_flip, flip_image, can_rotate = _flip_rotate(card)
    # Battles read by turning the card clockwise; planes/aftermath turn counter-clockwise (#11).
    rotate_cw = "battle" in (card.type_line or "").lower()
    legalities = card.legalities or {}
    legality_rows = [(fmt, legalities.get(fmt, "not_legal")) for fmt in LEGALITY_FORMATS]

    # Foil/etched shimmer is a property of the printing (a foil-only or etched *treatment*), shown
    # regardless of ownership (#9). Etched printings shimmer; foil-only printings (offered in foil
    # but not nonfoil) shimmer; ordinary cards that merely also come in foil do not.
    finishes = [f.lower() for f in (card.raw.get("finishes") or [])]
    anim_etched = "etched" in finishes
    anim_foil = "foil" in finishes and "nonfoil" not in finishes

    # Show a "Similar cards" section only when embeddings exist for this card (#176). This is a
    # local vector query, so it doesn't require the AI endpoint to be reachable/enabled.
    show_similar = bool(
        card.oracle_id is not None
        and await session.get(CardEmbedding, card.oracle_id) is not None
    )


    return templates.TemplateResponse(
        request,
        "card_detail.html",
        {
            "faces": card.raw.get("card_faces") or [],
            "image": _image(card),
            "scryfall_uri": card.raw.get("scryfall_uri", "#"),
            "artist": card.raw.get("artist"),
            "anim_foil": anim_foil,
            "anim_etched": anim_etched,
            "can_flip": can_flip,
            "flip_image": flip_image,
            "can_rotate": can_rotate,
            "rotate_cw": rotate_cw,
            "legality_rows": legality_rows,
            "wishlisted": await is_wishlisted(session, card.scryfall_id),
            "price_target": await target_for(session, str(card.scryfall_id)),
            "card_usd": float((card.prices or {}).get("usd") or 0.0),
            "show_similar": show_similar,
            "ai_ready": (await get_config(session)).ready,
            "price_rows": _price_rows(request, card),
            **(await _price_history_ctx(request, session, card, chart_range)),
            **collection_partial_data
        },
    )


@router.post("/card/fx-history")
async def fx_history(
    code: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Ensure historical FX rates for ``code`` are downloaded so the chart can render in it (#233).

    Called by the chart's currency dropdown before it reloads, so the download (and its spinner)
    happen up front and the reloaded page renders off cached data. Idempotent and best-effort:
    ``ok=false`` with ``approximate=true`` means no history is available and the chart will use the
    current rate. Not gated by read-only — FX history is shared reference data, not the collection.
    """
    code = currency.normalize(code) or "usd"
    if code == "usd" or code not in fx.HIST_CODES:
        return JSONResponse({"ok": True, "approximate": False})
    start = await earliest_snapshot_date(session)
    if start is None:
        return JSONResponse({"ok": True, "approximate": False})  # no snapshots to convert
    have = await fx.ensure_fx_history(session, code, start)
    return JSONResponse({"ok": have, "approximate": not have})


@router.get("/card/{scryfall_id}/image")
async def card_image(
    scryfall_id: str, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    """Redirect to the card's image (cached copy if present, else Scryfall CDN).

    Used by the hover-preview on card-name links (#143 follow-on). A redirect keeps it cheap and
    lets the browser cache the image.
    """
    card = await session.get(Card, scryfall_id)
    if(card is None): raise HTTPException(status_code=404, detail="Card not found.")
    target = _image(card, "normal")
    if not target:
        raise HTTPException(status_code=404, detail="No image.")
    return RedirectResponse(target, status_code=307)


@router.get("/card/{scryfall_id}/similar", response_class=HTMLResponse)
async def card_similar(
    request: Request,
    scryfall_id: str,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Lazily-loaded 'Similar cards' grid: nearest owned cards by oracle-text embedding (#176)."""
    card = await session.get(Card, scryfall_id)
    if(card is None): raise HTTPException(status_code=404, detail="Card not found.")
    items: list[tuple[Card, str]] = []
    if card.oracle_id is not None:
        scored = await similar_to_oracle(session, card.oracle_id, limit=8, scope="owned")
        oids = [oid for oid, _ in scored]
        if oids:
            rows = (
                await session.execute(
                    select(Card).where(Card.oracle_id.in_(oids)).distinct(Card.oracle_id)
                    .order_by(Card.oracle_id, Card.released_at.desc().nulls_last())
                )
            ).scalars().all()
            by_oracle = {c.oracle_id: c for c in rows}
            items = [(by_oracle[oid], _image(by_oracle[oid], "small"))
                     for oid, _ in scored if oid in by_oracle]
    return templates.TemplateResponse(request, "_similar_cards.html", {"items": items})


def _tags_response(request: Request, card_id: uuid.UUID, tags: list[str]) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_card_tags.html",
        {"card_id": card_id, "tags": tags, "read_only": get_settings().read_only},
    )


@router.post("/card/{scryfall_id}/tags", response_class=HTMLResponse)
async def add_tag(
    request: Request,
    scryfall_id: str,
    tag: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This demo is read-only.")
    card = await session.get(Card, scryfall_id)
    if(card is None): raise HTTPException(status_code=404, detail="Card not found.")
    tags = await add_card_tag(session, card.scryfall_id, tag)
    return _tags_response(request, card.scryfall_id, tags)


@router.post("/card/{scryfall_id}/tags/delete", response_class=HTMLResponse)
async def delete_tag(
    request: Request,
    scryfall_id: str,
    tag: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This demo is read-only.")
    card = await session.get(Card, scryfall_id)
    if(card is None): raise HTTPException(status_code=404, detail="Card not found.")
    tags = await remove_card_tag(session, card.scryfall_id, tag)
    return _tags_response(request, card.scryfall_id, tags)


async def fetch_rulings(scryfall_id: str, card: Card) -> list[dict] | None:
    """Rulings for a card from Scryfall, cached per printing (None if unavailable/offline)."""
    rulings: list[dict] | None = _rulings_cache.get(scryfall_id)
    if rulings is None:
        uri = card.raw.get("rulings_uri")
        try:
            if not uri:
                raise ScryfallError("no rulings_uri")
            async with ScryfallClient() as client:
                payload = await client.get_json(uri)
            rulings = payload.get("data", [])
            _rulings_cache[scryfall_id] = rulings
        except ScryfallError:
            rulings = None  # leave uncached so a later view can retry
    return rulings


@router.get("/card/{scryfall_id}/rulings", response_class=HTMLResponse)
async def card_rulings(
    request: Request,
    scryfall_id: str,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    card = await session.get(Card, scryfall_id)
    if(card is None): raise HTTPException(status_code=404, detail="Card not found.")
    rulings = await fetch_rulings(scryfall_id, card)
    return templates.TemplateResponse(
        request, "_card_rulings.html", {"rulings": rulings}
    )
