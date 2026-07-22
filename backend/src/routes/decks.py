"""Deck routes: list, create from a pasted decklist, view coverage, delete.

Mutations are blocked in read-only (demo) mode, mirroring uploads.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.brackets import BRACKET_LABELS, estimate_bracket, normalize_bracket
from src.collection_edit import add_or_increment
from src.config import get_settings
from src.currency import get_currency, info
from src.db import get_session
from src.deck_builder import BuildError, build_commander_deck, owned_commanders
from src.deck_export import EXPORT_FORMATS, collect_export_cards, render_deck
from src.deck_import import (
    PROFILE_PROVIDERS,
    SUPPORTED,
    DeckImportError,
    detect_profile,
    fetch_deck_from_url,
    fetch_profile_decks,
)
from src.deck_suggest import suggest_owned_upgrades
from src.deck_versions import diff_cards, list_versions, save_version, snapshot_cards
from src.decks import (
    DECK_LANGUAGES,
    LEGALITY_FORMATS,
    add_card_to_deck,
    apply_deck_card_edit,
    create_deck,
    deck_coverage,
    deck_printings,
    deck_stats,
    resolve_ownership_rows,
)
from src.llm import get_config
from src.models import Card, Deck, DeckCard, DeckVersion
from src.pricing import get_price_source
from src.scryfall.images import ImageCache
from src.scryfall.mapping import image_url as cdn_image_url
from src.templating import templates
from src.wishlist import add_deck_missing

router = APIRouter(tags=["decks"])

_DECK_NOT_FOUND = "Deck not found."
_DECK_NEW_TMPL = "deck_new.html"
_img_cache = ImageCache()


async def _deck_images(session: AsyncSession, deck: Deck) -> dict[str, str]:
    """Map each deck card's scryfall_id -> image URL (cached path, else CDN) for the grid view."""
    sids = [c.scryfall_id for c in deck.cards if c.scryfall_id]
    if not sids:
        return {}
    rows = (await session.execute(
        select(Card.scryfall_id, Card.raw).where(Card.scryfall_id.in_(sids))
    )).all()
    out: dict[str, str] = {}
    for sid, raw in rows:
        s = str(sid)
        cached = _img_cache.is_cached(s)
        out[s] = _img_cache.url_path(s) if cached else (cdn_image_url(raw or {}) or "")
    return out
_CURRENT = "current"  # diff source referring to the deck's live state (vs a saved version)


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
    return templates.TemplateResponse(
        request, _DECK_NEW_TMPL, {"supported": SUPPORTED, "providers": PROFILE_PROVIDERS})


async def _add_owned_pairs(session: AsyncSession, pairs: list[tuple[str, int]]) -> None:
    """Add each (scryfall_id, quantity) to the collection (increments existing stacks)."""
    for sid, qty in pairs:
        await add_or_increment(session, sid, qty)


async def _mark_all_owned(session: AsyncSession, decklist: str) -> None:
    """Add every matched card in a decklist to the collection at its needed quantity."""
    rows = await resolve_ownership_rows(session, decklist)
    await _add_owned_pairs(session, [(r.scryfall_id, r.quantity) for r in rows if r.matched])


async def _finish_import(
    request: Request, session: AsyncSession, name: str, decklist: str, ownership: str,
):
    """Create a deck (unowned / fully-owned), or show the owned-cards checklist for partial."""
    if ownership == "partial":
        return templates.TemplateResponse(
            request, "deck_ownership.html",
            {"name": name, "decklist": decklist,
             "rows": await resolve_ownership_rows(session, decklist)},
        )
    deck = await create_deck(session, name, decklist)
    if ownership == "full":
        await _mark_all_owned(session, decklist)
    return RedirectResponse(url=f"/decks/{deck.id}", status_code=303)


