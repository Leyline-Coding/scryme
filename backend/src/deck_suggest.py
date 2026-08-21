"""Heuristic precon-upgrade suggestions from owned cards (#181).

A deterministic, offline sibling to the AI ``suggest_from_collection`` (#163): given a deck and your
collection, propose **owned** cards — in the deck's colour identity and Commander-legal — that
strengthen the deck's *thin roles* (ramp / card draw / removal), each with a one-line reason and a
one-click add. No LLM, no network.

The heuristic:
1. Bucket the deck's mainboard by role (reusing ``deck_builder.classify_role``) and compare each
   tunable role's count against a typical Commander template target.
2. For every role that's below target, offer the best owned, in-identity, legal candidates of that
   role not already in the deck — ranked by **deck fit**, then mana-curve fit, then price.

Legality and colour identity only guarantee a pick is *allowed*, not that it's *good* (#294), so
candidates are scored against what the deck is actually doing — its themes and the creature types it
cares about — using :mod:`src.deck_synergy`, the same read of the deck the AI grounding uses. The
matched signal is surfaced in each pick's reason, because an unexplained recommendation is one the
reader has no way to disagree with.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.deck_builder import _CARD_DRAW, _TEMPLATE, classify_role
from src.deck_synergy import DeckProfile, card_synergy, deck_themes, deck_tribes, synergy_note
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
    synergy: int = 0
    fit: str = ""   # why this card fits the deck, e.g. "matches your tokens, elf"


@dataclass
class _Candidate:
    name: str
    scryfall_id: str
    role: str
    cmc: float
    price: float
    synergy: int = 0
    fit: str = ""


@dataclass
class UpgradeSuggestions:
    picks: list[UpgradePick] = field(default_factory=list)
    by_role: dict[str, list[UpgradePick]] = field(default_factory=dict)
    considered: int = 0  # size of the owned, in-colour, legal candidate pool
    themes: list[str] = field(default_factory=list)  # what the ranking read the deck as doing

    @property
    def empty(self) -> bool:
        return not self.picks


def _rank_key(cand: _Candidate) -> tuple[int, float, float]:
    """Rank within a role: best deck fit first, then lower curve, then cheaper.

    Synergy leads because every candidate here is already legal, owned and in the right role — the
    remaining question is which one actually advances this deck's plan. Curve and price stay as
    tie-breakers so a deck with no detectable theme ranks exactly as it did before (#294).
    """
    return (-cand.synergy, cand.cmc if cand.cmc is not None else _MISSING_CMC, cand.price)


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


async def _deck_profile(session: AsyncSession, deck: Deck) -> DeckProfile:
    """What this deck is about: its themes, and the creature types it cares about (#294).

    Reads the mainboard only — a sideboard or maybeboard describes what the deck *isn't* doing.
    """
    sids = [c.scryfall_id for c in deck.cards if c.board == "main" and c.scryfall_id]
    if not sids:
        return DeckProfile()

    rows = (await session.execute(
        select(Card.type_line, Card.oracle_text, Card.keywords)
        .where(Card.scryfall_id.in_(sids))
    )).all()

    kw_counts: Counter = Counter()
    texts: list[str] = []
    creature_lines: list[str | None] = []
    commander_text = ""
    for type_line, oracle_text, keywords in rows:
        kw_counts.update(keywords or [])
        texts.append((oracle_text or "").lower())
        line = (type_line or "").lower()
        if "creature" in line:
            creature_lines.append(type_line)
            # Any legendary creature may be the commander; the deck model doesn't mark one, so
            # pool their text rather than guessing which. Over-including only widens the tribes a
            # little, where guessing wrong would miss the deck's actual plan entirely.
            if "legendary" in line:
                commander_text += " " + (oracle_text or "")

    return DeckProfile(
        themes=deck_themes(kw_counts, texts),
        tribes=deck_tribes(commander_text, creature_lines),
    )


def _row_to_candidate(
    row, deck_oracles: set, identity: set[str], currency: str, source: str,
    profile: DeckProfile,
) -> _Candidate | None:
    """Turn one owned-card row into a tunable-role candidate, or None if it doesn't qualify."""
    (name, oracle, sid, ci, type_line, oracle_text, cmc, prices, market, legalities,
     keywords) = row
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
    score, reasons = card_synergy(
        profile, keywords=keywords, type_line=type_line, oracle_text=oracle_text
    )
    return _Candidate(name=name, scryfall_id=str(sid), role=role,
                      cmc=cmc if cmc is not None else _MISSING_CMC, price=price,
                      synergy=score, fit=synergy_note(reasons))


async def _candidate_pool(
    session: AsyncSession, deck: Deck, currency: str, source: str, profile: DeckProfile
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
            Card.keywords,
        )
        .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
        .where(Card.oracle_id.is_not(None))
        .distinct(Card.oracle_id)
        .order_by(Card.oracle_id, Card.released_at.desc().nulls_last())
    )).all()

    candidates = (
        _row_to_candidate(row, deck_oracles, identity, currency, source, profile) for row in rows
    )
    return [c for c in candidates if c is not None]


async def suggest_owned_upgrades(
    session: AsyncSession, deck: Deck, currency: str = "usd", source: str = "tcgplayer",
) -> UpgradeSuggestions:
    """Suggest owned cards to add, bucketed by the deck's thin roles (deterministic, no LLM)."""
    counts = await _deck_role_counts(session, deck)
    profile = await _deck_profile(session, deck)
    pool = await _candidate_pool(session, deck, currency, source, profile)

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
                        cmc=c.cmc, price=c.price, synergy=c.synergy, fit=c.fit)
            for c in best
        ]
        picks.extend(role_picks)
        by_role[role] = role_picks

    return UpgradeSuggestions(picks=picks, by_role=by_role, considered=len(pool),
                              themes=profile.themes + sorted(profile.tribes))
