"""Trade routes: the surplus binder (export a sharable list) and trade pools (#331).

The binder is a *view* of what you could trade; a pool is a *staged* trade with a named partner,
two sides, and its own frozen valuation basis. Pool pages are plain forms + redirects — a trade is
edited deliberately, one card at a time, and a full re-render keeps the two running totals honest
after every change.
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.currency import get_currency, info
from src.db import get_session
from src.pricing import get_price_source
from src.routes._safe import local_redirect
from src.templating import templates
from src.trade import trade_binder
from src.trade_pool import (
    clear_pool,
    create_pool,
    delete_pool,
    get_pool,
    pool_view,
    remove_item,
    stage_printing,
    update_item,
    update_pool,
)

router = APIRouter(tags=["trade"])

_TRADE_TAB = "/collection?tab=trade"
_POOL_NOT_FOUND = "Trade pool not found."


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.get("/trade")
async def trade_page(keep: int = 1) -> RedirectResponse:
    # The trade binder is now the Trade tab of /collection.
    return local_redirect(f"{_TRADE_TAB}&keep={keep}", status_code=307)


@router.get("/trade/export")
async def trade_export(
    fmt: str = "txt", keep: int = 1, session: AsyncSession = Depends(get_session)
):
    binder = await trade_binder(session, "usd", keep=keep)
    if fmt == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["Quantity", "Name", "Set", "Collector number", "Rarity", "USD each"])
        for c in binder.cards:
            writer.writerow([c.tradeable, c.name, c.set_code.upper(), c.collector_number,
                             c.rarity or "", f"{c.unit:.2f}"])
        return StreamingResponse(
            iter([buf.getvalue()]),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="scryme-trade.csv"'},
        )
    lines = [f"{c.tradeable} {c.name} ({c.set_code.upper()}) {c.collector_number}"
             for c in binder.cards]
    return PlainTextResponse(
        "\n".join(lines) + ("\n" if lines else ""),
        headers={"Content-Disposition": 'attachment; filename="scryme-trade.txt"'},
    )


# --- trade pools (#331) --------------------------------------------------------------------------

def _pool_url(pool_id: int) -> RedirectResponse:
    return local_redirect(f"/trade/pool/{pool_id}")


@router.post("/trade/pools")
async def new_pool(
    request: Request,
    name: str = Form(""),
    partner: str = Form(""),
    note: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Open a pool on the viewer's current currency / price source — see ADR 0002 D2."""
    _guard_writable()
    pool = await create_pool(
        session, name, partner=partner, note=note,
        currency=get_currency(request), price_source=get_price_source(request),
    )
    return _pool_url(pool.id)


@router.get("/trade/pool/{pool_id}", response_class=HTMLResponse)
async def view_pool(
    request: Request, pool_id: int, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    pool = await get_pool(session, pool_id)
    if pool is None:
        raise HTTPException(status_code=404, detail=_POOL_NOT_FOUND)
    return templates.TemplateResponse(
        request,
        "trade_pool.html",
        {
            "view": await pool_view(session, pool),
            "cur": info(pool.currency),
            "read_only": get_settings().read_only,
        },
    )


@router.post("/trade/pool/{pool_id}/update")
async def edit_pool(
    pool_id: int,
    name: str = Form(""),
    partner: str = Form(""),
    note: str = Form(""),
    status: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    _guard_writable()
    if await update_pool(session, pool_id, name=name, partner=partner, note=note,
                         status=status or None) is None:
        raise HTTPException(status_code=404, detail=_POOL_NOT_FOUND)
    return _pool_url(pool_id)


@router.post("/trade/pool/{pool_id}/delete")
async def remove_pool(
    pool_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    """Discard the whole pool. Nothing staged has touched the collection, so nothing is undone."""
    _guard_writable()
    await delete_pool(session, pool_id)
    return local_redirect(_TRADE_TAB)


@router.post("/trade/pool/{pool_id}/clear")
async def clear(
    pool_id: int, direction: str = Form(""), session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await clear_pool(session, pool_id, direction or None)
    return _pool_url(pool_id)


@router.post("/trade/pool/{pool_id}/stage")
async def stage(
    pool_id: int,
    scryfall_id: str = Form(...),
    direction: str = Form("in"),
    quantity: int = Form(1),
    finish: str = Form("normal"),
    condition: str = Form(""),
    language: str = Form("en"),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Stage a printing by hand — the incoming side, where there's no owned stack to point at."""
    _guard_writable()
    await stage_printing(session, pool_id, direction, scryfall_id, quantity,
                         finish=finish, condition=condition, language=language)
    return _pool_url(pool_id)


@router.post("/trade/pool/{pool_id}/items/{item_id}")
async def edit_item(
    pool_id: int,
    item_id: int,
    quantity: int = Form(...),
    finish: str = Form("normal"),
    condition: str = Form(""),
    language: str = Form("en"),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Adjust one staged copy. Quantity 0 unstages it, so the stepper needs no separate delete."""
    _guard_writable()
    await update_item(session, item_id, quantity=quantity, finish=finish,
                      condition=condition, language=language)
    return _pool_url(pool_id)


@router.post("/trade/pool/{pool_id}/items/{item_id}/delete")
async def unstage(
    pool_id: int, item_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await remove_item(session, item_id)
    return _pool_url(pool_id)
