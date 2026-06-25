"""Shared card seeding for importer tests (ids align with manabox_sample.csv)."""

from src.models import Card
from src.scryfall.mapping import card_to_columns

BLACK_LOTUS = "00000000-0000-0000-0000-0000000000b1"
LIGHTNING_BOLT = "00000000-0000-0000-0000-0000000000b2"

_CARDS = [
    {"id": BLACK_LOTUS, "name": "Black Lotus", "set": "LEA", "collector_number": "232",
     "rarity": "rare", "cmc": 0, "type_line": "Artifact"},
    {"id": LIGHTNING_BOLT, "name": "Lightning Bolt", "set": "MH2", "collector_number": "122",
     "rarity": "uncommon", "cmc": 1, "type_line": "Instant", "colors": ["R"]},
    {"id": "00000000-0000-0000-0000-0000000000e1", "name": "Llanowar Elves", "set": "M19",
     "collector_number": "314", "rarity": "common", "cmc": 1, "type_line": "Creature — Elf Druid",
     "colors": ["G"]},
    {"id": "00000000-0000-0000-0000-0000000000a1", "name": "Goblin Guide", "set": "ZEN",
     "collector_number": "145", "rarity": "rare", "cmc": 1, "type_line": "Creature — Goblin",
     "colors": ["R"], "released_at": "2010-04-23"},
]


async def seed_cards(session) -> None:
    for raw in _CARDS:
        session.add(Card(**card_to_columns(raw)))
    await session.commit()
