"""Wishlist routes: view the want list, add/remove printings. Mutations are read-only-guarded."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.scryfall.images import ImageCache
from src.scryfall.mapping import image_url as cdn_image_url
from src.templating import templates
from src.wishlist import add_to_wishlist, list_wishlist, remove_from_wishlist

router = APIRouter(tags=["wishlist"])
_cache = ImageCache()


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


def _image(card) -> str:
    sid = str(card.scryfall_id)
    return _cache.url_path(sid) if _cache.is_cached(sid) else (cdn_image_url(card.raw) or "")


@router.get("/wishlist", response_class=HTMLResponse)
async def wishlist_page(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    view = await list_wishlist(session)
    rows = [(item, _image(item.card)) for item in view.items]
    return templates.TemplateResponse(
        request,
        "wishlist.html",
        {"rows": rows, "view": view, "read_only": get_settings().read_only},
    )


def _button(request: Request, scryfall_id: str, wishlisted: bool) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "_wishlist_button.html",
        {
            "card_id": scryfall_id,
            "wishlisted": wishlisted,
            "read_only": get_settings().read_only,
        },
    )


@router.post("/wishlist/add", response_class=HTMLResponse)
async def add(
    request: Request,
    scryfall_id: str = Form(...),
    quantity: int = Form(1),
    note: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    await add_to_wishlist(session, scryfall_id, quantity, note or None)
    return _button(request, scryfall_id, wishlisted=True)


@router.post("/wishlist/remove")
async def remove(
    request: Request,
    scryfall_id: str = Form(...),
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    await remove_from_wishlist(session, scryfall_id)
    # The card page swaps the toggle button back in (HTMX); the wishlist page is a plain form POST,
    # so reload it to drop the removed row.
    if request.headers.get("HX-Request") == "true":
        return _button(request, scryfall_id, wishlisted=False)
    return RedirectResponse(url="/wishlist", status_code=303)
