"""Manual collection editing routes.

Card-page editing (add a stack, nudge quantity, delete) returns the ``_card_collection`` partial so
HTMX can swap just that block. Bulk actions operate on printings selected in the results grid and
redirect back to the same search.
"""

from __future__ import annotations
from typing import Optional

import uuid
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.box_service import (
    create_box,
    delete_box,
)
from src.card import _image
from src.collection_edit import (
    StaleStackError,
    _CONDITION_CODES,
    add_or_increment,
    adjust_quantity,
    bulk_add_to_collection,
    delete_stack,
    move_stack_location,
    move_stacks_location,
    organize_by_color_identity,
    owned_for_oracle,
    remove_copies,
    pull_stack_from_existing_stack,
    scryfall_ids_to_stack_ids,
    _STACK_NOT_FOUND,
)
from src.config import get_settings
from src.decks import DECK_LANGUAGES
from src.db import get_session
from src.grading import clear_grade, safe_photo_path, save_grade_photo, set_grade
from src.locations import find_or_create_grouped_locations
from src.models import Card, CollectionCard
from src.routes.validators import ValidCondition, ValidFinish, ValidLanguage
from src.tags import modify_stacks_tag
from src.templating import templates
from src.trade_pool import open_pools, stage_selection, stage_stack

router = APIRouter(tags=["collection"])



def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")



async def _collection_partial_generator(
    request: Request, session: AsyncSession, scryfall_id: uuid.UUID, conflict: bool = False
) -> dict:
    card = await session.get(Card, scryfall_id)
    if(card is None): raise HTTPException(status_code=404, detail="Card not found.")
    owned = list(
        (
            await session.execute(
                select(CollectionCard)
                .where(CollectionCard.scryfall_id == card.scryfall_id)
                .order_by(CollectionCard.finish, CollectionCard.language)
            )
        )
        .scalars()
        .all()
    )

    printings: list[Card] = []
    owned_other_printing: list[CollectionCard] = []
    owned_equivalent_oracle_id: list[CollectionCard] = []
    if card.oracle_id is not None:
        printings = list(
            (
                await session.execute(
                    select(Card)
                    .where(Card.oracle_id == card.oracle_id)
                    .order_by(Card.released_at.desc().nulls_last())
                    .limit(24)
                )
            )
            .scalars()
            .all()
        )

        owned_other_printing = list(
            (
                await session.execute(
                    select(CollectionCard)
                    .where(CollectionCard.scryfall_id != card.scryfall_id)
                    .where(CollectionCard.scryfall_id.in_([x.scryfall_id for x in printings]))
                    .order_by(CollectionCard.finish, CollectionCard.language)
                )
            )
            .scalars()
            .all()
        )

        owned_equivalent_oracle_id = list(
            (
                await session.execute(
                    select(CollectionCard)
                    .join(Card, CollectionCard.scryfall_id==Card.scryfall_id)
                    .where(CollectionCard.scryfall_id != card.scryfall_id)
                    .where(Card.oracle_id == card.oracle_id)
                    .order_by(CollectionCard.finish, CollectionCard.language)
                )
            )
            .scalars()
            .all()
        )

        




    return {
            "card":card,
            "owned": owned,
            "owned_total": sum(s.quantity for s in owned),
            "owned_foil": any((s.finish or "").lower() == "foil" for s in owned),
            "owned_etched": any((s.finish or "").lower() == "etched" for s in owned),
            "owned_other_printing": owned_other_printing,
            "owned_other_printing_total": sum(s.quantity for s in owned_other_printing),
            "owned_equivalent_oracle_id": owned_equivalent_oracle_id,
            "owned_equivalent_oracle_id_total": sum(s.quantity for s in owned_equivalent_oracle_id),
            "read_only": get_settings().read_only,
            "printings": [(p, _image(p, "small")) for p in printings],
            "conflict": conflict,
            "grouped_locations": await find_or_create_grouped_locations(session),
            "trade_pools": await open_pools(session),
            "languages": DECK_LANGUAGES,
            "condition_codes": _CONDITION_CODES,
        }


