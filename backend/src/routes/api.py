"""JSON / REST API (``/api/v1``).

A typed, versioned surface over the same services the HTML UI uses — so a mobile app, scripts, or a
thinner desktop shell can drive scryme. FastAPI generates the OpenAPI schema (browse it at ``/docs``
or ``/openapi.json``). Mutations honor ``SCRYME_READ_ONLY``; when ``SCRYME_API_TOKEN`` is set every
request must present it (``Authorization: Bearer <token>`` or ``X-API-Key``).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collection_edit import add_or_increment
from src.config import get_settings
from src.currency import normalize as normalize_currency
from src.db import get_session
from src.decks import deck_coverage
from src.models import Card, CollectionCard, Deck
from src.scryfall.mapping import image_url
from src.search import SearchError, SearchScope
from src.search.engine import DEFAULT_SORT, SORT_KEYS, run_search
from src.stats import collection_stats
from src.tags import add_card_tag, card_tags, remove_card_tag
from src.wishlist import add_to_wishlist, list_wishlist, remove_from_wishlist


def require_api_token(request: Request) -> None:
    token = get_settings().api_token
    if not token:
        return
    auth = request.headers.get("Authorization", "")
    provided = auth[7:] if auth.startswith("Bearer ") else request.headers.get("X-API-Key", "")
    if provided != token:
        raise HTTPException(status_code=401, detail="Invalid or missing API token.")


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


router = APIRouter(prefix="/api/v1", tags=["api"], dependencies=[Depends(require_api_token)])


# --- schemas ------------------------------------------------------------------------------------

class CardOut(BaseModel):
    scryfall_id: str
    oracle_id: str | None = None
    name: str
    set_code: str
    set_name: str | None = None
    collector_number: str
    rarity: str | None = None
    mana_cost: str | None = None
    cmc: float | None = None
    type_line: str | None = None
    colors: list[str] | None = None
    prices: dict | None = None
    image: str | None = None
    quantity: int = 0
    tags: list[str] = []


class StackOut(BaseModel):
    id: int
    scryfall_id: str
    quantity: int
    finish: str
    condition: str | None = None
    language: str
    binder_name: str | None = None
    tags: list[str] | None = None


class CardDetailOut(CardOut):
    oracle_text: str | None = None
    legalities: dict | None = None
    owned: list[StackOut] = []


class SearchOut(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    cards: list[CardOut]


class DeckSummaryOut(BaseModel):
    id: int
    name: str
    cards: int


class DeckCardOut(BaseModel):
    name: str
    quantity: int
    board: str
    owned: int
    matched: bool
    scryfall_id: str | None = None


class DeckDetailOut(BaseModel):
    id: int
    name: str
    pct_complete: int
    total_needed: int
    owned_count: int
    missing_count: int
    missing_cost: float
    main: list[DeckCardOut]
    side: list[DeckCardOut]


class WishlistItemOut(BaseModel):
    scryfall_id: str
    name: str
    set_code: str
    quantity: int
    note: str | None = None
    price: float | None = None


class WishlistOut(BaseModel):
    total_cards: int
    total_cost: float
    items: list[WishlistItemOut]


class BarOut(BaseModel):
    label: str
    count: int


class ValuedOut(BaseModel):
    scryfall_id: str
    name: str
    set_code: str
    usd: float


class StatsOut(BaseModel):
    total_cards: int
    printings: int
    distinct_cards: int
    total_value: float
    by_color: list[BarOut]
    by_rarity: list[BarOut]
    by_type: list[BarOut]
    by_set: list[BarOut]
    mana_curve: list[BarOut]
    most_valuable: list[ValuedOut]


class OkOut(BaseModel):
    ok: bool = True
    tags: list[str] | None = None
    quantity: int | None = None


# --- helpers ------------------------------------------------------------------------------------

def _card_out(card: Card, quantity: int = 0, tags: list[str] | None = None) -> CardOut:
    return CardOut(
        scryfall_id=str(card.scryfall_id),
        oracle_id=str(card.oracle_id) if card.oracle_id else None,
        name=card.name, set_code=card.set_code, set_name=card.set_name,
        collector_number=card.collector_number, rarity=card.rarity, mana_cost=card.mana_cost,
        cmc=card.cmc, type_line=card.type_line, colors=card.colors, prices=card.prices,
        image=image_url(card.raw), quantity=quantity, tags=tags or [],
    )


def _bars(group) -> list[BarOut]:
    return [BarOut(label=b.label, count=b.count) for b in group]


# --- read ---------------------------------------------------------------------------------------

@router.get("/search", response_model=SearchOut)
async def api_search(
    q: str = "",
    scope: str = "collection",
    page: int = 1,
    sort: str = DEFAULT_SORT,
    dir: str = "asc",
    session: AsyncSession = Depends(get_session),
) -> SearchOut:
    scope_enum = SearchScope.ALL if scope == "all" else SearchScope.COLLECTION
    sort = sort if sort in SORT_KEYS else DEFAULT_SORT
    try:
        result = await run_search(session, q, scope=scope_enum, page=page, sort=sort,
                                  descending=(dir == "desc"))
    except SearchError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cards = [
        _card_out(c, result.quantities.get(str(c.scryfall_id), 0),
                  result.tags.get(str(c.scryfall_id), []))
        for c in result.cards
    ]
    return SearchOut(total=result.total, page=result.page, page_size=result.page_size,
                     total_pages=result.total_pages, cards=cards)


async def _get_card(session: AsyncSession, scryfall_id: str) -> Card:
    try:
        sid = uuid.UUID(scryfall_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail="Card not found.") from exc
    card = await session.get(Card, sid)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    return card


@router.get("/cards/{scryfall_id}", response_model=CardDetailOut)
async def api_card(
    scryfall_id: str, session: AsyncSession = Depends(get_session)
) -> CardDetailOut:
    card = await _get_card(session, scryfall_id)
    owned = list(
        (await session.execute(
            select(CollectionCard).where(CollectionCard.scryfall_id == card.scryfall_id)
        )).scalars().all()
    )
    base = _card_out(card, sum(s.quantity for s in owned),
                     await card_tags(session, card.scryfall_id))
    return CardDetailOut(
        **base.model_dump(),
        oracle_text=card.oracle_text, legalities=card.legalities,
        owned=[StackOut(id=s.id, scryfall_id=str(s.scryfall_id), quantity=s.quantity,
                        finish=s.finish, condition=s.condition, language=s.language,
                        binder_name=s.binder_name, tags=s.tags) for s in owned],
    )


@router.get("/decks", response_model=list[DeckSummaryOut])
async def api_decks(session: AsyncSession = Depends(get_session)) -> list[DeckSummaryOut]:
    rows = await session.execute(
        select(Deck, func.count()).outerjoin(Deck.cards)
        .group_by(Deck.id).order_by(Deck.created_at.desc())
    )
    return [DeckSummaryOut(id=d.id, name=d.name, cards=n) for d, n in rows.all()]


@router.get("/decks/{deck_id}", response_model=DeckDetailOut)
async def api_deck(deck_id: int, session: AsyncSession = Depends(get_session)) -> DeckDetailOut:
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found.")
    cov = await deck_coverage(session, deck)

    def row(r) -> DeckCardOut:
        return DeckCardOut(name=r.name, quantity=r.quantity, board=r.board, owned=r.owned,
                           matched=r.matched, scryfall_id=r.scryfall_id)

    return DeckDetailOut(
        id=deck.id, name=deck.name, pct_complete=cov.pct_complete,
        total_needed=cov.total_needed, owned_count=cov.owned_count,
        missing_count=cov.missing_count, missing_cost=round(cov.missing_cost, 2),
        main=[row(r) for r in cov.main], side=[row(r) for r in cov.side],
    )


@router.get("/wishlist", response_model=WishlistOut)
async def api_wishlist(
    currency: str = "usd", session: AsyncSession = Depends(get_session)
) -> WishlistOut:
    cur = normalize_currency(currency) or "usd"
    view = await list_wishlist(session, cur)
    items = []
    for item in view.items:
        prices = item.card.prices or {}
        raw = prices.get(cur) if cur == "eur" else prices.get("usd")
        items.append(WishlistItemOut(
            scryfall_id=str(item.scryfall_id), name=item.card.name,
            set_code=item.card.set_code, quantity=item.quantity, note=item.note,
            price=float(raw) if raw else None,
        ))
    return WishlistOut(total_cards=view.total_cards, total_cost=view.total_cost, items=items)


@router.get("/stats", response_model=StatsOut)
async def api_stats(
    currency: str = "usd", session: AsyncSession = Depends(get_session)
) -> StatsOut:
    s = await collection_stats(session, normalize_currency(currency) or "usd")
    return StatsOut(
        total_cards=s.total_cards, printings=s.printings, distinct_cards=s.distinct_cards,
        total_value=round(s.total_value, 2),
        by_color=_bars(s.by_color), by_rarity=_bars(s.by_rarity), by_type=_bars(s.by_type),
        by_set=_bars(s.by_set), mana_curve=_bars(s.mana_curve),
        most_valuable=[ValuedOut(scryfall_id=v.scryfall_id, name=v.name, set_code=v.set_code,
                                 usd=round(v.usd, 2)) for v in s.most_valuable],
    )


# --- mutations ----------------------------------------------------------------------------------

class CollectionAddIn(BaseModel):
    scryfall_id: str
    quantity: int = 1
    finish: str = "normal"
    condition: str | None = None
    language: str = "en"
    binder: str | None = None


@router.post("/collection", response_model=OkOut)
async def api_collection_add(
    body: CollectionAddIn, session: AsyncSession = Depends(get_session)
) -> OkOut:
    _guard_writable()
    stack = await add_or_increment(
        session, body.scryfall_id, body.quantity, finish=body.finish,
        condition=body.condition, language=body.language, binder=body.binder,
    )
    if stack is None:
        raise HTTPException(status_code=404, detail="Unknown card.")
    return OkOut(quantity=stack.quantity)


class TagIn(BaseModel):
    tag: str


@router.post("/cards/{scryfall_id}/tags", response_model=OkOut)
async def api_add_tag(
    scryfall_id: str, body: TagIn, session: AsyncSession = Depends(get_session)
) -> OkOut:
    _guard_writable()
    card = await _get_card(session, scryfall_id)
    return OkOut(tags=await add_card_tag(session, card.scryfall_id, body.tag))


@router.delete("/cards/{scryfall_id}/tags", response_model=OkOut)
async def api_remove_tag(
    scryfall_id: str, tag: str = Query(...), session: AsyncSession = Depends(get_session)
) -> OkOut:
    _guard_writable()
    card = await _get_card(session, scryfall_id)
    return OkOut(tags=await remove_card_tag(session, card.scryfall_id, tag))


class WishlistAddIn(BaseModel):
    scryfall_id: str
    quantity: int = 1
    note: str | None = None


@router.post("/wishlist", response_model=OkOut)
async def api_wishlist_add(
    body: WishlistAddIn, session: AsyncSession = Depends(get_session)
) -> OkOut:
    _guard_writable()
    item = await add_to_wishlist(session, body.scryfall_id, body.quantity, body.note)
    if item is None:
        raise HTTPException(status_code=404, detail="Unknown card.")
    return OkOut(quantity=item.quantity)


@router.delete("/wishlist/{scryfall_id}", response_model=OkOut)
async def api_wishlist_remove(
    scryfall_id: str, session: AsyncSession = Depends(get_session)
) -> OkOut:
    _guard_writable()
    await remove_from_wishlist(session, scryfall_id)
    return OkOut()
