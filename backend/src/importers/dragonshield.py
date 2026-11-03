"""Dragon Shield (MTG Scanner) CSV importer.

Dragon Shield exports a leading ``sep=,`` line, then:

    Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Set Name,Card Number,Condition,
    Printing,Language,Price Bought,Date Bought,LOW,MID,MARKET

There is no Scryfall ID, so rows match on set code + collector number (then name).
"""

from __future__ import annotations

import csv
import io
from typing import ClassVar

from src.importers.base import ImportRow, register
from src.importers.util import normalize_finish, normalize_language, to_float, to_int


def _strip_sep(text: str) -> str:
    lines = text.splitlines()
    if lines and lines[0].strip().lower().startswith("sep="):
        return "\n".join(lines[1:])
    return text


@register
class DragonShieldImporter:
    format_name: ClassVar[str] = "dragonshield"

    @classmethod
    def detect(cls, text: str) -> bool:
        head = text.lstrip()[:512]
        return "Trade Quantity" in head and "Card Name" in head and "Card Number" in head

    @classmethod
    def parse(cls, text: str) -> list[ImportRow]:
        reader = csv.DictReader(io.StringIO(_strip_sep(text)))
        rows: list[ImportRow] = []
        for raw in reader:
            name = (raw.get("Card Name") or "").strip()
            if not name:
                continue
            rows.append(
                ImportRow(
                    name=name,
                    quantity=to_int(raw.get("Quantity")),
                    set_code=(raw.get("Set Code") or "").strip().lower() or None,
                    collector_number=(raw.get("Card Number") or "").strip() or None,
                    scryfall_id=None,
                    finish=normalize_finish(raw.get("Printing")),
                    condition=(raw.get("Condition") or "").strip() or None,
                    language=normalize_language(raw.get("Language")),
                    purchase_price=to_float(raw.get("Price Bought")),
                    binder_name=(raw.get("Folder Name") or "").strip() or None,
                    location=None,
                )
            )
        return rows
