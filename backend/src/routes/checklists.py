"""Custom checklist routes: list, create from a pasted list, view coverage, delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.checklists import (
    add_checklist_items,
    add_checklist_missing,
    checklist_coverage,
    create_checklist,
    remove_checklist_item,
    rename_checklist_item,
)
from src.config import get_settings
from src.db import get_session
from src.models import Checklist
from src.templating import templates

_NOT_FOUND = "Checklist not found."

router = APIRouter(tags=["checklists"])


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.get("/checklists")
async def list_checklists() -> RedirectResponse:
    # The checklist index is now the Checklists tab of /collection.
    return RedirectResponse(url="/collection?tab=checklists", status_code=307)


@router.post("/checklists")
async def create(
    name: str = Form(""),
    cards: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    checklist = await create_checklist(session, name, cards)
    return RedirectResponse(url=f"/checklists/{checklist.id}", status_code=303)


@router.get("/checklists/{checklist_id}", response_class=HTMLResponse)
async def view_checklist(
    request: Request, checklist_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    checklist = await session.get(Checklist, checklist_id)
    if checklist is None:
        raise HTTPException(status_code=404, detail="Checklist not found.")
    return templates.TemplateResponse(
        request, "checklist_detail.html",
        {"cov": await checklist_coverage(session, checklist),
         "read_only": get_settings().read_only},
    )


@router.post("/checklists/{checklist_id}/delete")
async def delete_checklist(checklist_id: int, session: AsyncSession = Depends(get_session)):
    _guard_writable()
    checklist = await session.get(Checklist, checklist_id)
    if checklist is not None:
        await session.delete(checklist)
        await session.commit()
    return RedirectResponse(url="/checklists", status_code=303)


@router.post("/checklists/{checklist_id}/wishlist")
async def checklist_to_wishlist(checklist_id: int, session: AsyncSession = Depends(get_session)):
    _guard_writable()
    checklist = await session.get(Checklist, checklist_id)
    if checklist is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    await add_checklist_missing(session, checklist)
    return RedirectResponse(url="/wishlist", status_code=303)


def _back(checklist_id: int) -> RedirectResponse:
    return RedirectResponse(url=f"/checklists/{checklist_id}", status_code=303)


@router.post("/checklists/{checklist_id}/items")
async def add_items(
    checklist_id: int, cards: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    """Add one or more cards (pasted, one per line) to a checklist (#297)."""
    _guard_writable()
    checklist = await session.get(Checklist, checklist_id)
    if checklist is None:
        raise HTTPException(status_code=404, detail=_NOT_FOUND)
    await add_checklist_items(session, checklist, cards)
    return _back(checklist_id)


@router.post("/checklists/{checklist_id}/items/{item_id}")
async def edit_item(
    checklist_id: int, item_id: int, name: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Rename (and re-resolve) a checklist item (#297)."""
    _guard_writable()
    await rename_checklist_item(session, checklist_id, item_id, name)
    return _back(checklist_id)


@router.post("/checklists/{checklist_id}/items/{item_id}/delete")
async def delete_item(
    checklist_id: int, item_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    """Remove one card from a checklist (#297)."""
    _guard_writable()
    await remove_checklist_item(session, checklist_id, item_id)
    return _back(checklist_id)
