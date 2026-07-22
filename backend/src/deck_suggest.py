"""Heuristic precon-upgrade suggestions from owned cards (#181).

A deterministic, offline sibling to the AI ``suggest_from_collection`` (#163): given a deck and your
collection, propose **owned** cards — in the deck's colour identity and Commander-legal — that
strengthen the deck's *thin roles* (ramp / card draw / removal), each with a one-line reason and a
one-click add. No LLM, no network.

The heuristic:
1. Bucket the deck's mainboard by role (reusing ``deck_builder.classify_role``) and compare each
   tunable role's count against a typical Commander template target.
2. For every role that's below target, offer the best owned, in-identity, legal candidates of that
   role not already in the deck — ranked by mana-curve fit, then price.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.deck_builder import _CARD_DRAW, _TEMPLATE, classify_role
from src.models import Card, CollectionCard, Deck
from src.pricing import resolve_prices

# The roles a deck-tuner actually swaps for; targets are taken from the deck-builder template so the
# two features can't drift apart.
_TUNABLE_ROLES = ("Ramp", _CARD_DRAW, "Removal")
_ROLE_TARGETS = {role: target for role, target in _TEMPLATE if role in _TUNABLE_ROLES}
_ALLOWED_LEGAL = {"legal", "restricted"}
_PER_ROLE = 5  # how many candidates to surface per thin role
_MISSING_CMC = 99.0  # sort cards with no mana value to the back of the curve


@dataclass
class UpgradePick:
    name: str
    scryfall_id: str
    role: str
    reason: str
    cmc: float
    price: float


@dataclass
class _Candidate:
    name: str
    scryfall_id: str
    role: str
    cmc: float
    price: float


@dataclass
class UpgradeSuggestions:
    picks: list[UpgradePick] = field(default_factory=list)
    by_role: dict[str, list[UpgradePick]] = field(default_factory=dict)
    considered: int = 0  # size of the owned, in-colour, legal candidate pool

    @property
    def empty(self) -> bool:
        return not self.picks


def _rank_key(cand: _Candidate) -> tuple[float, float]:
    """Rank a candidate within its role: lower curve first, then cheaper."""
    return (cand.cmc if cand.cmc is not None else _MISSING_CMC, cand.price)


async def _deck_identity(session: AsyncSession, deck_sids: list) -> set[str]:
    identity: set[str] = set()
    if deck_sids:
        for (ci,) in (await session.execute(
            select(Card.color_identity).where(Card.scryfall_id.in_(deck_sids))
        )).all():
            identity.update(ci or [])
    return identity


async def _deck_role_counts(session: AsyncSession, deck: Deck) -> dict[str, int]:
    """Current mainboard count for each tunable role (by classify_role, quantity-weighted)."""
    counts = dict.fromkeys(_TUNABLE_ROLES, 0)
    sids = [c.scryfall_id for c in deck.cards if c.board == "main" and c.scryfall_id]
    if not sids:
        return counts
    info = {
        sid: (tl, ot)
        for sid, tl, ot in (await session.execute(
            select(Card.scryfall_id, Card.type_line, Card.oracle_text)
            .where(Card.scryfall_id.in_(sids))
        )).all()
    }
    for c in deck.cards:
        if c.board != "main":
            continue
        type_line, oracle_text = info.get(c.scryfall_id, (None, None))
        role = classify_role(type_line, oracle_text)
        if role in counts:
            counts[role] += c.quantity
    return counts


def _row_to_candidate(
    row, deck_oracles: set, identity: set[str], currency: str, source: str
) -> _Candidate | None:
    """Turn one owned-card row into a tunable-role candidate, or None if it doesn't qualify."""
    name, oracle, sid, ci, type_line, oracle_text, cmc, prices, market, legalities = row
    if oracle in deck_oracles:
        return None
    if identity and not set(ci or []).issubset(identity):
        return None
    if (legalities or {}).get("commander") not in _ALLOWED_LEGAL:
        return None
    role = classify_role(type_line, oracle_text)
    if role not in _TUNABLE_ROLES:
        return None
    price = unit_price(resolve_prices(prices, market, source) or {}, "normal", currency)
    return _Candidate(name=name, scryfall_id=str(sid), role=role,
                      cmc=cmc if cmc is not None else _MISSING_CMC, price=price)


async def _candidate_pool(
    session: AsyncSession, deck: Deck, currency: str, source: str
) -> list[_Candidate]:
    """Owned, in-identity, Commander-legal cards not already in the deck, in a tunable role."""
    deck_sids = [c.scryfall_id for c in deck.cards if c.scryfall_id]
    deck_oracles = {c.oracle_id for c in deck.cards if c.oracle_id}
    identity = await _deck_identity(session, deck_sids)

    # DISTINCT ON (oracle_id) yields one row per card, so no extra de-duplication is needed.
    rows = (await session.execute(
        select(
            Card.name, Card.oracle_id, Card.scryfall_id, Card.color_identity, Card.type_line,
            Card.oracle_text, Card.cmc, Card.prices, Card.market_prices, Card.legalities,
        )
        .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
        .where(Card.oracle_id.is_not(None))
        .distinct(Card.oracle_id)
        .order_by(Card.oracle_id, Card.released_at.desc().nulls_last())
    )).all()

    candidates = (_row_to_candidate(row, deck_oracles, identity, currency, source) for row in rows)
    return [c for c in candidates if c is not None]


async def suggest_owned_upgrades(
    session: AsyncSession, deck: Deck, currency: str = "usd", source: str = "tcgplayer",
) -> UpgradeSuggestions:
    """Suggest owned cards to add, bucketed by the deck's thin roles (deterministic, no LLM)."""
    counts = await _deck_role_counts(session, deck)
    pool = await _candidate_pool(session, deck, currency, source)

    by_role_pool: dict[str, list[_Candidate]] = {role: [] for role in _TUNABLE_ROLES}
    for cand in pool:
        by_role_pool[cand.role].append(cand)

    picks: list[UpgradePick] = []
    by_role: dict[str, list[UpgradePick]] = {}
    for role in _TUNABLE_ROLES:
        have, target = counts.get(role, 0), _ROLE_TARGETS[role]
        if have >= target:
            continue  # role already well-stocked
        best = sorted(by_role_pool[role], key=_rank_key)[:_PER_ROLE]
        if not best:
            continue
        reason = f"Fills thin {role} (deck has {have}, ~{target} typical)"
        role_picks = [
            UpgradePick(name=c.name, scryfall_id=c.scryfall_id, role=role, reason=reason,
                        cmc=c.cmc, price=c.price)
            for c in best
        ]
        picks.extend(role_picks)
        by_role[role] = role_picks

    return UpgradeSuggestions(picks=picks, by_role=by_role, considered=len(pool))
