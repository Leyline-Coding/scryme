"""High-level search: assemble and run a query from a Scryfall-style string.

Scope:
  * COLLECTION (default) restricts results to cards the user owns;
  * ALL searches the full local card database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum

from sqlalchemy import Float, Select, case, cast, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Card, CollectionCard
from src.search.compiler import compile_node
from src.search.parser import parse

DEFAULT_PAGE_SIZE = 60

# Sort keys exposed in the UI. `name` is the default and preserves the original ordering.
SORT_KEYS = ("name", "mv", "price", "set", "rarity", "released")
DEFAULT_SORT = "name"

# Printed rarity has no natural lexical order, so rank it explicitly (rarest last for asc).
_RARITY_RANK = case(
    {"common": 1, "uncommon": 2, "rare": 3, "mythic": 4, "special": 5, "bonus": 6},
    value=Card.rarity,
    else_=9,
)


class SearchScope(str, Enum):
    COLLECTION = "collection"
    ALL = "all"


def _order_by(sort: str, descending: bool) -> list:
    """Build the ORDER BY clauses for a sort key, NULLs last, with a stable tiebreaker."""
    if sort == "mv":
        cols = [Card.cmc]
    elif sort == "price":
        # prices is JSONB; sort by the USD price as a number (missing prices sort last).
        cols = [cast(Card.prices["usd"].astext, Float)]
    elif sort == "rarity":
        cols = [_RARITY_RANK]
    elif sort == "released":
        cols = [Card.released_at]
    elif sort == "set":
        cols = [Card.set_code, Card.collector_number]
    else:  # name (default)
        cols = [Card.name]

    ordered = [(c.desc() if descending else c.asc()).nulls_last() for c in cols]
    # Deterministic tiebreaker so paging is stable when the sort key has ties.
    ordered += [Card.name.asc(), Card.scryfall_id.asc()]
    return ordered


@dataclass
class SearchResult:
    cards: list[Card]
    total: int
    page: int
    page_size: int
    quantities: dict[str, int] = field(default_factory=dict)
    tags: dict[str, list[str]] = field(default_factory=dict)

    @property
    def total_pages(self) -> int:
        return max(1, (self.total + self.page_size - 1) // self.page_size)


def build_search(
    query: str,
    scope: SearchScope = SearchScope.COLLECTION,
    *,
    sort: str = DEFAULT_SORT,
    descending: bool = False,
) -> Select:
    """Build a SELECT over cards for ``query`` (raises SearchError on a bad query)."""
    node = parse(query)
    stmt = select(Card)
    if scope is SearchScope.COLLECTION:
        # IN-subquery (not a join) keeps one row per printing even with multiple owned stacks.
        stmt = stmt.where(Card.scryfall_id.in_(select(CollectionCard.scryfall_id)))
    if node is not None:
        stmt = stmt.where(compile_node(node))
    if sort not in SORT_KEYS:
        sort = DEFAULT_SORT
    return stmt.order_by(*_order_by(sort, descending))


async def _owned_quantities(session: AsyncSession, ids: list) -> dict[str, int]:
    if not ids:
        return {}
    rows = await session.execute(
        select(CollectionCard.scryfall_id, func.sum(CollectionCard.quantity))
        .where(CollectionCard.scryfall_id.in_(ids))
        .group_by(CollectionCard.scryfall_id)
    )
    return {str(sid): int(qty) for sid, qty in rows.all()}


async def _owned_tags(session: AsyncSession, ids: list) -> dict[str, list[str]]:
    """Union of tags per printing across its owned stacks, for the result cards on this page."""
    if not ids:
        return {}
    rows = await session.execute(
        select(CollectionCard.scryfall_id, CollectionCard.tags)
        .where(CollectionCard.scryfall_id.in_(ids))
        .where(CollectionCard.tags.isnot(None))
    )
    out: dict[str, set] = {}
    for sid, tags in rows.all():
        if tags:
            out.setdefault(str(sid), set()).update(tags)
    return {sid: sorted(tags) for sid, tags in out.items()}


def _is_name_query(query: str) -> bool:
    """True for a plain-words query (no key:filters, regex, comparisons, or parens)."""
    q = query.strip()
    return bool(q) and not any(ch in q for ch in ":/<>=()")


async def name_suggestions(
    session: AsyncSession, query: str, scope: SearchScope, limit: int = 5
) -> list[str]:
    """Closest card names to a plain-name query, via pg_trgm similarity (for zero-result help)."""
    if not _is_name_query(query):
        return []
    q = query.strip().strip('"')
    sim = func.similarity(Card.name, q)
    stmt = select(Card.name).where(Card.name.op("%")(q))
    if scope is SearchScope.COLLECTION:
        stmt = stmt.where(Card.scryfall_id.in_(select(CollectionCard.scryfall_id)))
    stmt = stmt.group_by(Card.name).order_by(func.max(sim).desc()).limit(limit)
    rows = await session.execute(stmt)
    return [name for (name,) in rows.all()]


async def run_search(
    session: AsyncSession,
    query: str,
    *,
    scope: SearchScope = SearchScope.COLLECTION,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
    sort: str = DEFAULT_SORT,
    descending: bool = False,
) -> SearchResult:
    """Execute a search and return the requested page plus owned quantities."""
    page = max(1, page)
    base = build_search(query, scope, sort=sort, descending=descending)

    total = await session.scalar(select(func.count()).select_from(base.subquery())) or 0
    rows = await session.execute(base.limit(page_size).offset((page - 1) * page_size))
    cards = list(rows.scalars().all())

    ids = [c.scryfall_id for c in cards]
    quantities = await _owned_quantities(session, ids)
    tags = await _owned_tags(session, ids)
    return SearchResult(
        cards=cards, total=total, page=page, page_size=page_size,
        quantities=quantities, tags=tags,
    )