async def _collection_partial(
    request: Request, session: AsyncSession, scryfall_id: uuid.UUID, conflict: bool = False
) -> HTMLResponse:
    

    return templates.TemplateResponse(
        request,
        "_card_collection.html",
        (await _collection_partial_generator(request, session, scryfall_id)),
    )






@router.post("/collection/add", response_class=HTMLResponse)
async def add(
    request: Request,
    scryfall_id: str = Form(...),
    quantity: int = Form(1),
    finish: str = Form("normal"),
    condition: str = Form(""),
    language: str = Form("en"),
    location: Optional[int] = Form(None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    _guard_writable()
    sid = uuid.UUID(scryfall_id)
    await add_or_increment(session, sid, quantity, finish=finish, condition=condition,
                           language=language, location=location)
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
        raise HTTPException(status_code=404, detail=_STACK_NOT_FOUND)
    return await _collection_partial(request, session, sid)


def _expected(raw: str) -> int | None:
    """The version a form was rendered with; absent or unparseable means "don't guard".

    An old page that predates this feature simply behaves as it always did, rather than becoming
    unusable — the guard is a safety net, not a gate.
    """
    raw = (raw or "").strip()
    return int(raw) if raw.isdigit() else None


async def _conflict(
    request: Request, session: AsyncSession, scryfall_id: uuid.UUID
) -> HTMLResponse:
    """Re-render the card's stacks with a "this changed" notice, at HTTP 409.

    The current values come back with it: the editor needs to see what it is *now* to decide
    whether to re-apply, so failing without them would cost two edits instead of one.
    """
    resp = await _collection_partial(request, session, scryfall_id, conflict=True)
    resp.status_code = 409
    return resp


@router.post("/collection/stack/{stack_id}/delete", response_class=HTMLResponse)
async def remove_stack(
    request: Request,
    stack_id: int,
    version: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Delete a stack, refusing if it changed since the page was rendered (#207)."""
    _guard_writable()
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        raise HTTPException(status_code=404, detail=_STACK_NOT_FOUND)
    card_sid = stack.scryfall_id
    try:
        await delete_stack(session, stack_id, expected_version=_expected(version))
    except StaleStackError:
        return await _conflict(request, session, card_sid)
    return await _collection_partial(request, session, card_sid)


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


@router.post("/collection/stack/{stack_id}/edit", response_class=HTMLResponse)
async def locate_collection_stack(
    request: Request,
    stack_id: int,
    printing: str = Form(""),
    finish: ValidFinish = Form(""),
    condition: ValidCondition = Form(""),
    language: ValidLanguage = Form(""),
    location_moved: bool = Form(False), #location_id = None is a valid edited location
    location_id: int = Form(None),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Unified location picker (#160): file a stack into a box, binder, or deck. """
    _guard_writable()
    stack = await session.get(CollectionCard, stack_id)
    if stack is None: raise HTTPException(status_code=404, detail=_STACK_NOT_FOUND)
    scryfall_id = stack.scryfall_id
    if(stack and condition):
        stack.condition = condition
    if(stack and language):
        stack.language = language
    if(stack and finish):
        stack.finish = finish
    if(stack and printing):
        stack.scryfall_id = uuid.UUID(printing)
    await session.commit()
    if(location_moved):
      new_stack_id = await move_stack_location(session, stack_id, location_id)
      if(not new_stack_id): raise HTTPException(status_code=404, detail=_STACK_NOT_FOUND)
    return await _collection_partial(request, session, scryfall_id)


@router.post("/collection/stack/{stack_id}/trade", response_class=HTMLResponse)
async def stage_stack_for_trade(
    request: Request,
    stack_id: int,
    pool_id: str = Form(""),
    quantity: int = Form(1),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Stage this exact copy into a trade pool (#331). The collection is not changed — staging is
    a proposal, and only the commit in #332 moves anything.

    An empty ``pool_id`` is the picker's own placeholder and is a no-op, not an error.
    """
    _guard_writable()
    stack = await session.get(CollectionCard, stack_id)
    if stack is None: raise HTTPException(status_code=404, detail=_STACK_NOT_FOUND)
    if pool_id.strip().isdigit():
        await stage_stack(session, int(pool_id), stack_id, quantity)
    return await _collection_partial(request, session, stack.scryfall_id)


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
        raise HTTPException(status_code=404, detail=_STACK_NOT_FOUND)
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
        raise HTTPException(status_code=404, detail=_STACK_NOT_FOUND)
    sid = stack.scryfall_id
    await clear_grade(session, stack_id)
    return await _collection_partial(request, session, sid)


@router.post("/collection/stack/{stack_id}/pull", response_class=HTMLResponse)
async def pull_stack(
    request: Request,
    stack_id: int,
    version: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Pull one card from an existing stack into a new stack with all the same attributes."""
    _guard_writable()
    stack = await session.get(CollectionCard, stack_id)
    if stack is None:
        raise HTTPException(status_code=404, detail=_STACK_NOT_FOUND)
    
    try:
        # Call the pull function from collection_edit
        card_sid = await pull_stack_from_existing_stack(session, stack_id, expected_version=_expected(version))
    except StaleStackError:
        return await _conflict(request, session, stack.scryfall_id)
    
    if card_sid is None:
        raise HTTPException(status_code=404, detail="Failed to pull stack.")
        
    return await _collection_partial(request, session, card_sid)


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
    stack_ids: list[int] = Form(default=[]),
    tag: str = Form(""),
    bulk_location_id: str = Form(""),
    pool_id: str = Form(""),
    q: str = Form(""),
    scope: str = Form("collection"),
    sort: str = Form("name"),
    dir: str = Form("asc"),
    refresh: bool = Form(False),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    _guard_writable()
    ## When scryfall_ids are passed, convert these to stack ids first. The difference betwee a scryfall_id and a stack ID is that scryfall_id can match multiple stacks with the same scryfall_id but other different prooperties, such as finish, location, etc.
    if scryfall_ids:
        if bulk_action == "add":
            await bulk_add_to_collection(session, scryfall_ids, 1)
        stack_ids += (await scryfall_ids_to_stack_ids(session, scryfall_ids))
    if stack_ids:
        if bulk_action == "tag" and tag.strip():
            await modify_stacks_tag(session, stack_ids=stack_ids, tag=tag)
        elif bulk_action == "tag-remove" and tag.strip():
            await modify_stacks_tag(session, stack_ids=stack_ids, tag=tag, remove=True)
        elif (bulk_action == "location") and bulk_location_id:
            await move_stacks_location(session, stack_ids=stack_ids, location_id=int(bulk_location_id))
        elif bulk_action in ("trade_out", "trade_in") and pool_id.strip().isdigit():
            side = "out" if bulk_action == "trade_out" else "in"
            await stage_selection(session, int(pool_id), side, stack_ids=stack_ids)
    if(refresh): return Response(status_code=200, headers={"HX-Refresh": "true"})
    params = urlencode({"q": q, "scope": scope, "sort": sort, "dir": dir})
    return RedirectResponse(url=f"/search?{params}", status_code=303)



@router.post("/collection/boxes/new")
async def new_box(
    name: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await create_box(session, name)
    return RedirectResponse(url='/collection/locations', status_code=303)


@router.post("/collection/boxes/{box_id}/delete")
async def delete_box_route(
    box_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await delete_box(session, box_id)
    return RedirectResponse(url='/collection/locations', status_code=303)


@router.post("/collection/organize-by-identity")
async def organize_locations(
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    _guard_writable()
    await organize_by_color_identity(session)
    return RedirectResponse(url='/collection/locations', status_code=303)
