"""Deck routes: list, create from a pasted decklist, view coverage, delete.

Mutations are blocked in read-only (demo) mode, mirroring uploads.
"""

from __future__ import annotations

import datetime
import html
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, Response
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.brackets import BRACKET_LABELS, estimate_bracket, normalize_bracket
from src.collection_edit import add_or_increment
from src.config import get_settings
from src.currency import fx, get_currency, info, unit_price
from src.db import get_session
from src.deck_builder import BuildError, build_commander_deck, owned_commanders
from src.deck_card import quickswap_deck_card
from src.deck_export import EXPORT_FORMATS, collect_export_cards, render_deck
from src.deck_import import (
    PROFILE_PROVIDERS,
    SUPPORTED,
    DeckImportError,
    detect_profile,
    fetch_deck_from_url,
    fetch_profile_decks,
    normalize_deck_url,
)
from src.deck_suggest import suggest_owned_upgrades
from src.deck_sync import sync_printing, sync_quantity
from src.deck_versions import diff_cards, list_versions, save_version, snapshot_cards
from src.decks import (
    DECK_LANGUAGES,
    LEGALITY_FORMATS,
    PROXY_SOURCES,
    add_card_to_deck,
    apply_deck_card_edit,
    checkout_deck,
    create_deck,
    deck_coverage,
    deck_printings,
    deck_stats,
    deconstruct_deck,
    match_deck_cards_in_deck_collection_cards,
    move_qty_from_existing_stack,
    proxy_cards,
    rename_deck,
    resolve_ownership_rows,
    resync_printings,
)
from src.llm import get_config
from src.locations import find_or_create_grouped_locations, find_or_create_locations
from src.models import Card, CollectionCard, Deck, DeckCard, DeckVersion, Location, Sleeve
from src.pricing import get_price_source, resolve_prices
from src.scryfall.images import ImageCache
from src.scryfall.mapping import image_url as cdn_image_url
from src.share import DECK, just_minted_token, links_for, revoke_links_for
from src.templating import templates
from src.wishlist import add_deck_missing

router = APIRouter(tags=["decks"])

_DECK_NOT_FOUND = "Deck not found."
_DECK_NEW_TMPL = "deck_new.html"
_img_cache = ImageCache()


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


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


@router.get("/decks")
async def list_decks() -> RedirectResponse:
    # The deck index is now the Decks tab of /collection.
    return RedirectResponse(url="/collection/decks", status_code=307)


@router.get("/decks/new", response_class=HTMLResponse)
async def new_deck(request: Request) -> HTMLResponse:
    _guard_writable()
    return templates.TemplateResponse(
        request, _DECK_NEW_TMPL, {"supported": SUPPORTED, "providers": PROFILE_PROVIDERS})


async def _add_owned_pairs(session: AsyncSession, pairs: list[tuple[str, int, str]]) -> None:
    """Add each (scryfall_id, quantity, finish) to the collection (increments matching stacks)."""
    for sid, qty, finish in pairs:
        await add_or_increment(session, sid, qty, finish=finish)


async def _mark_all_owned(session: AsyncSession, decklist: str) -> None:
    """Add every matched card to the collection at its needed quantity, in the deck's finish."""
    rows = await resolve_ownership_rows(session, decklist)
    await _add_owned_pairs(
        session, [(r.scryfall_id, r.quantity, r.finish) for r in rows if r.matched])


