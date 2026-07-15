"""Manual collection editing routes.

Card-page editing (add a stack, nudge quantity, delete) returns the ``_card_collection`` partial so
HTMX can swap just that block. Bulk actions operate on printings selected in the results grid and
redirect back to the same search.
"""

from __future__ import annotations

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.binder_service import add_card as binder_add_card
from src.binder_service import all_binders, bulk_add_to_binder
from src.box_service import (
    all_boxes,
    create_box,
    delete_box,
    rename_box,
)
from src.collection_edit import (
    add_or_increment,
    adjust_quantity,
    bulk_add_tag,
    bulk_add_to_collection,
    delete_stack,
    edit_stack,
    organize_by_color_identity,
    owned_for_oracle,
    remove_copies,
    update_stack,
)
from src.config import get_settings
from src.db import get_session
from src.decks import add_card_to_deck
from src.grading import clear_grade, safe_photo_path, save_grade_photo, set_grade
from src.models import Card, CollectionCard, Deck
from src.tags import card_tags
from src.templating import templates

router = APIRouter(tags=["collection"])


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


async def _collection_partial(
    request: Request, session: AsyncSession, scryfall_id: uuid.UUID
) -> HTMLResponse:
    owned = list(
        (
            await session.execute(
                select(CollectionCard)
                .where(CollectionCard.scryfall_id == scryfall_id)
                .order_by(CollectionCard.finish, CollectionCard.language,
                          CollectionCard.binder_name)
            )
        )
        .scalars()
        .all()
    )
    return templates.TemplateResponse(
        request,
        "_card_collection.html",
        {
            "card_id": scryfall_id,
            "owned": owned,
            "owned_total": sum(s.quantity for s in owned),
            "tags": await card_tags(session, scryfall_id),
            "read_only": get_settings().read_only,
            "printing_opts": await printing_options(session, scryfall_id),
            **await location_choices(session),
        },
    )


async def location_choices(session: AsyncSession) -> dict:
    """Boxes / binders / decks offered by the unified location picker (#160)."""
    decks = (await session.execute(select(Deck).order_by(Deck.name))).scalars().all()
    return {
        "boxes": await all_boxes(session),
        "picker_binders": await all_binders(session),
        "decks": [(d.id, d.name) for d in decks],
    }


async def printing_options(session: AsyncSession, scryfall_id) -> list[tuple[str, str]]:
    """(scryfall_id, "SET·NUM") for every printing of this card's oracle — the printing picker."""
    sid = scryfall_id if isinstance(scryfall_id, uuid.UUID) else uuid.UUID(str(scryfall_id))
    card = await session.get(Card, sid)
    if card is None:
        return []
    others: list = []
    if card.oracle_id is not None:
        others = (await session.execute(
            select(Card).where(Card.oracle_id == card.oracle_id, Card.scryfall_id != sid)
            .order_by(Card.released_at.desc().nulls_last())
        )).scalars().all()
    opts = []
    for p in [card, *others]:
        opts.append((str(p.scryfall_id), f"{p.set_code.upper()}·{p.collector_number}"))
    return opts


