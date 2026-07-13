"""JSON / REST API (``/api/v1``).

A typed, versioned surface over the same services the HTML UI uses — so a mobile app, scripts, or a
thinner desktop shell can drive scryme. FastAPI generates the OpenAPI schema (browse it at ``/docs``
or ``/openapi.json``). Mutations honor ``SCRYME_READ_ONLY``; when ``SCRYME_API_TOKEN`` is set every
request must present it (``Authorization: Bearer <token>`` or ``X-API-Key``).
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collection_edit import add_or_increment, delete_stack, update_stack
from src.config import get_settings
from src.currency import normalize as normalize_currency
from src.db import get_session
from src.deck_export import EXPORT_FORMATS, collect_export_cards, render_deck
from src.decks import apply_deck_card_edit, create_deck, deck_coverage
from src.models import Card, CollectionCard, Deck, DeckCard
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
    card_id: int
    name: str
    quantity: int
    board: str
    owned: int
    matched: bool
    scryfall_id: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    language: str = "en"
    proxy: bool = False
    special: bool = False
    legality: str | None = None


class DeckDetailOut(BaseModel):
    id: int
    name: str
    pct_complete: int
    total_needed: int
    owned_count: int
    missing_count: int
    missing_cost: float
    fmt: str | None = None
    is_legal: bool | None = None
    illegal_count: int = 0
    main: list[DeckCardOut]
    side: list[DeckCardOut]


class DeckCreateIn(BaseModel):
    name: str = ""
    decklist: str = ""


class DeckUpdateIn(BaseModel):
    name: str | None = None


class DeckCardUpdateIn(BaseModel):
    scryfall_id: str | None = None
    language: str | None = None
    proxy: bool | None = None
    special: bool | None = None


class CollectionRowOut(BaseModel):
    id: int
    scryfall_id: str
    name: str
    set_code: str
    collector_number: str
    quantity: int
    finish: str
    condition: str | None = None
    language: str
    binder_name: str | None = None
    tags: list[str] | None = None
    price: float | None = None
    image: str | None = None


class CollectionListOut(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    items: list[CollectionRowOut]


class StackUpdateIn(BaseModel):
    quantity: int | None = None
    finish: str | None = None
    condition: str | None = None
    language: str | None = None
    binder: str | None = None
    tags: list[str] | None = None


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


def _deck_card_out(r) -> DeckCardOut:
    return DeckCardOut(
        card_id=r.card_id, name=r.name, quantity=r.quantity, board=r.board, owned=r.owned,
        matched=r.matched, scryfall_id=r.scryfall_id, set_code=r.set_code,
        collector_number=r.collector_number, language=r.language, proxy=r.proxy,
        special=r.special, legality=r.legality,
    )


async def _deck_detail(session: AsyncSession, deck: Deck, fmt: str | None) -> DeckDetailOut:
    cov = await deck_coverage(session, deck, fmt=fmt or None)
    return DeckDetailOut(
        id=deck.id, name=deck.name, pct_complete=cov.pct_complete,
        total_needed=cov.total_needed, owned_count=cov.owned_count,
        missing_count=cov.missing_count, missing_cost=round(cov.missing_cost, 2),
        fmt=cov.fmt, is_legal=cov.is_legal if cov.fmt else None, illegal_count=cov.illegal_count,
        main=[_deck_card_out(r) for r in cov.main], side=[_deck_card_out(r) for r in cov.side],
    )


async def _require_deck(session: AsyncSession, deck_id: int) -> Deck:
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found.")
    return deck


@router.get("/decks/{deck_id}", response_model=DeckDetailOut)
async def api_deck(
    deck_id: int, format: str = "", session: AsyncSession = Depends(get_session)
) -> DeckDetailOut:
    return await _deck_detail(session, await _require_deck(session, deck_id), format)


@router.post("/decks", response_model=DeckDetailOut, status_code=201)
async def api_deck_create(
    body: DeckCreateIn, session: AsyncSession = Depends(get_session)
) -> DeckDetailOut:
    _guard_writable()
    deck = await create_deck(session, body.name, body.decklist)
    return await _deck_detail(session, deck, None)


@router.patch("/decks/{deck_id}", response_model=DeckDetailOut)
async def api_deck_update(
    deck_id: int, body: DeckUpdateIn, session: AsyncSession = Depends(get_session)
) -> DeckDetailOut:
    _guard_writable()
    deck = await _require_deck(session, deck_id)
    if body.name is not None:
        deck.name = body.name.strip()[:256] or deck.name
    await session.commit()
    return await _deck_detail(session, deck, None)


@router.delete("/decks/{deck_id}", response_model=OkOut)
async def api_deck_delete(deck_id: int, session: AsyncSession = Depends(get_session)) -> OkOut:
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is not None:
        await session.delete(deck)
        await session.commit()
    return OkOut()


@router.get("/decks/{deck_id}/export", response_class=PlainTextResponse)
async def api_deck_export(
    deck_id: int, fmt: str = "text", session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    deck = await _require_deck(session, deck_id)
    if fmt not in EXPORT_FORMATS:
        fmt = "text"
    cards = await collect_export_cards(session, deck)
    return PlainTextResponse(render_deck(cards, fmt))


@router.patch("/decks/{deck_id}/cards/{card_id}", response_model=OkOut)
async def api_deck_card_update(
    deck_id: int, card_id: int, body: DeckCardUpdateIn,
    session: AsyncSession = Depends(get_session),
) -> OkOut:
    _guard_writable()
    dc = await session.get(DeckCard, card_id)
    if dc is None or dc.deck_id != deck_id:
        raise HTTPException(status_code=404, detail="Card not found in this deck.")
    await apply_deck_card_edit(
        session, dc, scryfall_id=body.scryfall_id, language=body.language,
        proxy=body.proxy, special=body.special,
    )
    return OkOut()


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


def _collection_row(s: CollectionCard, currency: str) -> CollectionRowOut:
    prices = s.card.prices or {}
    raw = prices.get("eur") if currency == "eur" else prices.get("usd")
    return CollectionRowOut(
        id=s.id, scryfall_id=str(s.scryfall_id), name=s.card.name, set_code=s.card.set_code,
        collector_number=s.card.collector_number, quantity=s.quantity, finish=s.finish,
        condition=s.condition, language=s.language, binder_name=s.binder_name, tags=s.tags,
        price=float(raw) if raw else None, image=image_url(s.card.raw),
    )


@router.get("/collection", response_model=CollectionListOut)
async def api_collection_list(
    page: int = 1,
    page_size: int = 50,
    q: str = "",
    binder: str = "",
    currency: str = "usd",
    session: AsyncSession = Depends(get_session),
) -> CollectionListOut:
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    cur = normalize_currency(currency) or "usd"
    stmt = select(CollectionCard).join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
    if q:
        stmt = stmt.where(func.lower(Card.name).like(f"%{q.lower()}%"))
    if binder:
        stmt = stmt.where(CollectionCard.binder_name == binder)
    total = await session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = (
        await session.execute(
            stmt.order_by(Card.name, CollectionCard.id)
            .offset((page - 1) * page_size).limit(page_size)
        )
    ).scalars().all()
    return CollectionListOut(
        total=total, page=page, page_size=page_size,
        total_pages=(total + page_size - 1) // page_size,
        items=[_collection_row(s, cur) for s in rows],
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


@router.patch("/collection/{row_id}", response_model=CollectionRowOut)
async def api_collection_update(
    row_id: int, body: StackUpdateIn, currency: str = "usd",
    session: AsyncSession = Depends(get_session),
) -> CollectionRowOut:
    _guard_writable()
    fields = body.model_dump(exclude_unset=True)
    stack = await update_stack(session, row_id, **fields)
    if stack is None:
        raise HTTPException(status_code=404, detail="Stack not found.")
    return _collection_row(stack, normalize_currency(currency) or "usd")


@router.delete("/collection/{row_id}", response_model=OkOut)
async def api_collection_delete(
    row_id: int, session: AsyncSession = Depends(get_session)
) -> OkOut:
    _guard_writable()
    if await delete_stack(session, row_id) is None:
        raise HTTPException(status_code=404, detail="Stack not found.")
    return OkOut()


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
