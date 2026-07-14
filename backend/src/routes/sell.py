"""Sell list + valuation report routes (#97)."""

from __future__ import annotations

import csv
import datetime
import io

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, RedirectResponse, StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import get_currency, info
from src.db import get_session
from src.sell import sell_list
from src.templating import templates
from src.valuation import valuation_report

router = APIRouter(tags=["sell"])


@router.get("/sell")
async def sell_page() -> RedirectResponse:
    # The sell list is the Sell tab of /collection.
    return RedirectResponse(url="/collection?tab=sell", status_code=307)


@router.get("/sell/export")
async def sell_export(fmt: str = "csv", session: AsyncSession = Depends(get_session)):
    sl = await sell_list(session, "usd")
    if fmt == "txt":
        lines = [f"{c.quantity} {c.name} ({c.set_code.upper()}) {c.collector_number}"
                 for c in sl.cards]
        return PlainTextResponse(
            "\n".join(lines) + ("\n" if lines else ""),
            headers={"Content-Disposition": 'attachment; filename="scryme-sell.txt"'},
        )
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["Quantity", "Name", "Set", "Collector number", "Rarity",
                     "USD each", "USD total"])
    for c in sl.cards:
        writer.writerow([c.quantity, c.name, c.set_code.upper(), c.collector_number,
                         c.rarity or "", f"{c.unit:.2f}", f"{c.value:.2f}"])
    writer.writerow([sl.total_cards, "TOTAL", "", "", "", "", f"{sl.total_value:.2f}"])
    return StreamingResponse(
        iter([buf.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="scryme-sell.csv"'},
    )


@router.get("/valuation", response_class=HTMLResponse)
async def valuation(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    currency = get_currency(request)
    return templates.TemplateResponse(
        request, "valuation.html",
        {"report": await valuation_report(session, currency),
         "cur": info(currency),
         "today": datetime.date.today().isoformat()},
    )
