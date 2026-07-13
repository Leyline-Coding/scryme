"""Deck routes: list, create from a pasted decklist, view coverage, delete.

Mutations are blocked in read-only (demo) mode, mirroring uploads.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.currency import get_currency, info
from src.db import get_session
from src.deck_builder import BuildError, build_commander_deck, owned_commanders
from src.deck_export import EXPORT_FORMATS, collect_export_cards, render_deck
from src.deck_import import SUPPORTED, DeckImportError, fetch_deck_from_url
from src.decks import (
    DECK_LANGUAGES,
    LEGALITY_FORMATS,
    apply_deck_card_edit,
    create_deck,
    deck_coverage,
    deck_printings,
    deck_stats,
)
from src.models import Deck, DeckCard
from src.templating import templates
from src.wishlist import add_deck_missing

router = APIRouter(tags=["decks"])


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.get("/decks")
async def list_decks() -> RedirectResponse:
    # The deck index is now the Decks tab of /collection.
    return RedirectResponse(url="/collection?tab=decks", status_code=307)


@router.get("/decks/new", response_class=HTMLResponse)
async def new_deck(request: Request) -> HTMLResponse:
    _guard_writable()
    return templates.TemplateResponse(request, "deck_new.html", {"supported": SUPPORTED})


@router.post("/decks/import-url")
async def import_url(
    request: Request,
    url: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    try:
        name, decklist = await fetch_deck_from_url(url.strip())
    except DeckImportError as exc:
        return templates.TemplateResponse(
            request, "deck_new.html", {"supported": SUPPORTED, "error": str(exc), "url": url},
        )
    deck = await create_deck(session, name, decklist)
    return RedirectResponse(url=f"/decks/{deck.id}", status_code=303)


@router.post("/decks")
async def create(
    name: str = Form(""),
    decklist: str = Form(""),
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    deck = await create_deck(session, name, decklist)
    return RedirectResponse(url=f"/decks/{deck.id}", status_code=303)


@router.get("/decks/build", response_class=HTMLResponse)
async def build_form(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    _guard_writable()
    return templates.TemplateResponse(
        request, "deck_build.html", {"commanders": await owned_commanders(session)}
    )


@router.post("/decks/build", response_class=HTMLResponse)
async def build_preview(
    request: Request,
    commander: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    try:
        built = await build_commander_deck(session, commander)
    except BuildError as exc:
        return templates.TemplateResponse(
            request, "deck_build.html",
            {"commanders": await owned_commanders(session), "error": exc.message,
             "commander": commander},
        )
    return templates.TemplateResponse(request, "deck_build_result.html", {"built": built})


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
    currency = get_currency(request)
    coverage = await deck_coverage(session, deck, fmt=format or None, currency=currency)
    return templates.TemplateResponse(
        request,
        "deck_detail.html",
        {
            "cov": coverage,
            "formats": LEGALITY_FORMATS,
            "stats": await deck_stats(session, deck, currency),
            "export_formats": EXPORT_FORMATS,
            "cur": info(currency),
            "read_only": get_settings().read_only,
        },
    )


async def _get_deck_card(session: AsyncSession, deck_id: int, card_id: int) -> DeckCard:
    dc = await session.get(DeckCard, card_id)
    if dc is None or dc.deck_id != deck_id:
        raise HTTPException(status_code=404, detail="Card not found in this deck.")
    return dc


@router.get("/decks/{deck_id}/card/{card_id}/edit", response_class=HTMLResponse)
async def edit_deck_card(
    request: Request,
    deck_id: int,
    card_id: int,
    format: str = "",
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Inline editor for a deck line's printing + proxy/special flag."""
    _guard_writable()
    dc = await _get_deck_card(session, deck_id, card_id)
    if dc.oracle_id is None:
        raise HTTPException(status_code=404, detail="This line has no matched card to edit.")
    return templates.TemplateResponse(
        request,
        "_deck_card_edit.html",
        {
            "deck_id": deck_id,
            "card_id": card_id,
            "printings": await deck_printings(session, dc.oracle_id),
            "current_sid": str(dc.scryfall_id) if dc.scryfall_id else "",
            "languages": DECK_LANGUAGES,
            "current_lang": dc.language,
            "proxy": dc.proxy,
            "special": dc.special,
            "fmt": format if format in LEGALITY_FORMATS else "",
        },
    )


@router.post("/decks/{deck_id}/card/{card_id}")
async def update_deck_card(
    deck_id: int,
    card_id: int,
    scryfall_id: str = Form(""),
    language: str = Form("en"),
    proxy: str | None = Form(None),
    special: str | None = Form(None),
    format: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Set the printing/language for a deck line and toggle its proxy/special flags."""
    _guard_writable()
    dc = await _get_deck_card(session, deck_id, card_id)
    await apply_deck_card_edit(
        session, dc,
        scryfall_id=scryfall_id or None,
        language=language,
        proxy=proxy is not None,
        special=special is not None,
    )
    url = f"/decks/{deck_id}" + (f"?format={format}" if format in LEGALITY_FORMATS else "")
    # HTMX form post -> refresh the whole deck page (coverage + legality change).
    return Response(status_code=204, headers={"HX-Redirect": url})


def _slug(name: str) -> str:
    cleaned = "".join(ch if ch.isalnum() else "-" for ch in (name or "").lower())
    return "-".join(filter(None, cleaned.split("-")))[:60] or "deck"


@router.get("/decks/{deck_id}/export")
async def export_deck(
    deck_id: int, fmt: str = "text", session: AsyncSession = Depends(get_session)
) -> PlainTextResponse:
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found.")
    if fmt not in EXPORT_FORMATS:
        fmt = "text"
    suffix, media_type, _label = EXPORT_FORMATS[fmt]
    cards = await collect_export_cards(session, deck)
    content = render_deck(cards, fmt)
    filename = f"{_slug(deck.name)}.{suffix}"
    return PlainTextResponse(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.post("/decks/{deck_id}/delete")
async def delete_deck(deck_id: int, session: AsyncSession = Depends(get_session)):
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is not None:
        await session.delete(deck)
        await session.commit()
    return RedirectResponse(url="/decks", status_code=303)


@router.post("/decks/{deck_id}/wishlist")
async def deck_to_wishlist(deck_id: int, session: AsyncSession = Depends(get_session)):
    """Add every card the deck is still missing to the wishlist, then show the wishlist."""
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail="Deck not found.")
    await add_deck_missing(session, deck)
    return RedirectResponse(url="/wishlist", status_code=303)