async def _finish_import(
    request: Request, session: AsyncSession, name: str, decklist: str, ownership: str,
    source_url: str = "",
):
    """Create a deck (unowned / fully-owned), or show the owned-cards checklist for partial."""
    if ownership == "partial":
        return templates.TemplateResponse(
            request, "deck_ownership.html",
            {"name": name, "decklist": decklist,
             "rows": await resolve_ownership_rows(session, decklist)},
        )
    deck = await create_deck(session, name, decklist, source_url=source_url)
    if ownership == "full":
        deck.ownership = "full"
        for dc in deck.cards:
            dc.owned = dc.oracle_id is not None  # every matched card is owned
        await session.commit()
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
    # Remember where it came from so its printings can be re-synced from the source later —
    # as the normalised https URL, never the raw form value (it is rendered as a link).
    return await _finish_import(request, session, name, decklist, ownership,
                                source_url=normalize_deck_url(url) or "")


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
    return RedirectResponse(url="/collection/decks", status_code=303)


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
    pairs: list[tuple[str, int, str]] = []
    for token in owned:
        sid, _, rest = token.partition("|")
        qty, _, finish = rest.partition("|")
        if sid:
            pairs.append((sid, int(qty) if qty.isdigit() else 1, finish or "normal"))
    owned_sids = {sid for sid, _qty, _finish in pairs}
    deck.ownership = "partial"
    for dc in deck.cards:
        dc.owned = dc.scryfall_id is not None and str(dc.scryfall_id) in owned_sids
    await session.commit()
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
    sort_by: str = "name",
    sort_dir: str = "asc",
    session: AsyncSession = Depends(get_session),
    shared: str = "",
) -> HTMLResponse:
    """``shared`` is a token just minted for this deck, shown once so it can be copied (#80)."""
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    currency = get_currency(request)
    source = get_price_source(request)
    coverage = await deck_coverage(session, deck, fmt=format or None, currency=currency,
                                    source=source)

    # Get locations for the location picker
    locations = await find_or_create_locations(session, binders=True, boxes=True, decks=True)

    # Normalize sort parameters
    sort_by = sort_by.lower() if sort_by else "name"
    sort_dir = sort_dir.lower() if sort_dir else "asc"

    # Apply sorting to the deck cards
    if sort_by == "quantity":
        # Sort by quantity (descending by default)
        coverage.main.sort(key=lambda x: x.quantity, reverse=(sort_dir == "desc"))
        coverage.side.sort(key=lambda x: x.quantity, reverse=(sort_dir == "desc"))
        coverage.main_combined.sort(key=lambda x: x.quantity, reverse=(sort_dir == "desc"))
        coverage.side_combined.sort(key=lambda x: x.quantity, reverse=(sort_dir == "desc"))
    elif sort_by == "price":
        # Sort by price (descending by default)
        def _get_card_price(card_row):
            if card_row.scryfall_card and card_row.scryfall_card.prices:
                return unit_price(
                    resolve_prices(
                        card_row.scryfall_card.prices, card_row.scryfall_card.market_prices, source
                    ),
                    card_row.finish, currency
                )
            return 0.0

        coverage.main.sort(key=lambda x: _get_card_price(x), reverse=(sort_dir == "desc"))
        coverage.side.sort(key=lambda x: _get_card_price(x), reverse=(sort_dir == "desc"))
        coverage.main_combined.sort(key=lambda x: _get_card_price(x), reverse=(sort_dir == "desc"))
        coverage.side_combined.sort(key=lambda x: _get_card_price(x), reverse=(sort_dir == "desc"))
    else:  # default to name
        # Sort by name (ascending by default)
        coverage.main.sort(key=lambda x: x.name.lower(), reverse=(sort_dir == "desc"))
        coverage.side.sort(key=lambda x: x.name.lower(), reverse=(sort_dir == "desc"))
        coverage.main_combined.sort(key=lambda x: x.name.lower(), reverse=(sort_dir == "desc"))
        coverage.side_combined.sort(key=lambda x: x.name.lower(), reverse=(sort_dir == "desc"))

    # Check if this is an HTMX request for just the deck boards partial
    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(
            request,
            "_deck_boards.html",
            {
                "cov": coverage,
                "deck": deck,
                "sort_by": sort_by,
                "sort_dir": sort_dir,
                "read_only": get_settings().read_only,
                "fx_rate": fx.rate(currency)
            },
        )

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
            "fx_rate": fx.rate(currency),
            "read_only": get_settings().read_only,
            "share_kind": DECK,
            "share_target": deck.id,
            "share_links": await links_for(session, DECK, deck.id),
            "share_new": await just_minted_token(session, DECK, deck.id, shared),
            "ai_ready": (await get_config(session)).ready,
            "locations": locations,
            "sort_by": sort_by,
            "sort_dir": sort_dir,
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
            "current_finish": dc.finish,
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
    finish: str = Form("normal"),
    proxy: str | None = Form(None),
    special: str | None = Form(None),
    format: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Set the printing/language for a deck line and toggle its proxy/special flags."""
    _guard_writable()
    dc = await _get_deck_card(session, deck_id, card_id)
    old_sid = dc.scryfall_id
    await apply_deck_card_edit(
        session, dc,
        scryfall_id=scryfall_id or None,
        language=language,
        proxy=proxy is not None,
        special=special is not None,
        finish=finish,
    )
    # Mirror a printing change into the collection for an owned deck card (#298).
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    await sync_printing(session, deck, dc, old_sid, dc.quantity)
    url = f"/decks/{deck_id}" + (f"?format={format}" if format in LEGALITY_FORMATS else "")
    # HTMX form post -> refresh the whole deck page (coverage + legality change).
    return Response(status_code=204, headers={"HX-Redirect": url})


@router.post("/decks/{deck_id}/card/{card_id}/quickswap")
async def update_deck_card_quickswap(
    deck_id: int,
    card_id: int,
    session: AsyncSession = Depends(get_session),
) -> Response:
    """If there is no coverage in the collection for the exact printing of card (card_id),
    the quick swap function will modify the card to match the first swappable copy, by oracle id."""
    _guard_writable()
    dc = await _get_deck_card(session, deck_id, card_id)
    await quickswap_deck_card(session, dc)
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@router.post("/decks/{deck_id}/card/{card_id}/qty")
async def change_deck_card_qty(
    deck_id: int, card_id: int, delta: int = Form(0),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Nudge a deck line's quantity by ±1 (#298); mirror it to the collection for an owned card.

    If all quanitites of this card are located in the deck, delta of these cards will be moved to
    unfiled.
    """
    _guard_writable()
    ## Modify deck_card
    dc = await _get_deck_card(session, deck_id, card_id)
    delta = 1 if delta > 0 else -1
    new_qty = max(0, dc.quantity + delta)
    applied = new_qty - dc.quantity  # 0 when already at the floor of 1
    dc.quantity = new_qty
    deck_card_unique_stack_key = dc.get_unique_stack_key()

    ## Modify collection_card if the new quantity is less than what's in the deck
    if(delta < 0 and dc.scryfall_id):
      location = (
          await session.scalars(
              select(Location).where(Location.type == 'deck', Location.foreign_id==deck_id)
          )
      ).first()
      matches = await match_deck_cards_in_deck_collection_cards(
          session, source_cards=[dc], source_location=location.id if location else None
      )
      matching_collection_cards = matches[deck_card_unique_stack_key]

      # Calculate how many cards need to be moved back to unfiled
      current_in_deck = sum([c.quantity for c in matching_collection_cards])
      if(current_in_deck > new_qty):
          # We need to move excess quantity back to unfiled
          quantity_to_move_back = current_in_deck - new_qty

          # Move the maximum possible quantity from deck cards back to unfiled
          # Start with the largest stacks first
          sorted_collection_cards = sorted(
              matching_collection_cards, key=lambda x: x.quantity, reverse=True
          )
          remaining_to_move = quantity_to_move_back

          for collection_card in sorted_collection_cards:
              if remaining_to_move <= 0:
                  break

              move_qty = min(remaining_to_move, collection_card.quantity)
              if move_qty > 0:
                  await move_qty_from_existing_stack(
                      session, collection_card=collection_card, quantity=move_qty,
                      new_location_id=None
                  )
                  remaining_to_move -= move_qty

    ## Remove from deck entirely if the new qty is zero
    if(new_qty==0):
        await session.delete(dc)
        await session.commit()

    await session.commit()
    if applied:
        deck = await session.get(Deck, deck_id)
        if deck is not None:
            await sync_quantity(session, deck, dc, applied)
    return Response(status_code=204, headers={"HX-Redirect": f"/decks/{deck_id}"})


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


