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

from src.models import Card, CollectionCard
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


def _count_colors(cs, colors: dict) -> int:
    """Tally color letters into ``colors``; return 1 when the card is colorless."""
    if not cs:
        return 1
    for letter in cs:
        colors[letter.lower()] = colors.get(letter.lower(), 0) + 1
    return 0


def _count_legal(legalities: dict, legal: dict) -> None:
    for fmt in _FACET_FORMATS:
        if legalities.get(fmt) in _LEGAL:
            legal[fmt] = legal.get(fmt, 0) + 1


@dataclass
class _FacetTally:
    colors: dict
    colorless: int
    rarities: dict
    types: dict
    sets: dict
    years: dict
    legal: dict


def _tally_facets(rows) -> _FacetTally:
    """Count facet dimensions over the (capped) result rows."""
    colors: dict[str, int] = {}
    colorless = 0
    rarities: dict[str, int] = {}
    types: dict[str, int] = {}
    sets: dict[str, tuple[str, int]] = {}  # code -> (label, count)
    years: dict[int, int] = {}
    legal: dict[str, int] = {}
    for c in rows:
        colorless += _count_colors(c.colors or [], colors)
        if c.rarity:
            rarities[c.rarity] = rarities.get(c.rarity, 0) + 1
        pt = _primary_type(c.type_line)
        types[pt] = types.get(pt, 0) + 1
        label, n = sets.get(c.set_code, (c.set_name or c.set_code.upper(), 0))
        sets[c.set_code] = (label, n + 1)
        if c.released_at:
            years[c.released_at.year] = years.get(c.released_at.year, 0) + 1
        _count_legal(c.legalities or {}, legal)
    return _FacetTally(colors, colorless, rarities, types, sets, years, legal)


def _color_rarity_type_groups(query: str, t: _FacetTally) -> list[FacetGroup]:
    groups: list[FacetGroup] = []
    color_items = [
        (name, f"c:{ltr}", t.colors[ltr]) for ltr, name in _COLOR_FACETS if t.colors.get(ltr)
    ]
    if t.colorless:
        color_items.append(("Colorless", "c:c", t.colorless))
    if color_items:
        groups.append(_group(query, "colors", "Colors", color_items))
    if t.rarities:
        ordered = sorted(t.rarities, key=lambda r: _RARITY_ORDER.index(r) if r in _RARITY_ORDER
                         else len(_RARITY_ORDER))
        groups.append(_group(query, "rarity", "Rarity",
                             [(r.capitalize(), f"r:{r}", t.rarities[r]) for r in ordered]))
    if t.types:
        ordered_types = sorted(t.types.items(), key=lambda kv: kv[1], reverse=True)
        groups.append(_group(query, "type", "Type",
                             [(ty, f"t:{ty.lower()}", n) for ty, n in ordered_types]))
    return groups


def _set_year_legal_finish_groups(
    query: str, t: _FacetTally, top_sets: int, foil_count: int, etched_count: int
) -> list[FacetGroup]:
    groups: list[FacetGroup] = []
    if t.sets:
        top = sorted(t.sets.items(), key=lambda kv: kv[1][1], reverse=True)[:top_sets]
        groups.append(_group(query, "set", "Set",
                             [(label, f"s:{code}", n) for code, (label, n) in top]))
    if t.years:
        top_years = sorted(t.years.items(), key=lambda kv: kv[0], reverse=True)[:_TOP_YEARS]
        groups.append(_group(query, "year", "Year",
                             [(str(y), f"year:{y}", n) for y, n in top_years]))
    if t.legal:
        groups.append(_group(query, "legality", "Legal in",
                             [(f.capitalize(), f"f:{f}", t.legal[f])
                              for f in _FACET_FORMATS if t.legal.get(f)]))
    finish_items = []
    if foil_count:
        finish_items.append(("Foil", "is:foil", foil_count))
    if etched_count:
        finish_items.append(("Etched", "is:etched", etched_count))
    if finish_items:
        groups.append(_group(query, "foil", "Finish", finish_items))
    return groups


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

    tally = _tally_facets(rows)

    # Finish facets count printings you own in that finish (matching the is:foil / is:etched
    # filter), not merely printings that *can* be foil/etched — so the count agrees with what
    # clicking the facet returns.
    def _finish_count(finish: str):
        owned = select(CollectionCard.scryfall_id).where(
            func.lower(CollectionCard.finish) == finish
        )
        return select(func.count()).select_from(
            base.where(Card.scryfall_id.in_(owned)).limit(FACET_ROW_CAP).subquery()
        )

    foil_count = await session.scalar(_finish_count("foil")) or 0
    etched_count = await session.scalar(_finish_count("etched")) or 0

    groups = _color_rarity_type_groups(query, tally)
    groups += _set_year_legal_finish_groups(query, tally, top_sets, foil_count, etched_count)
    return groups
