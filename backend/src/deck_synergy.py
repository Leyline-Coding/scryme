"""Deterministic, offline read of what a deck is *about* — themes and tribes (#294).

The heuristic upgrade suggester (:mod:`src.deck_suggest`) and the AI grounding
(:mod:`src.llm`) both need to answer "what is this deck trying to do?", and both must answer it the
same way. Theme detection used to live only in ``llm``, which would have forced the deliberately
offline suggester to import the LLM module to reuse it — or, worse, to grow a second copy that
drifts. (This repo has already paid for exactly that: two card-name resolvers disagreed until DFCs
matched from a decklist but not from a CSV, #338.) So it lives here, with no LLM and no network.

**Tribes are derived from the deck, not from a hardcoded list of creature types.** Magic has
hundreds of them and the list grows every set, so instead the subtypes are read off the deck's own
creatures, and a type counts as one the deck cares about when the commander's text names it or when
enough of the deck already plays it. That stays correct for types invented after this was written.
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field

# Oracle-text phrases that stand in for a theme, checked across the whole deck.
THEME_TEXT_SIGNALS = {
    "+1/+1 counters": "+1/+1 counter", "tokens": "token", "sacrifice": "sacrifice",
    "graveyard recursion": "from your graveyard", "lifegain": "gain life",
    "spellslinger": "instant or sorcery", "mill": "mill",
}

# A tribe the commander names, or that this many of the deck's creatures already share.
_TRIBE_DECK_THRESHOLD = 5
# Subtypes that describe a role rather than a tribe — every deck has some, so they say nothing.
_NON_TRIBAL_SUBTYPES = frozenset({"token"})
_TYPE_SPLIT = re.compile(r"[—–-]")


def deck_themes(kw_counts: Counter, text_cards: list) -> list[str]:
    """Themes from repeated keywords, plus text-signal themes seen in >=4 cards."""
    themes = [k for k, n in kw_counts.most_common(5) if n >= 2]
    for label, sig in THEME_TEXT_SIGNALS.items():
        if label not in themes and sum(1 for t in text_cards if sig in t) >= 4:
            themes.append(label)
    return themes


def subtypes(type_line: str | None) -> set[str]:
    """The creature subtypes on a type line — everything after the em dash, lowercased.

    Only creatures: an Equipment's or Saga's subtypes aren't tribes, and counting them would make
    "Aura" look like a tribe in any enchantment deck.
    """
    line = (type_line or "").lower()
    if "creature" not in line:
        return set()
    parts = _TYPE_SPLIT.split(line, maxsplit=1)
    if len(parts) < 2:
        return set()
    return {w for w in parts[1].split() if w} - _NON_TRIBAL_SUBTYPES


def _plural_forms(tribe: str) -> set[str]:
    """The spellings a tribe might appear as in oracle text.

    Rules text names tribes in the plural far more often than the singular ("Elves you control"),
    and English does not form those by adding an ``s``: Elf/Elves, Dwarf/Dwarves, Ally/Allies. A
    naive ``s?`` silently matches nothing for exactly the tribes people build decks around.

    Only the two irregulars that real creature types actually use are handled. A type this misses
    still matches its singular and regular plural, so an unknown irregular degrades to a missed
    signal rather than a wrong one.
    """
    forms = {tribe, f"{tribe}s", f"{tribe}es"}
    if tribe.endswith("f"):
        forms.add(f"{tribe[:-1]}ves")     # Elf, Wolf, Dwarf
    elif tribe.endswith("y"):
        forms.add(f"{tribe[:-1]}ies")     # Ally
    return forms


def _named_in(text: str, tribe: str) -> bool:
    """Whether oracle text names a tribe, in any of its plausible spellings."""
    forms = sorted(_plural_forms(tribe), key=len, reverse=True)
    alternation = "|".join(re.escape(f) for f in forms)
    return bool(re.search(rf"\b(?:{alternation})\b", text))


@dataclass
class DeckProfile:
    """What the deck is doing, in terms a candidate card can be scored against."""

    themes: list[str] = field(default_factory=list)
    tribes: set[str] = field(default_factory=set)

    @property
    def informative(self) -> bool:
        """False for a deck with no discernible plan — score nothing rather than score noise."""
        return bool(self.themes or self.tribes)

    def text_signals(self) -> dict[str, str]:
        """The detected themes that can be matched against a single card's oracle text."""
        return {t: sig for t, sig in THEME_TEXT_SIGNALS.items() if t in self.themes}


def deck_tribes(commander_text: str, creature_type_lines: list[str | None]) -> set[str]:
    """Creature types this deck cares about: named by the commander, or already played in bulk."""
    counts: Counter = Counter()
    for line in creature_type_lines:
        counts.update(subtypes(line))
    if not counts:
        return set()
    text = (commander_text or "").lower()
    return {
        tribe for tribe, n in counts.items()
        if n >= _TRIBE_DECK_THRESHOLD or (text and _named_in(text, tribe))
    }


def card_synergy(
    profile: DeckProfile,
    *,
    keywords: list[str] | None,
    type_line: str | None,
    oracle_text: str | None,
) -> tuple[int, list[str]]:
    """Score one candidate against the deck's plan. Returns (score, human reasons).

    Deliberately a small integer of matched signals rather than a tuned weighting: it is a
    tie-breaker over an already-filtered pool (owned, in-identity, legal, right role), and a
    precise-looking float would imply an accuracy this has no way to earn.
    """
    reasons: list[str] = []
    score = 0

    matched_kw = [k for k in (keywords or []) if k in profile.themes]
    if matched_kw:
        score += len(matched_kw)
        reasons.append(matched_kw[0].lower())

    text = (oracle_text or "").lower()
    for theme, signal in profile.text_signals().items():
        if signal in text:
            score += 1
            reasons.append(theme)

    matched_tribes = sorted(subtypes(type_line) & profile.tribes)
    if matched_tribes:
        # A tribal hit is worth more than a text match: it's the deck's whole plan, not a mode.
        score += 2 * len(matched_tribes)
        reasons.append(matched_tribes[0])

    return score, reasons


def synergy_note(reasons: list[str], limit: int = 2) -> str:
    """The '· matches your tokens, sacrifice' fragment appended to a pick's reason."""
    if not reasons:
        return ""
    seen: list[str] = []
    for r in reasons:
        if r not in seen:
            seen.append(r)
    return "matches your " + ", ".join(seen[:limit])
