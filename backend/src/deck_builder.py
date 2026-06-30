"""Build a Commander deck from owned cards (#87).

Given a commander you own, suggest a 99-card singleton deck drawn from cards already in your
collection that fit the commander's colour identity and are Commander-legal. Cards are sorted into
role buckets by simple heuristics (lands / ramp / draw / removal / creatures / other), filled toward
a typical template, and the mana base is topped up with basic lands. It is intentionally a *starting
point*, not an optimised list — the result is handed to the user as an editable decklist.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models import Card, CollectionCard

BASIC_BY_COLOR = {"W": "Plains", "U": "Island", "B": "Swamp", "R": "Mountain", "G": "Forest"}
_ALLOWED_LEGAL = {"legal", "restricted"}

DECK_SIZE = 99  # cards besides the commander
LAND_TARGET = 35
# Non-land role targets; the remainder of the 99 is creatures + filler.
_TEMPLATE = [
    ("Ramp", 10),
    ("Card draw", 10),
    ("Removal", 10),
    ("Creatures", 28),
    ("Other", 6),
]
ROLES = ["Lands", "Ramp", "Card draw", "Removal", "Creatures", "Other"]


@dataclass
class PoolCard:
    name: str
    cmc: float
    type_line: str
    oracle_text: str


@dataclass
class RoleGroup:
    name: str
    cards: list[str] = field(default_factory=list)
    target: int = 0


@dataclass
class BuildError(Exception):
    message: str


@dataclass
class BuiltDeck:
    commander: str
    identity: list[str]
    groups: list[RoleGroup]
    decklist_text: str
    owned_used: int
    basics_added: int
    shortfalls: list[str]

    @property
    def total(self) -> int:
        return 1 + self.owned_used + self.basics_added


def classify_role(type_line: str | None, oracle_text: str | None) -> str:
    """Bucket a card into a deckbuilding role by simple type/text heuristics."""
    t = (type_line or "").lower()
    o = (oracle_text or "").lower()
    if "land" in t:
        return "Lands"
    if "add {" in o or "add one mana" in o or ("search your library for" in o and "land" in o):
        return "Ramp"
    if (
        "destroy target" in o
        or "exile target" in o
        or "destroy all" in o
        or "exile all" in o
        or ("deals" in o and "damage to" in o and "target" in o)
    ):
        return "Removal"
    if "draw" in o and "card" in o:
        return "Card draw"
    if "creature" in t:
        return "Creatures"
    return "Other"


async def find_commander(session: AsyncSession, name: str):
    """Resolve a commander by name to its (name, oracle_id, identity, type_line, oracle_text)."""
    row = (
        await session.execute(
            select(
                Card.name, Card.oracle_id, Card.color_identity, Card.type_line, Card.oracle_text
            )
            .where(func.lower(Card.name) == (name or "").strip().lower())
            .order_by(Card.released_at.desc().nulls_last())
            .limit(1)
        )
    ).first()
    return row


async def owned_commanders(session: AsyncSession) -> list[str]:
    """Names of owned legendary creatures, for the picker datalist."""
    rows = (
        await session.execute(
            select(Card.name)
            .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
            .where(Card.type_line.ilike("legendary%creature%"))
            .distinct()
            .order_by(Card.name)
        )
    ).all()
    return [r[0] for r in rows]


async def _owned_pool(session: AsyncSession, identity: set[str], exclude_oracle) -> list[PoolCard]:
    rows = (
        await session.execute(
            select(
                Card.name, Card.oracle_id, Card.color_identity, Card.type_line,
                Card.oracle_text, Card.cmc, Card.legalities,
            ).join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
        )
    ).all()
    seen: set = set()
    pool: list[PoolCard] = []
    for name, oracle, ci, type_line, oracle_text, cmc, legalities in rows:
        if not oracle or oracle in seen or oracle == exclude_oracle:
            continue
        if not set(ci or []) <= identity:
            continue
        if (legalities or {}).get("commander") not in _ALLOWED_LEGAL:
            continue
        seen.add(oracle)
        pool.append(
            PoolCard(name, cmc if cmc is not None else 99.0, type_line or "", oracle_text or "")
        )
    return pool


def _basics_for(identity: list[str], count: int) -> list[tuple[str, int]]:
    """Distribute ``count`` basic lands across the commander's colours (round-robin)."""
    colors = [c for c in identity if c in BASIC_BY_COLOR]
    if not colors:  # colourless commander — no coloured basics to add
        return []
    per = {BASIC_BY_COLOR[c]: 0 for c in colors}
    names = [BASIC_BY_COLOR[c] for c in colors]
    for i in range(count):
        per[names[i % len(names)]] += 1
    return [(n, q) for n, q in per.items() if q]


