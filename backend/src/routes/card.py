"""Card detail page.

A dedicated page for a single printing: large art, full oracle text, prices, format
legalities, the stacks you own, and the card's other printings. Rulings are loaded lazily
from Scryfall (HTMX) so the page renders instantly and stays useful offline if the fetch fails.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.binder_service import all_binders, binders_for_card
from src.config import get_settings
from src.currency import get_currency
from src.db import get_session
from src.embeddings import similar_to_oracle
from src.llm import get_config
from src.models import Card, CardEmbedding, CollectionCard
from src.price_watch import target_for
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


@router.get("/card/{scryfall_id}", response_class=HTMLResponse)
async def card_detail(
    request: Request,
    scryfall_id: str,
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

    prices = card.prices or {}
    _usd_rows = [("USD", "usd"), ("USD foil", "usd_foil")]
    _eur_rows = [("EUR", "eur"), ("EUR foil", "eur_foil")]
    # Lead with the visitor's chosen display currency.
    ordered = (_eur_rows + _usd_rows if get_currency(request) == "eur" else _usd_rows + _eur_rows)
    price_rows = [
        (label, prices.get(key)) for label, key in [*ordered, ("TIX", "tix")] if prices.get(key)
    ]
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
            "printings": [(p, _image(p, "small")) for p in printings],
            "price_rows": price_rows,
            "legality_rows": legality_rows,
            "tags": await card_tags(session, card.scryfall_id),
            "wishlisted": await is_wishlisted(session, card.scryfall_id),
            "price_target": await target_for(session, str(card.scryfall_id)),
            "card_usd": float((card.prices or {}).get("usd") or 0.0),
            "read_only": get_settings().read_only,
            "show_similar": show_similar,
            "ai_ready": (await get_config(session)).ready,
            "binders": await all_binders(session),
            "in_ids": await binders_for_card(session, str(card.scryfall_id)),
        },
    )


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
