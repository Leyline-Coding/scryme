"""Custom checklist routes: list, create from a pasted list, view coverage, delete."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.checklists import (
    add_checklist_missing,
    checklist_coverage,
    create_checklist,
)
from src.config import get_settings
from src.db import get_session
from src.models import Checklist
from src.templating import templates

router = APIRouter(tags=["checklists"])


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.get("/checklists", response_class=HTMLResponse)
async def list_checklists(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    rows = await session.execute(
        select(Checklist, func.count())
        .outerjoin(Checklist.items)
        .group_by(Checklist.id)
        .order_by(Checklist.created_at.desc())
    )
    checklists = [(c, n) for c, n in rows.all()]
    return templates.TemplateResponse(
        request, "checklists.html",
        {"checklists": checklists, "read_only": get_settings().read_only},
    )


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
        raise HTTPException(status_code=404, detail="Checklist not found.")
    await add_checklist_missing(session, checklist)
    return RedirectResponse(url="/wishlist", status_code=303)