@router.post("/collection/add", response_class=HTMLResponse)
async def add(
    request: Request,
    scryfall_id: str = Form(...),
    quantity: int = Form(1),
    finish: str = Form("normal"),
    condition: str = Form(""),
    language: str = Form("en"),
    binder: str = Form(""),
    location: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    sid = uuid.UUID(scryfall_id)
    await add_or_increment(session, sid, quantity, finish=finish, condition=condition,
                           language=language, binder=binder, location=location)
    return await _collection_partial(request, session, sid)


@router.post("/collection/stack/{stack_id}/adjust", response_class=HTMLResponse)
async def adjust(
    request: Request,
    stack_id: int,
    delta: int = Form(...),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    sid = await adjust_quantity(session, stack_id, delta)
    if sid is None:
        raise HTTPException(status_code=404, detail="Stack not found.")
    return await _collection_partial(request, session, sid)


@router.post("/collection/stack/{stack_id}/delete", response_class=HTMLResponse)
async def remove_stack(
    request: Request,
    stack_id: int,
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    sid = await delete_stack(session, stack_id)
    if sid is None:
        raise HTTPException(status_code=404, detail="Stack not found.")
    return await _collection_partial(request, session, sid)


@router.post("/collection/stack/{stack_id}/edit", response_class=HTMLResponse)
async def edit_stack_route(
    request: Request,
    stack_id: int,
    card_id: str = Form(...),
    quantity: str = Form(""),
    finish: str = Form(""),
    printing: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Edit a stack's quantity / finish / printing (#collection-edit)."""
    _guard_writable()
    qty: int | None = None
    if quantity.strip():
        try:
            qty = int(quantity)
        except ValueError:
            qty = None
    await edit_stack(session, stack_id, quantity=qty,
                     finish=finish.strip() or None, scryfall_id=printing.strip() or None)
    return await _collection_partial(request, session, uuid.UUID(card_id))


@router.get("/card/{scryfall_id}/remove-modal", response_class=HTMLResponse)
async def remove_modal(
    request: Request, scryfall_id: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """The remove-from-collection modal: pick how many copies (and which printings) to remove."""
    card = await session.get(Card, uuid.UUID(scryfall_id))
    if card is None:
        raise HTTPException(status_code=404, detail="Card not found.")
    if card.oracle_id is not None:
        stacks = await owned_for_oracle(session, card.oracle_id)
    else:
        owned = (await session.execute(
            select(CollectionCard).where(CollectionCard.scryfall_id == card.scryfall_id)
        )).scalars().all()
        stacks = [(cc, card) for cc in owned]
    return templates.TemplateResponse(
        request, "_remove_modal.html",
        {"card_id": scryfall_id, "name": card.name, "stacks": stacks,
         "read_only": get_settings().read_only},
    )


@router.post("/collection/remove", response_class=HTMLResponse)
async def remove_route(
    request: Request,
    card_id: str = Form(...),
    stack_id: list[int] = Form(default=[]),
    count: list[int] = Form(default=[]),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    await remove_copies(session, list(zip(stack_id, count, strict=False)))
    return await _collection_partial(request, session, uuid.UUID(card_id))


@router.post("/collection/stack/{stack_id}/locate", response_class=HTMLResponse)
async def locate_stack(
    request: Request,
    stack_id: int,
    location_choice: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Unified location picker (#160): file a stack into a box, binder, or deck.

    ``location_choice`` is ``box:<name>`` / ``binder:<id>`` / ``deck:<id>`` / ``""`` (unfile).
    Boxes set the stack's physical ``location``; binders/decks add the printing to that container.
    """
    _guard_writable()
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        raise HTTPException(status_code=404, detail="Stack not found.")
    sid = stack.scryfall_id
    kind, _, ref = location_choice.partition(":")
    if kind == "box":
        await update_stack(session, stack_id, location=ref or None)
    elif kind == "binder" and ref.isdigit():
        await binder_add_card(session, int(ref), sid)
    elif kind == "deck" and ref.isdigit():
        card = await session.get(Card, sid)
        if card is not None:
            await add_card_to_deck(session, int(ref), card)
    else:  # "" / "none" → unfile from any box
        await update_stack(session, stack_id, location=None)
    return await _collection_partial(request, session, sid)


@router.post("/collection/stack/{stack_id}/grade", response_class=HTMLResponse)
async def grade_stack(
    request: Request,
    stack_id: int,
    company: str = Form(""),
    grade: str = Form(""),
    cert: str = Form(""),
    value_override: str = Form(""),
    photo: UploadFile | None = File(None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Set graded-card metadata (+ optional photo) on a stack (#179)."""
    _guard_writable()
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        raise HTTPException(status_code=404, detail="Stack not found.")
    try:
        override = float(value_override) if value_override.strip() else None
    except ValueError:
        override = None
    await set_grade(session, stack_id, company=company, grade=grade, cert=cert,
                    value_override=override)
    if photo is not None and photo.filename:
        await save_grade_photo(session, stack_id, photo)
    return await _collection_partial(request, session, stack.scryfall_id)


@router.post("/collection/stack/{stack_id}/grade/clear", response_class=HTMLResponse)
async def grade_clear(
    request: Request, stack_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    _guard_writable()
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        raise HTTPException(status_code=404, detail="Stack not found.")
    sid = stack.scryfall_id
    await clear_grade(session, stack_id)
    return await _collection_partial(request, session, sid)


@router.get("/grades/{filename}")
async def grade_photo(filename: str) -> FileResponse:
    path = safe_photo_path(filename)
    if path is None:
        raise HTTPException(status_code=404, detail="Photo not found.")
    return FileResponse(path)


@router.post("/collection/bulk")
async def bulk(
    bulk_action: str = Form(...),
    scryfall_ids: list[str] = Form(default=[]),
    tag: str = Form(""),
    binder_id: str = Form(""),
    q: str = Form(""),
    scope: str = Form("collection"),
    sort: str = Form("name"),
    dir: str = Form("asc"),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    _guard_writable()
    if scryfall_ids:
        if bulk_action == "tag" and tag.strip():
            await bulk_add_tag(session, scryfall_ids, tag)
        elif bulk_action == "binder" and binder_id.strip():
            await bulk_add_to_binder(session, int(binder_id), scryfall_ids)
        elif bulk_action == "add":
            await bulk_add_to_collection(session, scryfall_ids, 1)
    params = urlencode({"q": q, "scope": scope, "sort": sort, "dir": dir})
    return RedirectResponse(url=f"/search?{params}", status_code=303)


@router.get("/collection/locations")
async def locations() -> RedirectResponse:
    # The storage hub is now the collection Locations tab.
    return RedirectResponse(url="/collection?tab=locations", status_code=307)


@router.post("/collection/boxes/new")
async def new_box(
    name: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await create_box(session, name)
    return RedirectResponse(url="/collection?tab=locations", status_code=303)


@router.post("/collection/boxes/{box_id}/rename")
async def rename_box_route(
    box_id: int, name: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await rename_box(session, box_id, name)
    return RedirectResponse(url="/collection?tab=locations", status_code=303)


@router.post("/collection/boxes/{box_id}/delete")
async def delete_box_route(
    box_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await delete_box(session, box_id)
    return RedirectResponse(url="/collection?tab=locations", status_code=303)


@router.post("/collection/organize-by-identity")
async def organize_locations(
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    _guard_writable()
    await organize_by_color_identity(session)
    return RedirectResponse(url="/collection?tab=locations", status_code=303)