@router.get("/decks/{deck_id}/proxies", response_class=HTMLResponse)
async def proxy_sheet(
    request: Request,
    deck_id: int,
    source: str = "proxy",
    lands: int = 0,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Print-optimized proxy sheet (3x3 @ 63x88 mm) of a deck's proxy-flagged, missing, or
    all cards, ready to print and cut for playtesting (#161). Images come from the local
    cache (CDN fallback); a text frame stands in when an image is unavailable."""
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    if source not in PROXY_SOURCES:
        source = "proxy"
    coverage = await deck_coverage(session, deck)
    images = await _deck_images(session, deck)
    cards = [
        {"name": c.name, "image": images.get(c.scryfall_id or "", ""), "special": c.special}
        for c in proxy_cards(coverage, source=source, include_lands=bool(lands))
    ]
    return templates.TemplateResponse(
        request,
        "proxy_sheet.html",
        {
            "deck": deck,
            "cards": cards,
            "source": source,
            "sources": PROXY_SOURCES,
            "lands": bool(lands),
        },
    )


@router.post("/decks/{deck_id}/resync")
async def resync_deck_printings(
    deck_id: int, url: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    """Re-fetch a deck's source and correct its printings + finishes in place.

    ``url`` lets a deck imported before source URLs were recorded be linked to its source, so decks
    imported when printings were ignored can still be repaired.
    """
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    # Validate before anything is stored: the URL is rendered as the deck's "Source" link, so a
    # ``javascript:``/``file:`` value must never reach the row — not even on the way to a 502.
    source = normalize_deck_url(url) if url.strip() else normalize_deck_url(deck.source_url or "")
    if source is None:
        if url.strip() or deck.source_url:
            raise HTTPException(
                status_code=400, detail=f"Unsupported source URL. Supported: {SUPPORTED}.")
        raise HTTPException(status_code=400, detail="Give this deck a source URL to re-sync from.")
    if url.strip():
        # A validated URL is kept even if the fetch below fails (the provider may just be down).
        deck.source_url = source
        await session.commit()
    try:
        _name, decklist = await fetch_deck_from_url(source)
    except DeckImportError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    await resync_printings(session, deck, decklist)
    return RedirectResponse(url=f"/decks/{deck.id}", status_code=303)


@router.post("/decks/{deck_id}/replace-list")
async def replace_deck_list(
    deck_id: int, decklist_text: str = Form(""), backup_before_import: bool = Form(False),
    session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    """Replace the entire deck content with a new list from a textarea. """
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if decklist_text == '':
        raise HTTPException(status_code=400, detail='Empty replace list.')
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)

    from src.decks import (
        _merge_lines,
        _printing_for,
        _resolve_exact,
        _resolve_names,
        parse_decklist,
    )

    parsed = _merge_lines(parse_decklist(decklist_text))
    owned_sids = set(await session.scalars(select(CollectionCard.scryfall_id)))
    resolved = await _resolve_names(session, [p.name for p in parsed], owned_sids)
    exact = await _resolve_exact(session, {(p.set_code, p.collector_number) for p in parsed})

    if len(parsed) == 0:
        raise HTTPException(status_code=400, detail='Empty replace list.')

    # Backup cards (if enabled)
    if(backup_before_import):
        await save_version(
            session, deck,
            "Pre-import backup "+datetime.datetime.now(datetime.UTC).strftime("%Y-%m-%d")
        )

    # Clear existing cards
    for card in deck.cards:
        await session.delete(card)
    await session.flush()

    ## Add new cards
    for p in parsed:
        oracle, sid = _printing_for(p, exact, resolved)
        deck.cards.append(
            DeckCard(name=p.name, quantity=p.quantity, board=p.board,
                     oracle_id=oracle, scryfall_id=sid, finish=p.finish)
        )

    # Manually update the updated_at timestamp

    deck.updated_at = datetime.datetime.now(datetime.UTC)

    await session.commit()
    return RedirectResponse(url=f"/decks/{deck.id}", status_code=303)




@router.post("/decks/{deck_id}/delete")
async def delete_deck(deck_id: int, session: AsyncSession = Depends(get_session)):
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is not None:
        # Withdraw share links first — one outliving its deck would 404, which reads as a broken
        # app rather than as "that isn't shared any more" (#80).
        await revoke_links_for(session, DECK, deck_id)
        await session.delete(deck)
        await session.commit()
    return RedirectResponse(url="/decks", status_code=303)


@router.post("/decks/{deck_id}/sleeve", response_class=HTMLResponse)
async def set_deck_sleeve(
    deck_id: int,
    sleeve_id: int = Form(None),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Associate a sleeve with a deck."""
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)

    # Clear existing sleeves for this deck
    stmt = select(Sleeve).where(Sleeve.deck_id == deck_id)
    result = await session.execute(stmt)
    existing_sleeves = result.scalars().all()
    for sleeve in existing_sleeves:
        sleeve.deck_id = None

    # If sleeve_id is provided, associate it with the deck
    if sleeve_id:
        # Ensure the sleeve exists and belongs to this deck (or has no deck association)
        sleeve = await session.get(Sleeve, sleeve_id)
        if sleeve is None:
            raise HTTPException(status_code=404, detail="Sleeve not found.")

        # Set the sleeve for this deck
        sleeve.deck_id = deck_id

    await session.commit()

    # Redirect back to the deck detail page
    return Response(status_code=200, headers={})



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
    return RedirectResponse(url=f"/decks/{deck.id}", status_code=303)


@router.post("/decks/{deck_id}/versions/{version_id}/delete")
async def delete_deck_version(
    deck_id: int, version_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    version = await session.get(DeckVersion, version_id)
    if version is not None and version.deck_id == deck_id:
        await session.delete(version)
        await session.commit()
    return RedirectResponse(url=f"/decks/{int(deck_id)}", status_code=303)


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


@router.post("/decks/{deck_id}/rename")
async def update_deck_name(
    request: Request, deck_id: int, name: str = Form(""),
    session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    new_deck_name = await rename_deck(session, deck, name)
    # htmx swaps this into the heading with innerHTML, so it is an HTML fragment: escape it.
    return HTMLResponse(html.escape(new_deck_name))


@router.post("/decks/{deck_id}/checkout")
async def deck_checkout(
    request: Request,
    deck_id: int,
    mainboard: bool = Form(False),
    sideboard: bool = Form(False),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Checkout cards from a deck to the collection."""
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    await checkout_deck(session, deck, mainboard=mainboard, sideboard=sideboard)
    return RedirectResponse(url=f"/decks/{deck.id}", status_code=303)


@router.post("/decks/{deck_id}/deconstruct")
async def deck_deconstruct(
    request: Request,
    deck_id: int,
    location_id: int = Form(None),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Deconstruct a deck by moving its cards to a specific location."""
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)

    # Call the deconstruct function with the specified location
    await deconstruct_deck(session, deck, location=location_id)
    return RedirectResponse(url=f"/decks/{deck.id}", status_code=303)


@router.get("/decks/{deck_id}/deconstruct", response_class=HTMLResponse)
async def deconstruct_form(
    request: Request,
    deck_id: int,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Show the deconstruction form for a deck."""
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)

    # Get locations for the location picker
    grouped_locations = await find_or_create_grouped_locations(
        session, binders=True, boxes=True, decks=False
    )

    return templates.TemplateResponse(
        request,
        "deck_deconstruct.html",
        {
            "deck": deck,
            "grouped_locations": grouped_locations,
        },
    )


@router.post("/decks/{deck_id}/update-description", response_class=HTMLResponse)
async def update_deck_description(
    deck_id: int,
    description: str = Form(""),
    session: AsyncSession = Depends(get_session)
) -> str:
    """Update the description for a deck."""
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)
    # Stored as the text the user typed. Escaping belongs to whoever renders it: the page
    # template autoescapes, and the fragment returned here (swapped in with innerHTML) is escaped
    # on the way out. Escaping on write as well showed ``&amp;`` on the next page load.
    deck.description = description or None
    await session.commit()
    return html.escape(deck.description or "")




@router.post("/decks/{deck_id}/set-commander")
async def set_commander(
    deck_id: int,
    card_id: int = Form(...),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Set a card as the commander for a deck."""
    _guard_writable()
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)

    # Get the deck card
    dc = await session.get(DeckCard, card_id)
    if dc is None or dc.deck_id != deck_id:
        raise HTTPException(status_code=404, detail="Card not found in this deck.")

    # Check if this card is a legendary creature by examining its type_line
    card = await session.get(Card, dc.scryfall_id) if dc.scryfall_id else None

    # Check for legendary creature
    if not card or not card.is_legendary_creature() or dc.quantity != 1 :
        raise HTTPException(status_code=400, detail="Only legendary creatures can be commanders.")

    # Set this card as the commander
    deck.commander_id = dc.id
    await session.commit()

    return Response(status_code=200, headers={"HX-Refresh": "true"})


@router.post("/decks/{deck_id}/add-card")
async def add_card_to_deck_endpoint(
    deck_id: int,
    scryfall_id: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> Response:
    """Add a card to the deck by its scryfall_id."""
    _guard_writable()

    # Get the deck
    deck = await session.get(Deck, deck_id)
    if deck is None:
        raise HTTPException(status_code=404, detail=_DECK_NOT_FOUND)

    # Get the card
    card = await session.get(Card, scryfall_id)
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found.")

    # Add the card to the deck
    await add_card_to_deck(session, deck_id, card)

    # Refresh the deck boards partial
    return Response(status_code=200, headers={"HX-Refresh": "true"})


@router.get("/decks/{deck_id}/search-cards")
async def search_cards(
    request: Request,
    deck_id: int,
    q: str = "",
    limit: int = 10,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Search for cards by name to suggest additions to a deck."""
    limit = max(1, min(limit, 50))
    # If no query or empty query, return empty results
    if not q or q.strip() == "":
        return templates.TemplateResponse(
            request, "_deck_search_suggestions.html", {"cards": [], "deck_id": deck_id}
        )

    # Search for card names that match the query (case insensitive)
    # Prioritize matches: exact match > starts with > contains anywhere
    stmt = select(Card.name, Card.scryfall_id).where(
        Card.name.ilike(f"%{q.strip()}%"),
        and_(Card.type_line != "Card", Card.type_line.not_ilike("%token%"))
    ).order_by(
        # Prioritize exact matches first
        Card.name.op('ILIKE')(f"{q.strip()}").desc(),
        # Then prioritize matches that start with the query
        Card.name.op('ILIKE')(f"{q.strip()}%").desc(),
        # Then order by name for consistent sorting
        Card.name
    ).limit(limit * 3)  # Get more results to allow deduplication and ranking

    results = await session.execute(stmt)
    cards = [{"name": row[0], "scryfall_id": str(row[1])} for row in results]

    # Deduplicate by name, choosing the best version based on collection availability
    deduplicated_cards = []
    seen_names = set()

    # First, let's group all cards by name to get all versions of each card
    cards_by_name = {}
    for card in cards:
        name = card["name"]
        if name not in cards_by_name:
            cards_by_name[name] = []
        cards_by_name[name].append(card)

    # For each unique name, select the best version according to our rules
    for name, matching_cards in cards_by_name.items():
        if name in seen_names:
            continue

        # Select the best version based on quantity in collection
        best_card = await _select_best_card_version(session, matching_cards)

        deduplicated_cards.append(best_card)
        seen_names.add(name)

        # Limit to requested limit
        if len(deduplicated_cards) >= limit:
            break

    # Add coverage information for each card
    cards_with_coverage = []
    for card in deduplicated_cards:
        # Get the card details for coverage calculation
        card_details = await session.execute(
            select(Card).where(Card.scryfall_id == card["scryfall_id"])
        )
        card_obj = card_details.scalars().first()

        if card_obj:
            # Calculate coverage - we need to get deck coverage info for this specific card
            # This is a simplified version that gets the basic coverage data
            coverage_info = await _get_card_coverage(session, deck_id, card_obj)
            card["coverage"] = coverage_info
            cards_with_coverage.append(card)

    return templates.TemplateResponse(
        request, "_deck_search_suggestions.html",
        {"cards": cards_with_coverage, "deck_id": deck_id, "q": html.escape(q)}
    )


async def _select_best_card_version(session: AsyncSession, matching_cards: list[dict]) -> dict:
    """Select the best card version based on quantity in collection.

    Rules:
    1. Choose first item with > 0 quantity in non-deck location
    2. If no matches in non-deck locations, choose first item with > 0 quantity in deck location
    3. If no matches anywhere, pick the first card
    """
    # Get all unique scryfall_ids for these matching cards
    scryfall_ids = [c["scryfall_id"] for c in matching_cards]

    # First, check collection cards that are NOT in a deck location (first preference)
    stmt = select(CollectionCard).where(
        CollectionCard.scryfall_id.in_(scryfall_ids)
    ).outerjoin(Location, CollectionCard.location == Location.id).where(
        (Location.type != "deck") | (Location.id.is_(None))
    )

    collection_cards_non_deck = (await session.execute(stmt)).scalars().all()

    # Group by scryfall_id for easy lookup
    collection_by_sid_non_deck = {}
    for cc in collection_cards_non_deck:
        if str(cc.scryfall_id) not in collection_by_sid_non_deck:
            collection_by_sid_non_deck[str(cc.scryfall_id)] = []
        collection_by_sid_non_deck[str(cc.scryfall_id)].append(cc)

    # Try to find a card with quantity > 0 in non-deck location
    for card in matching_cards:
        scryfall_id = card["scryfall_id"]
        if scryfall_id in collection_by_sid_non_deck and any(
            cc.quantity > 0 for cc in collection_by_sid_non_deck[scryfall_id]
        ):
            return card

    # If no matches in non-deck locations, check deck locations (second preference)
    stmt = select(CollectionCard).where(
        CollectionCard.scryfall_id.in_(scryfall_ids)
    ).join(Location, CollectionCard.location == Location.id).where(
        Location.type == "deck"
    )

    collection_cards_deck = (await session.execute(stmt)).scalars().all()

    # Group by scryfall_id for easy lookup
    collection_by_sid_deck = {}
    for cc in collection_cards_deck:
        if str(cc.scryfall_id) not in collection_by_sid_deck:
            collection_by_sid_deck[str(cc.scryfall_id)] = []
        collection_by_sid_deck[str(cc.scryfall_id)].append(cc)

    # Try to find a card with quantity > 0 in deck location
    for card in matching_cards:
        scryfall_id = card["scryfall_id"]
        if scryfall_id in collection_by_sid_deck and any(
            cc.quantity > 0 for cc in collection_by_sid_deck[scryfall_id]
        ):
            return card

    # If no matches anywhere, just pick the first one
    return matching_cards[0]


async def _get_card_coverage(session: AsyncSession, deck_id: int, card: Card) -> dict:
    """Calculate coverage information for a card in the context of a specific deck."""
    # This is simplified - in reality, this would involve more complex logic
    # to determine how many copies are in deck vs. available elsewhere

    # Get all collection cards for this scryfall_id that are NOT in a deck location
    stmt = select(CollectionCard).where(
        CollectionCard.scryfall_id == card.scryfall_id
    ).outerjoin(Location, CollectionCard.location == Location.id).where(
        (Location.type != "deck") | (Location.id.is_(None))
    )

    collection_cards_non_deck = (await session.execute(stmt)).scalars().all()

    # Get deck cards for this scryfall_id
    stmt = select(DeckCard).where(
        DeckCard.scryfall_id == card.scryfall_id,
        DeckCard.deck_id == deck_id
    )

    deck_cards = (await session.execute(stmt)).scalars().all()

    # Calculate quantities
    in_deck = sum(dc.quantity for dc in deck_cards)
    available_non_deck = sum(cc.quantity for cc in collection_cards_non_deck)
    swappable = sum(cc.quantity for cc in collection_cards_non_deck)  # Simplified for now

    return {
        "in_deck": in_deck,
        "available": available_non_deck,
        "swappable": swappable
    }