async def build_commander_deck(session: AsyncSession, commander_name: str) -> BuiltDeck:
    cmd = await find_commander(session, commander_name)
    if cmd is None:
        raise BuildError(f"No card named “{commander_name}” found.")
    name, oracle, ci, type_line, oracle_text = cmd
    tl = (type_line or "").lower()
    can_command = "legendary" in tl and (
        "creature" in tl or "can be your commander" in (oracle_text or "").lower()
    )
    if not can_command:
        raise BuildError(f"“{name}” isn't a legendary creature, so it can't be a commander.")

    identity = list(ci or [])
    pool = await _owned_pool(session, set(identity), oracle)

    buckets: dict[str, list[PoolCard]] = {r: [] for r in ROLES}
    for c in pool:
        buckets[classify_role(c.type_line, c.oracle_text)].append(c)
    for r in buckets:
        buckets[r].sort(key=lambda c: (c.cmc, c.name))  # low curve first

    groups: list[RoleGroup] = []
    shortfalls: list[str] = []
    chosen: list[str] = []

    # Non-land roles toward their targets.
    leftovers: list[PoolCard] = []
    for role, target in _TEMPLATE:
        take = buckets[role][:target]
        leftovers += buckets[role][target:]
        if len(take) < target:
            shortfalls.append(f"{role} {len(take)}/{target}")
        groups.append(RoleGroup(role, [c.name for c in take], target))
        chosen += [c.name for c in take]

    # Top up the non-land count toward (99 - LAND_TARGET) from leftover spells.
    nonland_goal = DECK_SIZE - LAND_TARGET
    leftovers.sort(key=lambda c: (c.cmc, c.name))
    extra = [c.name for c in leftovers][: max(0, nonland_goal - len(chosen))]
    if extra:
        groups[-1].cards.extend(extra)  # fold into "Other"
        chosen += extra

    # Lands: owned first, then basics to reach the land target.
    owned_lands = [c.name for c in buckets["Lands"][:LAND_TARGET]]
    basics = _basics_for(identity, max(0, LAND_TARGET - len(owned_lands)))
    basics_added = sum(q for _, q in basics)
    land_names = owned_lands + [f"{n} ×{q}" for n, q in basics]
    if len(owned_lands) + basics_added < LAND_TARGET:
        shortfalls.append(f"Lands {len(owned_lands) + basics_added}/{LAND_TARGET}")
    groups.insert(0, RoleGroup("Lands", land_names, LAND_TARGET))

    owned_used = len(chosen) + len(owned_lands)

    # Decklist text: commander first, then one line per owned card, then basics with quantities.
    lines = [f"1 {name}"]
    lines += [f"1 {n}" for n in chosen + owned_lands]
    lines += [f"{q} {n}" for n, q in basics]
    decklist_text = "\n".join(lines)

    return BuiltDeck(
        commander=name,
        identity=identity,
        groups=groups,
        decklist_text=decklist_text,
        owned_used=owned_used,
        basics_added=basics_added,
        shortfalls=shortfalls,
    )
