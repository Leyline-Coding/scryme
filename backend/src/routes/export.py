"""Export the current search results.

Reuses the same query the UI runs (q + scope + sort), so a download reflects exactly what's on
screen. Three formats:

- ``csv``     — a generic one-row-per-card sheet (with the owned quantity and USD price).
- ``txt``     — a plain decklist (``Nx Name (SET) CN``).
- ``manabox`` — the owned stacks matching the search, in ManaBox's CSV layout, so the file
  round-trips back through the importer.

Rows are capped (MAX_EXPORT) to bound memory; ``load_only`` keeps the heavy ``raw`` JSONB out of
the card-export queries.
"""

from __future__ import annotations

import csv
import io

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from src.db import get_session
from src.models import Card, CollectionCard
from src.search import SearchError, SearchScope
from src.search.engine import DEFAULT_SORT, SORT_KEYS, _owned_quantities, build_search

router = APIRouter(tags=["export"])

MAX_EXPORT = 100_000

FORMATS = {
    "csv": ("scryme-export.csv", "text/csv"),
    "txt": ("scryme-decklist.txt", "text/plain"),
    "manabox": ("scryme-manabox.csv", "text/csv"),
}

# Columns ManaBox emits; the importer detects the format by "Scryfall ID" + "ManaBox ID".
MANABOX_HEADER = [
    "Binder Name", "Binder Type", "Name", "Set code", "Set name", "Collector number", "Foil",
    "Rarity", "Quantity", "ManaBox ID", "Scryfall ID", "Purchase price", "Misprint", "Altered",
    "Condition", "Language", "Purchase price currency", "Added",
]

_CARD_EXPORT_COLS = (
    Card.scryfall_id, Card.name, Card.set_code, Card.set_name, Card.collector_number,
    Card.rarity, Card.mana_cost, Card.cmc, Card.type_line, Card.prices,
)


def _row_flusher():
    buf = io.StringIO()
    writer = csv.writer(buf)

    def flush() -> str:
        value = buf.getvalue()
        buf.seek(0)
        buf.truncate(0)
        return value

    return writer, flush


def _csv_stream(cards, qmap):
    writer, flush = _row_flusher()
    writer.writerow(
        ["Name", "Set name", "Set code", "Collector number", "Rarity", "Mana cost", "CMC",
         "Type", "Quantity", "USD"]
    )
    yield flush()
    for c in cards:
        writer.writerow([
            c.name, c.set_name or "", c.set_code.upper(), c.collector_number, c.rarity or "",
            c.mana_cost or "", "" if c.cmc is None else c.cmc, c.type_line or "",
            qmap.get(str(c.scryfall_id), 0), (c.prices or {}).get("usd", "") or "",
        ])
        yield flush()


def _txt_stream(cards, qmap):
    for c in cards:
        qty = qmap.get(str(c.scryfall_id), 0) or 1
        yield f"{qty}x {c.name} ({c.set_code.upper()}) {c.collector_number}\n"


def _manabox_stream(stacks):
    writer, flush = _row_flusher()
    writer.writerow(MANABOX_HEADER)
    yield flush()
    for stack, card in stacks:
        price = "" if stack.purchase_price is None else stack.purchase_price
        writer.writerow([
            stack.binder_name or "", "", card.name, card.set_code, card.set_name or "",
            card.collector_number, stack.finish, card.rarity or "", stack.quantity, "",
            str(card.scryfall_id), price, "false", "false", stack.condition or "",
            stack.language, "USD" if price != "" else "", "",
        ])
        yield flush()


@router.get("/export")
async def export(
    q: str = "",
    scope: str = SearchScope.COLLECTION.value,
    sort: str = DEFAULT_SORT,
    dir: str = "asc",
    fmt: str = "csv",
    session: AsyncSession = Depends(get_session),
) -> StreamingResponse:
    if fmt not in FORMATS:
        fmt = "csv"
    scope_enum = SearchScope.ALL if scope == SearchScope.ALL.value else SearchScope.COLLECTION
    sort = sort if sort in SORT_KEYS else DEFAULT_SORT
    descending = dir == "desc"
    filename, media_type = FORMATS[fmt]

    try:
        base = build_search(q, scope_enum, sort=sort, descending=descending)
    except SearchError:
        # An invalid query exports nothing rather than 500ing the download.
        base = build_search("", scope_enum, sort=sort, descending=descending).where(False)

    if fmt == "manabox":
        # Only owned stacks become ManaBox rows; intersect the search with what's owned.
        ids = select(base.subquery().c.scryfall_id)
        rows = (
            await session.execute(
                select(CollectionCard, Card)
                .join(Card, Card.scryfall_id == CollectionCard.scryfall_id)
                .where(CollectionCard.scryfall_id.in_(ids))
                .order_by(Card.name, CollectionCard.finish)
                .limit(MAX_EXPORT)
            )
        ).all()
        content = _manabox_stream([(r[0], r[1]) for r in rows])
    else:
        cards = list(
            (
                await session.execute(
                    base.options(load_only(*_CARD_EXPORT_COLS, raiseload=True)).limit(MAX_EXPORT)
                )
            )
            .scalars()
            .all()
        )
        qmap = await _owned_quantities(session, [c.scryfall_id for c in cards])
        content = _csv_stream(cards, qmap) if fmt == "csv" else _txt_stream(cards, qmap)

    return StreamingResponse(
        content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