@router.post("/decks/import-url")
async def import_url(
    request: Request,
    url: str = Form(""),
    ownership: str = Form("unowned"),
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    try:
        name, decklist = await fetch_deck_from_url(url.strip())
    except DeckImportError as exc:
        return templates.TemplateResponse(
            request, _DECK_NEW_TMPL, {"supported": SUPPORTED, "error": str(exc), "url": url},
        )
    return await _finish_import(request, session, name, decklist, ownership)


@router.post("/decks/import-profile", response_class=HTMLResponse)
async def import_profile(
    request: Request,
    provider: str = Form("moxfield"),
    who: str = Form(""),
    ownership: str = Form("unowned"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """List a Moxfield/Archidekt profile's public decks to pick from (#299)."""
    _guard_writable()
    # A pasted profile URL wins over the provider dropdown; otherwise it's a bare username.
    found = detect_profile(who)
    provider, username = found if found else (provider, who.strip())
    try:
        decks = await fetch_profile_decks(provider, username)
    except DeckImportError as exc:
        return templates.TemplateResponse(
            request, _DECK_NEW_TMPL,
            {"supported": SUPPORTED, "error": str(exc), "providers": PROFILE_PROVIDERS},
        )
    return templates.TemplateResponse(
        request, "deck_profile_select.html",
        {"decks": decks, "provider": provider, "username": username, "ownership": ownership},
    )


@router.post("/decks/import-profile/confirm")
async def import_profile_confirm(
    urls: list[str] = Form(default=[]),
    ownership: str = Form("unowned"),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Bulk-import the ticked profile decks; a deck that fails is skipped, not fatal (#299)."""
    _guard_writable()
    for url in urls:
        try:
            name, decklist = await fetch_deck_from_url(url)
        except DeckImportError:
            continue  # skip this deck, keep importing the rest
        await create_deck(session, name, decklist)
        if ownership == "full":
            await _mark_all_owned(session, decklist)
    return RedirectResponse(url="/collection?tab=decks", status_code=303)


@router.post("/decks")
async def create(
    request: Request,
    name: str = Form(""),
    decklist: str = Form(""),
    ownership: str = Form("unowned"),
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    return await _finish_import(request, session, name, decklist, ownership)


@router.post("/decks/owned-confirm")
async def owned_confirm(
    name: str = Form(""),
    decklist: str = Form(""),
    owned: list[str] = Form(default=[]),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Create the deck and add the cards the user checked as owned (values are "sid|qty")."""
    _guard_writable()
    deck = await create_deck(session, name, decklist)
    pairs: list[tuple[str, int]] = []
    for token in owned:
        sid, _, qty = token.partition("|")
        if sid:
            pairs.append((sid, int(qty) if qty.isdigit() else 1))
    await _add_owned_pairs(session, pairs)
    return RedirectResponse(url=f"/decks/{deck.id}", status_code=303)


@router.get("/decks/build", response_class=HTMLResponse)
async def build_form(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    _guard_writable()
    return templates.TemplateResponse(
        request, "deck_build.html",
        {"commanders": await owned_commanders(session),
         "ai_ready": (await get_config(session)).ready},
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
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    currency = get_currency(request)
    source = get_price_source(request)
    coverage = await deck_coverage(session, deck, fmt=format or None, currency=currency,
                                   source=source)
    return templates.TemplateResponse(
        request,
        "deck_detail.html",
        {
            "cov": coverage,
            "deck": deck,
            "formats": LEGALITY_FORMATS,
            "versions": await list_versions(session, deck_id),
            "images": await _deck_images(session, deck),
            "bracket": await estimate_bracket(session, deck),
            "bracket_labels": BRACKET_LABELS,
            "stats": await deck_stats(session, deck, currency, source),
            "export_formats": EXPORT_FORMATS,
            "cur": info(currency),
            "read_only": get_settings().read_only,
            "ai_ready": (await get_config(session)).ready,
        },
    )


@router.post("/decks/{deck_id}/bracket", response_class=HTMLResponse)
async def set_deck_bracket(
    request: Request, deck_id: int, bracket: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Manually set (or clear) a deck's Commander bracket, then re-render its panel (#159)."""
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    deck.bracket_override = normalize_bracket(bracket)
    await session.commit()
    return templates.TemplateResponse(
        request, "_deck_bracket.html",
        {"deck": deck, "bracket": await estimate_bracket(session, deck),
         "bracket_labels": BRACKET_LABELS, "read_only": get_settings().read_only},
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
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
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
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    await add_deck_missing(session, deck)
    return RedirectResponse(url="/wishlist", status_code=303)


# --- deck versions + diff (#100) ----------------------------------------------------------------

@router.post("/decks/{deck_id}/versions")
async def save_deck_version(
    deck_id: int, label: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    """Snapshot the deck's current card list as a named version."""
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    await save_version(session, deck, label)
    return RedirectResponse(url=f"/decks/{deck_id}", status_code=303)


@router.post("/decks/{deck_id}/versions/{version_id}/delete")
async def delete_deck_version(
    deck_id: int, version_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    version = await session.get(DeckVersion, version_id)
    if version is not None and version.deck_id == deck_id:
        await session.delete(version)
        await session.commit()
    return RedirectResponse(url=f"/decks/{deck_id}", status_code=303)


async def _diff_source(session: AsyncSession, deck: Deck, src: str):
    """Resolve a diff source — the literal ``current`` or a version id — to (label, cards)."""
    if src == _CURRENT:
        return _CURRENT, snapshot_cards(deck)
    try:
        version = await session.get(DeckVersion, int(src))
    except (ValueError, TypeError):
        return None
    if version is None or version.deck_id != deck.id:
        return None
    return version.label, version.cards


@router.get("/decks/{deck_id}/diff", response_class=HTMLResponse)
async def deck_diff_view(
    request: Request, deck_id: int, a: str = "", b: str = _CURRENT,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Diff two of a deck's versions (or a version and its current state)."""
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    versions = await list_versions(session, deck_id)
    if not a:  # default: newest saved version -> current ("what changed since my last save?")
        a = str(versions[0].id) if versions else _CURRENT
    src_a = await _diff_source(session, deck, a)
    src_b = await _diff_source(session, deck, b)
    if src_a is None or src_b is None:
        raise HTTPException(status_code=404, detail="Version not found.")
    return templates.TemplateResponse(
        request, "deck_diff.html",
        {"deck": deck, "versions": versions, "a": a, "b": b,
         "a_label": src_a[0], "b_label": src_b[0], "diff": diff_cards(src_a[1], src_b[1])},
    )


async def _render_owned_upgrades(
    request: Request, deck: Deck, session: AsyncSession, added: str | None = None
) -> HTMLResponse:
    currency = get_currency(request)
    source = get_price_source(request)
    result = await suggest_owned_upgrades(session, deck, currency, source)
    return templates.TemplateResponse(
        request, "_deck_upgrade_owned.html",
        {"result": result, "deck": deck, "cur": info(currency),
         "read_only": get_settings().read_only, "added": added},
    )


@router.post("/decks/{deck_id}/suggest-owned", response_class=HTMLResponse)
async def deck_suggest_owned(
    request: Request, deck_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Heuristic owned-card upgrade suggestions by thin role (#181) — no LLM, read-only safe."""
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    return await _render_owned_upgrades(request, deck, session)


@router.post("/decks/{deck_id}/add-owned", response_class=HTMLResponse)
async def deck_add_owned(
    request: Request, deck_id: int, scryfall_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Add one owned suggestion to the deck and re-render the panel so the pick drops off."""
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    added = None
    try:
        sid = uuid.UUID(scryfall_id)
    except (ValueError, AttributeError):
        sid = None
    card = await session.get(Card, sid) if sid else None
    if card is not None:
        await add_card_to_deck(session, deck_id, card)
        await session.refresh(deck)
        added = card.name
    return await _render_owned_upgrades(request, deck, session, added=added)
