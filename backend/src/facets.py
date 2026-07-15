"""Facets for the current search: counts by color, rarity, type, and set.

Each facet value maps to a Scryfall token (`c:`, `r:`, `t:`, `s:`) so clicking it toggles that
filter on the live query — the query string stays the single source of truth. Counts are computed
in Python over a capped projection of the result set (no array/type SQL gymnastics), mirroring how
`stats` aggregates.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only

from src.models import Card
from src.search import SearchScope
from src.search.engine import build_search
from src.stats import _RARITY_ORDER, _primary_type

FACET_ROW_CAP = 4000  # facet counts are computed over at most this many result rows
_COLOR_FACETS = [("w", "White"), ("u", "Blue"), ("b", "Black"), ("r", "Red"), ("g", "Green")]
# Formats offered in the "Legal in" facet, in display order.
_FACET_FORMATS = ["commander", "modern", "pioneer", "standard", "legacy", "vintage", "pauper"]
_LEGAL = {"legal", "restricted"}
_TOP_YEARS = 8  # most-recent years shown in the Year facet


@dataclass
class FacetValue:
    label: str
    token: str        # e.g. "c:w"
    count: int
    active: bool      # token already present in the query
    new_query: str    # query with the token toggled on/off


@dataclass
class FacetGroup:
    key: str
    title: str
    values: list[FacetValue] = field(default_factory=list)


def _toggle(query: str, token: str) -> tuple[bool, str]:
    """Return (was_present, query-with-token-toggled). Case-insensitive token match."""
    tokens = query.split()
    low = token.lower()
    present = any(t.lower() == low for t in tokens)
    if present:
        kept = [t for t in tokens if t.lower() != low]
    else:
        kept = [*tokens, token]
    return present, " ".join(kept)


def _group(query: str, key: str, title: str, items: list[tuple[str, str, int]]) -> FacetGroup:
    """items: (label, token, count) -> a FacetGroup with toggled queries filled in."""
    values = []
    for label, token, count in items:
        active, new_query = _toggle(query, token)
        values.append(FacetValue(label=label, token=token, count=count,
                                  active=active, new_query=new_query))
    return FacetGroup(key=key, title=title, values=values)


async def compute_facets(
    session: AsyncSession, query: str, scope: SearchScope, top_sets: int = 8
) -> list[FacetGroup]:
    """Facet groups for the result set of (query, scope). Raises SearchError on a bad query."""
    base = build_search(query, scope)
    rows = (
        await session.execute(
            base.options(
                load_only(
                    Card.rarity, Card.colors, Card.type_line, Card.set_code, Card.set_name,
                    Card.released_at, Card.legalities,
                    raiseload=True,
                )
            ).limit(FACET_ROW_CAP)
        )
    ).scalars().all()

    colors: dict[str, int] = {}
    colorless = 0
    rarities: dict[str, int] = {}
    types: dict[str, int] = {}
    sets: dict[str, tuple[str, int]] = {}  # code -> (label, count)
    years: dict[int, int] = {}
    legal: dict[str, int] = {}
    for c in rows:
        cs = c.colors or []
        if cs:
            for letter in cs:
                colors[letter.lower()] = colors.get(letter.lower(), 0) + 1
        else:
            colorless += 1
        if c.rarity:
            rarities[c.rarity] = rarities.get(c.rarity, 0) + 1
        types[_primary_type(c.type_line)] = types.get(_primary_type(c.type_line), 0) + 1
        label, n = sets.get(c.set_code, (c.set_name or c.set_code.upper(), 0))
        sets[c.set_code] = (label, n + 1)
        if c.released_at:
            years[c.released_at.year] = years.get(c.released_at.year, 0) + 1
        legalities = c.legalities or {}
        for fmt in _FACET_FORMATS:
            if legalities.get(fmt) in _LEGAL:
                legal[fmt] = legal.get(fmt, 0) + 1

    # Finish availability lives in the raw card object (avoid loading it for every row above);
    # a couple of counts over the same result set are cheap.
    foil_count = await session.scalar(
        select(func.count()).select_from(
            base.where(Card.raw["foil"].astext == "true").limit(FACET_ROW_CAP).subquery()
        )
    ) or 0
    etched_count = await session.scalar(
        select(func.count()).select_from(
            base.where(Card.raw["finishes"].astext.contains("etched")).limit(FACET_ROW_CAP).subquery()
        )
    ) or 0

    groups: list[FacetGroup] = []

    color_items = [
        (name, f"c:{ltr}", colors[ltr]) for ltr, name in _COLOR_FACETS if colors.get(ltr)
    ]
    if colorless:
        color_items.append(("Colorless", "c:c", colorless))
    if color_items:
        groups.append(_group(query, "colors", "Colors", color_items))

    if rarities:
        ordered = sorted(rarities, key=lambda r: _RARITY_ORDER.index(r) if r in _RARITY_ORDER
                         else len(_RARITY_ORDER))
        groups.append(_group(query, "rarity", "Rarity",
                             [(r.capitalize(), f"r:{r}", rarities[r]) for r in ordered]))

    if types:
        ordered = sorted(types.items(), key=lambda kv: kv[1], reverse=True)
        groups.append(_group(query, "type", "Type",
                             [(t, f"t:{t.lower()}", n) for t, n in ordered]))

    if sets:
        top = sorted(sets.items(), key=lambda kv: kv[1][1], reverse=True)[:top_sets]
        groups.append(_group(query, "set", "Set",
                             [(label, f"s:{code}", n) for code, (label, n) in top]))

    if years:
        top = sorted(years.items(), key=lambda kv: kv[0], reverse=True)[:_TOP_YEARS]
        groups.append(_group(query, "year", "Year",
                             [(str(y), f"year:{y}", n) for y, n in top]))

    if legal:
        groups.append(_group(query, "legality", "Legal in",
                             [(f.capitalize(), f"f:{f}", legal[f])
                              for f in _FACET_FORMATS if legal.get(f)]))

    finish_items = []
    if foil_count:
        finish_items.append(("Foil", "is:foil", foil_count))
    if etched_count:
        finish_items.append(("Etched", "is:etched", etched_count))
    if finish_items:
        groups.append(_group(query, "foil", "Finish", finish_items))

    return groups
