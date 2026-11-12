"""Custom binders (#206): a binder's card view + create/rename/delete + card-page add/remove.

The binder *index* lives on the collection page's Binders tab (/collection/binders); the
legacy import-``binder_name`` browse (``/binders/cards``) is kept for cards imported with a binder
column.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.binder_service import (
    get_all_binder_cards,
    create_binder,
    delete_binder,
    rename_binder,
)
from src.collection_edit import move_stack_location, _STACK_NOT_FOUND
from src.config import get_settings
from src.db import get_session
from src.models import Binder, Card
from src.routes._safe import local_redirect
from src.scryfall.images import ImageCache
from src.share import BINDER, links_for, revoke_links_for
from src.templating import templates

router = APIRouter(tags=["binders"])

_BINDER_NOT_FOUND = "Binder not found."
_BINDERS_URL = "/collection/binders"
_cache = ImageCache()

NONE_SENTINEL = "__none__"


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@dataclass
class CardView:
    card: Card
    quantity: int
    image: str


# --- custom binders (#206) ----------------------------------------------------------------------

@router.get("/binders")
async def binders_home() -> RedirectResponse:
    # The binder index lives on the collection Binders tab.
    return RedirectResponse(url=_BINDERS_URL, status_code=307)


@router.get("/binders/view/{binder_id}", response_class=HTMLResponse)
async def binder_view(
    request: Request, binder_id: int, session: AsyncSession = Depends(get_session),
    shared: str = "",
) -> HTMLResponse:
    """``shared`` is a token just minted for this binder, shown once so it can be copied (#80)."""
    binder = await session.get(Binder, binder_id)
    if binder is None:
        raise HTTPException(status_code=404, detail=_BINDER_NOT_FOUND)
    cards = await get_all_binder_cards(session, binder_id)
    return templates.TemplateResponse(
        request, "binder_view.html",
        {"binder": binder, 
         "cards": cards,
         "read_only": get_settings().read_only,
         "share_kind": BINDER, "share_target": binder.id,
         "share_links": await links_for(session, BINDER, binder.id),
         "share_new": shared},
    )


@router.post("/binders/new")
async def new_binder(
    name: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await create_binder(session, name)
    return RedirectResponse(url=_BINDERS_URL, status_code=303)


@router.post("/binders/{binder_id}/rename")
async def rename_binder_route(
    binder_id: int, name: str = Form(""), session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    _guard_writable()
    binder = await session.get(Binder, binder_id)
    if binder is None: raise HTTPException(status_code=404, detail=_BINDER_NOT_FOUND)
    new_binder_name = await rename_binder(session, binder, name)
    return PlainTextResponse(new_binder_name)


@router.post("/binders/{binder_id}/delete")
async def delete_binder_route(
    binder_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    # Withdraw any share links first: a link outliving its binder would 404, which reads as a
    # broken app rather than as "that isn't shared any more" (#80).
    await revoke_links_for(session, BINDER, binder_id)
    await delete_binder(session, binder_id)
    return RedirectResponse(url=_BINDERS_URL, status_code=303)


@router.post("/binders/{binder_id}/stack/{stack_id}/locate")
async def locate_binder_stack(
    binder_id: int, 
    stack_id: int,
    location_id: int = Form(None),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    _guard_writable()
    sid = await move_stack_location(session, stack_id, location_id)
    if(not sid): raise HTTPException(status_code=404, detail=_STACK_NOT_FOUND)
    return local_redirect(f"/binders/view/{binder_id}")

