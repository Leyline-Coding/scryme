"""Deck routes: list, create from a pasted decklist, view coverage, delete.

Mutations are blocked in read-only (demo) mode, mirroring uploads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.decks import LEGALITY_FORMATS, create_deck, deck_coverage
from src.models import Deck
from src.templating import templates

router = APIRouter(tags=["decks"])


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.get("/decks", response_class=HTMLResponse)
async def list_decks(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    rows = await session.execute(
        select(Deck, func.count())
        .outerjoin(Deck.cards)
        .group_by(Deck.id)
        .order_by(Deck.created_at.desc())
    )
    decks = [(d, n) for d, n in rows.all()]
    return templates.TemplateResponse(
        request, "decks.html", {"decks": decks, "read_only": get_settings().read_only}
    )


@router.get("/decks/new", response_class=HTMLResponse)
async def new_deck(request: Request) -> HTMLResponse:
    _guard_writable()
    return templates.TemplateResponse(request, "deck_new.html", {})


@router.post("/decks")
async def create(
    name: str = Form(""),
    decklist: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    deck = await create_deck(session, name, decklist)
    return RedirectResponse(url=f"/decks/{deck.id}", status_code=303)


@router.get("/decks/{deck_id}", response_class=HTMLResponse)
async def view_deck(
    request: Request,
    deck_id: int,
    format: str = "",
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found.")
    coverage = await deck_coverage(session, deck, fmt=format or None)
    return templates.TemplateResponse(
        request,
        "deck_detail.html",
        {
            "cov": coverage,
            "formats": LEGALITY_FORMATS,
            "read_only": get_settings().read_only,
        },
    )


@router.post("/decks/{deck_id}/delete")
async def delete_deck(deck_id: int, session: AsyncSession = Depends(get_session)):
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is not None:
        await session.delete(deck)
        await session.commit()
    return RedirectResponse(url="/decks", status_code=303)
