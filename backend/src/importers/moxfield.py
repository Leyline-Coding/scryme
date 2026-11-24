"""Moxfield collection CSV importer.

Moxfield's collection export header:

    Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,
    Collector Number,Alter,Proxy,Purchase Price

``Edition`` is the set code; ``Foil`` is ``foil`` / ``etched`` / empty. No Scryfall ID, so rows
match on set code + collector number (then name).
"""

from __future__ import annotations

import csv
import io
from typing import ClassVar

from src.importers.base import ImportRow, register
from src.importers.util import normalize_finish, normalize_language, to_float, to_int


@register
class MoxfieldImporter:
    format_name: ClassVar[str] = "moxfield"

    @classmethod
    def detect(cls, text: str) -> bool:
        head = text.lstrip().splitlines()[0] if text.strip() else ""
        return all(c in head for c in ("Tradelist Count", "Edition", "Collector Number",
                                       "Purchase Price"))

    @classmethod
    def parse(cls, text: str) -> list[ImportRow]:
        reader = csv.DictReader(io.StringIO(text))
        rows: list[ImportRow] = []
        for raw in reader:
            name = (raw.get("Name") or "").strip()
            if not name:
                continue
            rows.append(
                ImportRow(
                    name=name,
                    quantity=to_int(raw.get("Count")),
                    set_code=(raw.get("Edition") or "").strip().lower() or None,
                    collector_number=(raw.get("Collector Number") or "").strip() or None,
                    scryfall_id=None,
                    finish=normalize_finish(raw.get("Foil")),
                    condition=(raw.get("Condition") or "").strip() or None,
                    language=normalize_language(raw.get("Language")),
                    purchase_price=to_float(raw.get("Purchase Price")),
                    location=None,
                )
            )
        return rows
