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
from src.binder_service import all_binders, binders_for_card
from src.box_service import all_boxes
from src.config import get_settings
from src.currency import get_currency
from src.db import get_session
from src.embeddings import similar_to_oracle
from src.llm import get_config
from src.models import Card, CardEmbedding, CardPricePoint, CollectionCard, Deck
from src.price_watch import target_for
from src.prices import (
    CHART_RANGES,
    DEFAULT_RANGE,
    build_value_chart,
    card_value_series,
    convert_card_series,
    earliest_snapshot_date,
    range_days,
)
from src.pricing import SOURCES, effective_prices, get_price_source
from src.routes.collection import printing_options
from src.scryfall.client import ScryfallClient, ScryfallError
from src.scryfall.images import ImageCache
from src.scryfall.mapping import image_url as cdn_image_url
from src.tags import add_card_tag, card_tags, remove_card_tag
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


def _image(card: Card, size: str = "normal") -> str:
    sid = str(card.scryfall_id)
    if size == "normal" and _cache.is_cached(sid):
        return _cache.url_path(sid)
    return cdn_image_url(card.raw, size) or cdn_image_url(card.raw) or ""


async def _load_card(session: AsyncSession, scryfall_id: str) -> Card:
    try:
        sid = uuid.UUID(scryfall_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Card not found.") from exc
    card = await session.get(Card, sid)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    return card


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
    can_rotate = card.layout in ("battle", "planar") or "aftermath" in keywords
    return can_flip, flip_image, can_rotate


def _hist_currency(request: Request) -> str:
    """Display currency for the per-card price-history chart (#233).

    Its own ``scryme_hist_currency`` cookie (set by the chart dropdown) takes precedence; otherwise
    it follows the site-wide currency, so a EUR user sees the chart in EUR by default.
    """
    return currency.normalize(request.cookies.get("scryme_hist_currency")) or get_currency(request)


def _hist_currencies() -> list[dict]:
    """Chart-currency dropdown options: USD plus each convertible currency, in menu order."""
    return [
        {"id": c["code"], "label": c["label"], "symbol": c["symbol"]}
        for c in currency.CURRENCIES.values()
    ]


def _price_rows(request: Request, card: Card) -> list[tuple[str, float]]:
    """Non-empty (label, price) rows, led by the visitor's chosen currency and price source."""
    source = get_price_source(request)
    prices = effective_prices(card, source) or {}
    src = SOURCES.get(source, SOURCES["tcgplayer"])
    usd_label = f"{src['label']}" if source != "tcgplayer" else "USD"
    usd_rows = [(usd_label, "usd"), (f"{usd_label} foil", "usd_foil")]
    eur_rows = [("EUR", "eur"), ("EUR foil", "eur_foil")]
    ordered = eur_rows + usd_rows if get_currency(request) == "eur" else usd_rows + eur_rows
    return [
        (label, prices.get(key)) for label, key in [*ordered, ("TIX", "tix")] if prices.get(key)
    ]


@router.get("/card/{scryfall_id}", response_class=HTMLResponse)
async def card_detail(
    request: Request,
    scryfall_id: str,
    chart_range: str = Query(DEFAULT_RANGE, alias="range"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    card = await _load_card(session, scryfall_id)

    owned = list(
        (
            await session.execute(
                select(CollectionCard)
                .where(CollectionCard.scryfall_id == card.scryfall_id)
                .order_by(CollectionCard.finish, CollectionCard.language)
            )
        )
        .scalars()
        .all()
    )

    printings: list[Card] = []
    if card.oracle_id is not None:
        printings = list(
            (
                await session.execute(
                    select(Card)
                    .where(Card.oracle_id == card.oracle_id)
                    .where(Card.scryfall_id != card.scryfall_id)
                    .order_by(Card.released_at.desc().nulls_last())
                    .limit(24)
                )
            )
            .scalars()
            .all()
        )

    # Double-faced cards (transform / modal DFC / battles / reversible) carry a separate image per
    # face — offer a Scryfall-style flip button. Battles, Planes/Phenomena, and Aftermath cards read
    # sideways, so offer a rotate button. Both get playful animations on the card page.
    can_flip, flip_image, can_rotate = _flip_rotate(card)
    price_rows = _price_rows(request, card)

    # Per-card price history (#233): a USD chart from recorded points, shown for owned + tracked
    # cards. `has_card_history` guards the onboarding empty-state for cards with no points at all.
    has_card_history = (
        await session.scalar(
            select(CardPricePoint.id).where(
                CardPricePoint.scryfall_id == card.scryfall_id
            ).limit(1)
        )
    ) is not None
    usd_series = await card_value_series(session, card.scryfall_id, range_days(chart_range))
    # Convert the USD series into the visitor's chosen chart currency using date-matched historical
    # FX rates (#233), downloading them on first use. `hist_approx` flags a current-rate fallback
    # (offline / download failed) so the chart can say so rather than imply exact history.
    hist_code = _hist_currency(request)
    hist_symbol = currency.info(hist_code)["symbol"]
    hist_decimals = 0 if hist_code == "jpy" else 2
    hist_approx = False
    if hist_code != "usd" and usd_series:
        start = await earliest_snapshot_date(session)
        have = bool(start) and await fx.ensure_fx_history(session, hist_code, start)
        hist_points = await fx.fx_history_points(session, hist_code) if have else []
        hist_approx = not hist_points
        usd_series = convert_card_series(
            usd_series, hist_code, hist_points, fx.rate(hist_code) or 1.0
        )
    price_chart = build_value_chart(usd_series)
    legalities = card.legalities or {}
    legality_rows = [(fmt, legalities.get(fmt, "not_legal")) for fmt in LEGALITY_FORMATS]

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
            "card": card,
            "faces": card.raw.get("card_faces") or [],
            "image": _image(card),
            "scryfall_uri": card.raw.get("scryfall_uri", "#"),
            "artist": card.raw.get("artist"),
            "owned": owned,
            "owned_total": sum(s.quantity for s in owned),
            "owned_foil": any((s.finish or "").lower() == "foil" for s in owned),
            "owned_etched": any((s.finish or "").lower() == "etched" for s in owned),
            "can_flip": can_flip,
            "flip_image": flip_image,
            "can_rotate": can_rotate,
            "printings": [(p, _image(p, "small")) for p in printings],
            "price_rows": price_rows,
            "legality_rows": legality_rows,
            "has_card_history": has_card_history,
            "price_chart": price_chart,
            "chart_range": chart_range,
            "chart_ranges": CHART_RANGES,
            "hist_currencies": _hist_currencies(),
            "hist_currency": hist_code,
            "hist_symbol": hist_symbol,
            "hist_decimals": hist_decimals,
            "hist_approx": hist_approx,
            "tags": await card_tags(session, card.scryfall_id),
            "wishlisted": await is_wishlisted(session, card.scryfall_id),
            "price_target": await target_for(session, str(card.scryfall_id)),
            "card_usd": float((card.prices or {}).get("usd") or 0.0),
            "read_only": get_settings().read_only,
            "show_similar": show_similar,
            "ai_ready": (await get_config(session)).ready,
            "binders": await all_binders(session),
            "in_ids": await binders_for_card(session, str(card.scryfall_id)),
            "boxes": await all_boxes(session),
            "picker_binders": await all_binders(session),
            "decks": [
                (d.id, d.name) for d in
                (await session.execute(select(Deck).order_by(Deck.name))).scalars().all()
            ],
            "printing_opts": await printing_options(session, card.scryfall_id),
        },
    )


@router.post("/card/fx-history")
async def fx_history(
    code: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> JSONResponse:
    """Ensure historical FX rates for ``code`` are downloaded so the chart can render in it (#233).

    Called by the chart's currency dropdown before it reloads, so the download (and its spinner)
    happen up front rather than blocking the reloaded page. Idempotent and best-effort: ``ok=false``
    with ``approximate=true`` means no history is available and the chart will use the current rate.
    Not gated by read-only — FX history is shared reference data, not the user's collection.
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
    card = await _load_card(session, scryfall_id)
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
    card = await _load_card(session, scryfall_id)
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
    card = await _load_card(session, scryfall_id)
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
    card = await _load_card(session, scryfall_id)
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
    card = await _load_card(session, scryfall_id)
    rulings = await fetch_rulings(scryfall_id, card)
    return templates.TemplateResponse(
        request, "_card_rulings.html", {"rulings": rulings}
    )
