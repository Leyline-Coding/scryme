"""Trade / surplus binder routes: view your spares, export a sharable list."""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import get_currency, info
from src.db import get_session
from src.templating import templates
from src.trade import trade_binder

router = APIRouter(tags=["trade"])


@router.get("/trade", response_class=HTMLResponse)
async def trade_page(
    request: Request, keep: int = 1, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    currency = get_currency(request)
    binder = await trade_binder(session, currency, keep=keep)
    return templates.TemplateResponse(
        request, "trade.html", {"binder": binder, "cur": info(currency)}
    )


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
