"""Commander bracket estimator (#159).

WotC's Commander **bracket system** (1–5: Exhibition / Core / Upgraded / Optimized / cEDH) is the
common language for gauging a deck's power. This produces a transparent 1–5 *estimate* from signals
that are all derivable from data scryme already stores (``cards.raw`` + oracle text), plus the small
curated lists below — no new data source:

- **Game Changers** — read straight off Scryfall's ``game_changer`` boolean (in ``cards.raw``); any
  present raises the floor, several raise it further.
- **Mass land denial**, **extra-turn chaining**, **two-card infinite combos** — each forces a
  higher floor.
- **Fast mana** and **tutors** — density signals that nudge a baseline deck up a notch.

Every contributing factor is returned as a :class:`Signal` so the deck page can show *why*, never a
black box. The heuristic is intentionally capped at bracket 4 — separating Optimized (4) from cEDH
(5) reliably needs human judgement.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Card, Deck

BRACKET_LABELS = {1: "Exhibition", 2: "Core", 3: "Upgraded", 4: "Optimized", 5: "cEDH"}
_MAX_HEURISTIC_BRACKET = 4  # 4-vs-5 isn't reliably separable by heuristics; cap here.
_BASELINE = 2               # a typical, unoptimised deck sits at Core (2).

# Mass land denial — names + text signals. Any present pushes a deck to at least bracket 4.
_MLD_NAMES = {
    "armageddon", "ravages of war", "catastrophe", "jokulhaups", "obliterate",
    "decree of annihilation", "winter orb", "static orb", "death cloud", "cataclysm",
    "impending disaster", "boiling seas", "sunder", "fall of the thran",
}
_MLD_TEXT = ("destroy all lands",)

# Fast mana — explosive rocks/rituals used as a density signal. Sol Ring is legal at every bracket,
# so it is intentionally omitted.
_FAST_MANA = {
    "mana crypt", "mana vault", "jeweled lotus", "grim monolith", "chrome mox", "mox diamond",
    "mox opal", "mox amber", "lotus petal", "dark ritual", "cabal ritual", "lion's eye diamond",
    "ancient tomb",
}

# Well-known two-card infinite combos — both halves present flags a "possible infinite combo".
# Stored with display names; matched case-insensitively.
_COMBO_PAIRS = [
    ("Thassa's Oracle", "Demonic Consultation"),
    ("Thassa's Oracle", "Tainted Pact"),
    ("Laboratory Maniac", "Demonic Consultation"),
    ("Kiki-Jiki, Mirror Breaker", "Zealous Conscripts"),
    ("Kiki-Jiki, Mirror Breaker", "Restoration Angel"),
    ("Splinter Twin", "Deceiver Exarch"),
    ("Isochron Scepter", "Dramatic Reversal"),
    ("Mikaeus, the Unhallowed", "Triskelion"),
    ("Walking Ballista", "Heliod, Sun-Crowned"),
    ("Dockside Extortionist", "Temur Sabertooth"),
    ("Worldgorger Dragon", "Animate Dead"),
    ("Food Chain", "Eternal Scourge"),
]

# Density thresholds that bump a baseline deck from Core (2) to Upgraded (3).
_FAST_MANA_BUMP = 2
_TUTOR_BUMP = 3


@dataclass
class Signal:
    """One factor contributing to the estimate. ``weight`` is a rough 1–3 severity for display."""
    label: str
    detail: str
    weight: int


@dataclass
class BracketEstimate:
    bracket: int
    label: str
    signals: list[Signal] = field(default_factory=list)
    is_commander: bool = False


@dataclass
class _CardInfo:
    name: str
    type_line: str
    oracle_text: str
    game_changer: bool


def _names(names: list[str], limit: int = 6) -> str:
    """Comma-joined names, capped with a '+N more' tail so a long list stays readable."""
    shown = names[:limit]
    extra = len(names) - len(shown)
    joined = ", ".join(shown)
    return f"{joined}, +{extra} more" if extra > 0 else joined


def _is_commander_card(c: _CardInfo) -> bool:
    t = c.type_line.lower()
    if "legendary" in t and "creature" in t:
        return True
    # Planeswalker / Background commanders and the like advertise it in their text.
    return "can be your commander" in c.oracle_text.lower()


def _is_mld(c: _CardInfo) -> bool:
    return c.name.lower() in _MLD_NAMES or any(sig in c.oracle_text.lower() for sig in _MLD_TEXT)


def _is_extra_turn(c: _CardInfo) -> bool:
    o = c.oracle_text.lower()
    return "take an extra turn" in o or "takes an extra turn" in o


def _is_tutor(c: _CardInfo) -> bool:
    # Unconditional tutors ("search your library for a card ..."); land fetches read "for a
    # <type> land card" / "for a basic land card" and are excluded.
    return "search your library for a card" in c.oracle_text.lower()


def _find_combos(cards: list[_CardInfo]) -> list[str]:
    names_lower = {c.name.lower() for c in cards}
    return [f"{a} + {b}" for a, b in _COMBO_PAIRS
            if a.lower() in names_lower and b.lower() in names_lower]


def score_bracket(cards: list[_CardInfo]) -> BracketEstimate:
    """Pure scorer over resolved card info — the testable core of the estimator."""
    is_commander = any(_is_commander_card(c) for c in cards)
    signals: list[Signal] = []
    floor = _BASELINE

    game_changers = [c.name for c in cards if c.game_changer]
    if game_changers:
        gc_floor = 4 if len(game_changers) >= 4 else 3
        floor = max(floor, gc_floor)
        signals.append(Signal("Game Changers",
                              f"{len(game_changers)}: {_names(game_changers)}",
                              3 if gc_floor >= 4 else 2))

    mld = [c.name for c in cards if _is_mld(c)]
    if mld:
        floor = max(floor, 4)
        signals.append(Signal("Mass land denial", _names(mld), 3))

    extra_turns = [c.name for c in cards if _is_extra_turn(c)]
    if len(extra_turns) >= 2:
        floor = max(floor, 4)
        signals.append(Signal("Extra-turn chaining", _names(extra_turns), 3))
    elif extra_turns:
        signals.append(Signal("Extra turns", extra_turns[0], 1))

    combos = _find_combos(cards)
    if combos:
        floor = max(floor, 4)
        signals.append(Signal("Possible infinite combo", "; ".join(combos), 3))

    fast_mana = [c.name for c in cards if c.name.lower() in _FAST_MANA]
    tutors = [c.name for c in cards if _is_tutor(c)]
    if fast_mana:
        signals.append(Signal("Fast mana", f"{len(fast_mana)}: {_names(fast_mana)}", 2))
    if tutors:
        signals.append(Signal("Tutors", f"{len(tutors)}: {_names(tutors)}", 1))

    # A baseline deck with a fast-mana/tutor package feels "Upgraded" (3).
    if floor <= _BASELINE and (len(fast_mana) >= _FAST_MANA_BUMP or len(tutors) >= _TUTOR_BUMP):
        floor = 3

    bracket = min(floor, _MAX_HEURISTIC_BRACKET)
    return BracketEstimate(bracket=bracket, label=BRACKET_LABELS[bracket],
                          signals=signals, is_commander=is_commander)


async def estimate_bracket(session: AsyncSession, deck: Deck) -> BracketEstimate:
    """Estimate a deck's Commander bracket from its resolved mainboard cards."""
    sids = [c.scryfall_id for c in deck.cards if c.board == "main" and c.scryfall_id]
    cards: list[_CardInfo] = []
    if sids:
        rows = (await session.execute(
            select(Card.name, Card.type_line, Card.oracle_text, Card.raw)
            .where(Card.scryfall_id.in_(sids))
        )).all()
        cards = [
            _CardInfo(name=name or "", type_line=type_line or "", oracle_text=oracle_text or "",
                      game_changer=bool((raw or {}).get("game_changer")))
            for name, type_line, oracle_text, raw in rows
        ]
    return score_bracket(cards)
