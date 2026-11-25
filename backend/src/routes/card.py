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
from src.currency import get_currency
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
from src.pricing import SOURCES, get_price_source
from src.routes.collection import _collection_partial_generator
from src.scryfall.client import ScryfallClient, ScryfallError
from src.scryfall.images import ImageCache
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


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


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


# Scryfall-style price keys -> the finish label shown on a price chip.
_FINISH_KEYS = (("usd", "normal"), ("usd_foil", "✦ foil"), ("usd_etched", "❖ etched"))
_EUR_KEYS = (("eur", "normal"), ("eur_foil", "✦ foil"), ("eur_etched", "❖ etched"))
# The per-card history chart is built from snapshots of Scryfall's ``usd`` — TCGplayer's market
# price for the non-foil printing — whatever source the visitor prefers (see ``snapshot_prices``).
CHART_SOURCE = "tcgplayer"


def _amount(raw) -> float | None:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


def _chips(prices: dict, keys: tuple, code: str, rate: float | None = None) -> list[dict]:
    """One chip per finish that has a price. ``currency`` is the currency the amount is really in,
    so a price that could not be converted is labelled with what it is rather than the wish."""
    chips = []
    for key, label in keys:
        amount = _amount(prices.get(key))
        if amount is not None:
            chips.append({"label": label, "amount": amount * rate if rate else amount,
                          "currency": code})
    return chips


def _price_rows(request: Request, card: Card) -> list[dict]:
    """The card's current prices, one row per marketplace that has one (#410).

    Every row names its marketplace, so a price is never shown without saying where it came from;
    the visitor's preferred source is flagged and listed first. Amounts are in the display currency
    where a rate is known, else in USD (and labelled as such). TCGplayer is always included when
    Scryfall has a price for it, because it is also what the history chart below records.
    """
    code = get_currency(request)
    preferred = get_price_source(request)
    convert = currency.info(code).get("convert")
    rate = fx.rate(code) if convert else None
    shown = code if rate else "usd"

    by_source = {"tcgplayer": card.prices or {}, **(card.market_prices or {})}
    rows = []
    for source, prices in by_source.items():
        chips = _chips(prices or {}, _FINISH_KEYS, shown, rate)
        if chips:
            meta = SOURCES.get(source, {})
            rows.append({"code": source, "label": meta.get("label", source),
                         "note": meta.get("note", ""), "preferred": source == preferred,
                         "prices": chips})
    if code == "eur":
        # Euro prices are Cardmarket's own (Scryfall ``eur``), not a conversion — and they are what
        # every euro total in the app uses, whichever USD marketplace is preferred.
        chips = _chips(card.prices or {}, _EUR_KEYS, "eur")
        if chips:
            for row in rows:
                row["preferred"] = False
            rows.append({"code": "cardmarket", "label": "Cardmarket",
                         "note": "Trend price (Scryfall)", "preferred": True, "prices": chips})
    rows.sort(key=lambda row: not row["preferred"])
    return rows


def _chart_source_note(request: Request, rows: list[dict]) -> str | None:
    """Why the chart may not end on the price shown above it, when that is the case (#410)."""
    preferred = next((row for row in rows if row["preferred"]), None)
    if preferred is None or preferred["code"] == CHART_SOURCE:
        return None
    return (f"Your prices come from {preferred['label']}, but history is only recorded from "
            f"{SOURCES[CHART_SOURCE]['label']}, so this line can differ from the price above.")


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

    try:
        card_uuid = uuid.UUID(scryfall_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Card not found.") from None

    collection_partial_data = await _collection_partial_generator(request, session, card_uuid)
    card = collection_partial_data['card']

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


    price_rows = _price_rows(request, card)

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
            "legality_rows": legality_rows,
            "wishlisted": await is_wishlisted(session, card.scryfall_id),
            "price_target": await target_for(session, str(card.scryfall_id)),
            "card_usd": float((card.prices or {}).get("usd") or 0.0),
            "show_similar": show_similar,
            "ai_ready": (await get_config(session)).ready,
            "price_rows": price_rows,
            "tix": _amount((card.prices or {}).get("tix")),
            "chart_source": SOURCES[CHART_SOURCE]["label"],
            "chart_source_note": _chart_source_note(request, price_rows),
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
    if(card is None):
        raise HTTPException(status_code=404, detail="Card not found.")
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
    if(card is None):
        raise HTTPException(status_code=404, detail="Card not found.")
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
    if(card is None):
        raise HTTPException(status_code=404, detail="Card not found.")
    rulings = await fetch_rulings(scryfall_id, card)
    return templates.TemplateResponse(
        request, "_card_rulings.html", {"rulings": rulings}
    )
