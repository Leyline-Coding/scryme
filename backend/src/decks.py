"""Deck parsing, card resolution, and ownership coverage.

`parse_decklist` reads a plain decklist (``4 Lightning Bolt``, optional ``(SET) NUM`` suffix,
``Sideboard`` marker / ``SB:`` prefix). `create_deck` resolves each line to a representative
printing + oracle id. `deck_coverage` compares the deck against the owned collection by oracle id
(any printing you own counts) to answer "what am I missing".
"""

from __future__ import annotations

import datetime
import re
from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Card, CollectionCard, Deck, DeckCard

_LINE = re.compile(r"^\s*(\d+)\s*[xX]?\s+(.+?)\s*$")
# Strip a trailing "(SET) 123" / "(SET)" printing hint from a card name.
_SET_SUFFIX = re.compile(r"\s*\([A-Za-z0-9]{2,6}\)\s*[A-Za-z0-9-]*\s*$")


@dataclass
class ParsedLine:
    quantity: int
    name: str
    board: str  # main | side


def parse_decklist(text: str | None) -> list[ParsedLine]:
    out: list[ParsedLine] = []
    board = "main"
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s.startswith(("#", "//")):
            continue
        low = s.lower()
        if low.startswith("sideboard"):
            board = "side"
            continue
        sb = False
        if low.startswith("sb:"):
            sb, s = True, s[3:].strip()
        m = _LINE.match(s)
        if not m:
            continue
        name = _SET_SUFFIX.sub("", m.group(2)).strip()
        if name:
            out.append(ParsedLine(int(m.group(1)), name, "side" if sb else board))
    return out


async def _owned_by_oracle(session: AsyncSession) -> dict:
    rows = await session.execute(
        select(Card.oracle_id, func.sum(CollectionCard.quantity))
        .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
        .group_by(Card.oracle_id)
    )
    return {o: int(q) for o, q in rows.all() if o}


async def _resolve_names(session: AsyncSession, names: list[str], owned_sids: set) -> dict:
    """Map each lowercased name -> (oracle_id, scryfall_id), preferring an owned/latest printing."""
    wanted = {n.lower() for n in names}
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(Card.name, Card.oracle_id, Card.scryfall_id, Card.released_at).where(
                func.lower(Card.name).in_(wanted)
            )
        )
    ).all()
    by_name: dict[str, list] = {}
    for name, oracle, sid, released in rows:
        by_name.setdefault(name.lower(), []).append((oracle, sid, released))

    resolved: dict[str, tuple] = {}
    for low, cands in by_name.items():
        # Prefer a printing the user actually owns (nicer image/price), else the newest.
        cands.sort(
            key=lambda c: (c[1] in owned_sids, c[2] or datetime.date.min),
            reverse=True,
        )
        resolved[low] = (cands[0][0], cands[0][1])

    # Fallback: match the front face of split / double-faced cards ("Name // Other").
    for low in wanted - set(resolved):
        row = (
            await session.execute(
                select(Card.oracle_id, Card.scryfall_id)
                .where(func.lower(Card.name).like(low + " //%"))
                .order_by(Card.released_at.desc().nulls_last())
                .limit(1)
            )
        ).first()
        if row:
            resolved[low] = (row[0], row[1])
    return resolved


async def create_deck(session: AsyncSession, name: str, decklist_text: str) -> Deck:
    parsed = parse_decklist(decklist_text)
    owned_sids = set(await session.scalars(select(CollectionCard.scryfall_id)))
    resolved = await _resolve_names(session, [p.name for p in parsed], owned_sids)

    deck = Deck(name=(name or "").strip()[:256] or "Untitled deck")
    for p in parsed:
        oracle, sid = resolved.get(p.name.lower(), (None, None))
        deck.cards.append(
            DeckCard(name=p.name, quantity=p.quantity, board=p.board,
                     oracle_id=oracle, scryfall_id=sid)
        )
    session.add(deck)
    await session.commit()
    await session.refresh(deck)
    return deck


@dataclass
class CardRow:
    name: str
    quantity: int
    board: str
    owned: int
    matched: bool
    scryfall_id: str | None


@dataclass
class DeckCoverage:
    deck: Deck
    main: list[CardRow] = field(default_factory=list)
    side: list[CardRow] = field(default_factory=list)
    total_needed: int = 0
    missing_count: int = 0          # total physical cards still needed
    unique_missing: int = 0         # distinct cards (oracle / unmatched line) not fully owned
    missing_cost: float = 0.0
    unmatched: int = 0              # lines whose name didn't resolve to a card

    @property
    def owned_count(self) -> int:
        return self.total_needed - self.missing_count

    @property
    def pct_complete(self) -> int:
        return round(100 * self.owned_count / self.total_needed) if self.total_needed else 0


def _usd(prices: dict | None) -> float:
    try:
        return float((prices or {}).get("usd") or 0)
    except (TypeError, ValueError):
        return 0.0


async def deck_coverage(session: AsyncSession, deck: Deck) -> DeckCoverage:
    owned = await _owned_by_oracle(session)

    sids = [c.scryfall_id for c in deck.cards if c.scryfall_id]
    price_by_sid: dict[str, dict] = {}
    oracle_sid: dict = {}
    if sids:
        rows = (
            await session.execute(
                select(Card.scryfall_id, Card.oracle_id, Card.prices).where(
                    Card.scryfall_id.in_(sids)
                )
            )
        ).all()
        for sid, oracle, prices in rows:
            price_by_sid[str(sid)] = prices or {}
            oracle_sid[oracle] = str(sid)

    # Needed totals per oracle across both boards (ownership is shared between main + side).
    needed_by_oracle: dict = {}
    for c in deck.cards:
        if c.oracle_id:
            needed_by_oracle[c.oracle_id] = needed_by_oracle.get(c.oracle_id, 0) + c.quantity

    cov = DeckCoverage(deck=deck)
    for c in deck.cards:
        row = CardRow(
            name=c.name, quantity=c.quantity, board=c.board,
            owned=owned.get(c.oracle_id, 0) if c.oracle_id else 0,
            matched=c.oracle_id is not None,
            scryfall_id=str(c.scryfall_id) if c.scryfall_id else None,
        )
        (cov.main if c.board == "main" else cov.side).append(row)
        cov.total_needed += c.quantity

    # Missing math, counted once per oracle (and per unmatched line).
    for oracle, needed in needed_by_oracle.items():
        miss = max(0, needed - owned.get(oracle, 0))
        if miss:
            cov.missing_count += miss
            cov.unique_missing += 1
            cov.missing_cost += miss * _usd(price_by_sid.get(oracle_sid.get(oracle, ""), {}))
    for c in deck.cards:
        if not c.oracle_id:
            cov.missing_count += c.quantity
            cov.unique_missing += 1
            cov.unmatched += 1
    return cov
