"""ManaBox CSV importer.

ManaBox exports include a ``Scryfall ID`` column (the most reliable match key) alongside set
code + collector number. Header:

    Binder Name,Binder Type,Name,Set code,Set name,Collector number,Foil,Rarity,Quantity,
    ManaBox ID,Scryfall ID,Purchase price,Misprint,Altered,Condition,Language,
    Purchase price currency,Added
"""

from __future__ import annotations

import csv
import io
from typing import ClassVar

from src.importers.base import ImportRow, register


def _to_int(value: str | None, default: int = 1) -> int:
    try:
        return int(value) if value else default
    except ValueError:
        return default


def _to_float(value: str | None) -> float | None:
    try:
        return float(value) if value else None
    except ValueError:
        return None


def _finish(value: str | None) -> str:
    v = (value or "normal").strip().lower()
    return v if v in ("normal", "foil", "etched") else "normal"


@register
class ManaBoxImporter:
    format_name: ClassVar[str] = "manabox"

    @classmethod
    def detect(cls, text: str) -> bool:
        header = text.lstrip().splitlines()[0] if text.strip() else ""
        return "Scryfall ID" in header and "ManaBox ID" in header

    @classmethod
    def parse(cls, text: str) -> list[ImportRow]:
        reader = csv.DictReader(io.StringIO(text))
        rows: list[ImportRow] = []
        for raw in reader:
            name = (raw.get("Name") or "").strip()
            if not name:
                continue
            sid = (raw.get("Scryfall ID") or "").strip() or None
            set_code = (raw.get("Set code") or "").strip().lower() or None
            rows.append(
                ImportRow(
                    name=name,
                    quantity=_to_int(raw.get("Quantity")),
                    set_code=set_code,
                    collector_number=(raw.get("Collector number") or "").strip() or None,
                    scryfall_id=sid,
                    finish=_finish(raw.get("Foil")),
                    condition=(raw.get("Condition") or "").strip() or None,
                    language=(raw.get("Language") or "en").strip() or "en",
                    purchase_price=_to_float(raw.get("Purchase price")),
                    binder_name=(raw.get("Binder Name") or "").strip() or None,
                )
            )
        return rows
