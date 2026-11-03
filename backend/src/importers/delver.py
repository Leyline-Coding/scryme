"""Delver Lens CSV importer.

Delver's export is configurable and often Deckbox-compatible. Column names vary, so lookups are
case-insensitive and accept several aliases. When the export includes a ``Scryfall ID`` that is
the primary match key; otherwise rows fall back to set/number or name. Registered after ManaBox
and Dragon Shield, so it only claims files those two don't recognize.
"""

from __future__ import annotations

import csv
import io
from typing import ClassVar

from src.importers.base import ImportRow, register
from src.importers.util import normalize_finish, normalize_language, to_float, to_int


def _lower_keys(row: dict) -> dict:
    return {(k or "").strip().lower(): v for k, v in row.items()}


def _get(row: dict, *keys: str) -> str | None:
    for k in keys:
        if k in row and (row[k] or "").strip():
            return row[k].strip()
    return None


@register
class DelverImporter:
    format_name: ClassVar[str] = "delver"

    @classmethod
    def detect(cls, text: str) -> bool:
        header = (text.lstrip().splitlines()[0] if text.strip() else "").lower()
        if "manabox id" in header:  # belongs to ManaBox
            return False
        return "scryfall id" in header or ("edition" in header and "card number" in header)

    @classmethod
    def parse(cls, text: str) -> list[ImportRow]:
        reader = csv.DictReader(io.StringIO(text))
        rows: list[ImportRow] = []
        for raw in reader:
            row = _lower_keys(raw)
            name = _get(row, "name", "card name") or ""
            sid = _get(row, "scryfall id", "scryfallid")
            if not name and not sid:
                continue
            rows.append(
                ImportRow(
                    name=name,
                    quantity=to_int(_get(row, "quantity", "count")),
                    set_code=(_get(row, "set code", "set") or "").lower() or None,
                    collector_number=_get(row, "card number", "collector number", "number"),
                    scryfall_id=sid,
                    finish=normalize_finish(_get(row, "foil", "printing", "finish")),
                    condition=_get(row, "condition"),
                    language=normalize_language(_get(row, "language", "lang")),
                    purchase_price=to_float(_get(row, "purchase price", "my price", "price")),
                    binder_name=_get(row, "binder name", "folder name", "tags"),
                    location=None,
                )
            )
        return rows
