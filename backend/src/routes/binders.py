"""Custom binders (#206): a binder's card view + create/rename/delete + card-page add/remove.

The binder *index* lives on the collection page's Binders tab (``/collection?tab=binders``); the
legacy import-``binder_name`` browse (``/binders/cards``) is kept for cards imported with a binder
column.
"""

from __future__ import annotations

from dataclasses import dataclass

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.binder_service import (
    add_card,
    all_binders,
    binder_cards,
    binders_for_card,
    create_binder,
    delete_binder,
    remove_card,
    rename_binder,
)
from src.config import get_settings
from src.db import get_session
from src.models import Binder, Card, CollectionCard
from src.scryfall.images import ImageCache
from src.scryfall.mapping import image_url as cdn_image_url
from src.templating import templates

router = APIRouter(tags=["binders"])
_cache = ImageCache()

NONE_SENTINEL = "__none__"


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


def _image(card: Card) -> str:
    sid = str(card.scryfall_id)
    return _cache.url_path(sid) if _cache.is_cached(sid) else (cdn_image_url(card.raw) or "")


@dataclass
class CardView:
    card: Card
    quantity: int
    image: str


# --- custom binders (#206) ----------------------------------------------------------------------

@router.get("/binders")
async def binders_home() -> RedirectResponse:
    # The binder index lives on the collection Binders tab.
    return RedirectResponse(url="/collection?tab=binders", status_code=307)


@router.get("/binders/view/{binder_id}", response_class=HTMLResponse)
async def binder_view(
    request: Request, binder_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    binder = await session.get(Binder, binder_id)
    if binder is None:
        raise HTTPException(status_code=404, detail="Binder not found.")
    cards = await binder_cards(session, binder_id)
    return templates.TemplateResponse(
        request, "binder_view.html",
        {"binder": binder, "views": [(c, _image(c)) for c in cards],
         "read_only": get_settings().read_only},
    )


@router.post("/binders/new")
async def new_binder(
    name: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await create_binder(session, name)
    return RedirectResponse(url="/collection?tab=binders", status_code=303)


@router.post("/binders/{binder_id}/rename")
async def rename_binder_route(
    binder_id: int, name: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await rename_binder(session, binder_id, name)
    return RedirectResponse(url=f"/binders/view/{binder_id}", status_code=303)


@router.post("/binders/{binder_id}/delete")
async def delete_binder_route(
    binder_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await delete_binder(session, binder_id)
    return RedirectResponse(url="/collection?tab=binders", status_code=303)


@router.post("/binders/{binder_id}/remove-card")
async def remove_card_route(
    binder_id: int, scryfall_id: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await remove_card(session, binder_id, scryfall_id)
    return RedirectResponse(url=f"/binders/view/{binder_id}", status_code=303)


# Add/remove a card to/from a binder from the card detail page (HTMX-swaps the control).

async def _card_binders_partial(
    request: Request, session: AsyncSession, scryfall_id: str
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "_card_binders.html",
        {"card_id": scryfall_id, "binders": await all_binders(session),
         "in_ids": await binders_for_card(session, scryfall_id),
         "read_only": get_settings().read_only},
    )


@router.post("/card/{scryfall_id}/binder-add", response_class=HTMLResponse)
async def card_binder_add(
    request: Request, scryfall_id: str, binder_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    if binder_id.strip():
        await add_card(session, int(binder_id), scryfall_id)
    return await _card_binders_partial(request, session, scryfall_id)


@router.post("/card/{scryfall_id}/binder-remove", response_class=HTMLResponse)
async def card_binder_remove(
    request: Request, scryfall_id: str, binder_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    if binder_id.strip():
        await remove_card(session, int(binder_id), scryfall_id)
    return await _card_binders_partial(request, session, scryfall_id)


@router.get("/binders/cards", response_class=HTMLResponse)
async def binder_cards_browse(
    request: Request, name: str = "", session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    is_none = name == NONE_SENTINEL or name == ""
    condition = CollectionCard.binder_name.is_(None) if is_none else (
        CollectionCard.binder_name == name
    )
    rows = (
        await session.execute(
            select(Card, func.sum(CollectionCard.quantity))
            .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
            .where(condition)
            .group_by(Card)
            .order_by(Card.name)
        )
    ).all()
    views = [CardView(card=c, quantity=int(q), image=_image(c)) for c, q in rows]
    return templates.TemplateResponse(
        request,
        "binder_detail.html",
        {"views": views, "label": "Unsorted" if is_none else name},
    )
