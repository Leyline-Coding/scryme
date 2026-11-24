"""Archidekt collection CSV importer.

Archidekt's collection export header:

    Quantity,Name,Finish,Condition,Date Added,Language,Purchase Price,Tags,Edition Name,
    Edition Code,Multiverse Id,Scryfall ID,MTGO ID,Collector Number

``Edition Code`` is the set code; it carries a ``Scryfall ID`` (best match key) and an explicit
``Finish`` (Normal / Foil / Etched).
"""

from __future__ import annotations

import csv
import io
from typing import ClassVar

from src.importers.base import ImportRow, register
from src.importers.util import normalize_finish, normalize_language, to_float, to_int


@register
class ArchidektImporter:
    format_name: ClassVar[str] = "archidekt"

    @classmethod
    def detect(cls, text: str) -> bool:
        head = text.lstrip().splitlines()[0] if text.strip() else ""
        return "Edition Code" in head and "Scryfall ID" in head

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
                    quantity=to_int(raw.get("Quantity")),
                    set_code=(raw.get("Edition Code") or "").strip().lower() or None,
                    collector_number=(raw.get("Collector Number") or "").strip() or None,
                    scryfall_id=(raw.get("Scryfall ID") or "").strip() or None,
                    finish=normalize_finish(raw.get("Finish")),
                    condition=(raw.get("Condition") or "").strip() or None,
                    language=normalize_language(raw.get("Language")),
                    purchase_price=to_float(raw.get("Purchase Price")),
                    location=None,
                )
            )
        return rows
