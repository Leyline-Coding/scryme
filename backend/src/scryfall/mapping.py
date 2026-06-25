"""Map a raw Scryfall card object to ``cards`` column values.

Most fields are top-level, but split / modal-double-faced cards carry some attributes only on
``card_faces``. For those we fall back to a ``//``-joined value of the faces so search on
oracle text / type line / mana cost still matches either face. The complete object is always
preserved in ``raw`` for anything not promoted to a column.
"""

from __future__ import annotations

import datetime
from typing import Any


def _face_join(raw: dict, field: str) -> str | None:
    faces = raw.get("card_faces") or []
    parts = [f.get(field) for f in faces if f.get(field)]
    return " // ".join(parts) if parts else None


def _colors_union(raw: dict, field: str) -> list[str] | None:
    if raw.get(field) is not None:
        return raw[field]
    seen: list[str] = []
    for face in raw.get("card_faces") or []:
        for c in face.get(field) or []:
            if c not in seen:
                seen.append(c)
    return seen or None


def _parse_date(value: Any) -> datetime.date | None:
    if not value:
        return None
    try:
        return datetime.date.fromisoformat(value)
    except (ValueError, TypeError):
        return None


def card_to_columns(raw: dict) -> dict[str, Any]:
    """Return kwargs suitable for inserting/updating a :class:`~src.models.card.Card`."""
    return {
        "scryfall_id": raw["id"],
        "oracle_id": raw.get("oracle_id"),
        "name": raw.get("name", ""),
        "set_code": (raw.get("set") or "").lower(),
        "set_name": raw.get("set_name"),
        "collector_number": raw.get("collector_number", ""),
        "rarity": raw.get("rarity"),
        "mana_cost": raw.get("mana_cost") or _face_join(raw, "mana_cost"),
        "cmc": raw.get("cmc"),
        "type_line": raw.get("type_line") or _face_join(raw, "type_line"),
        "oracle_text": raw.get("oracle_text") or _face_join(raw, "oracle_text"),
        "power": raw.get("power"),
        "toughness": raw.get("toughness"),
        "loyalty": raw.get("loyalty"),
        "colors": _colors_union(raw, "colors"),
        "color_identity": raw.get("color_identity"),
        "keywords": raw.get("keywords") or None,
        "lang": raw.get("lang", "en"),
        "layout": raw.get("layout"),
        "released_at": _parse_date(raw.get("released_at")),
        "legalities": raw.get("legalities"),
        "prices": raw.get("prices"),
        "raw": raw,
    }


def image_url(raw: dict, size: str = "normal") -> str | None:
    """Best image URL for a card at the requested size (front face for two-faced cards)."""
    uris = raw.get("image_uris")
    if uris and uris.get(size):
        return uris[size]
    for face in raw.get("card_faces") or []:
        face_uris = face.get("image_uris")
        if face_uris and face_uris.get(size):
            return face_uris[size]
    return None
