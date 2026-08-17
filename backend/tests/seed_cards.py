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
    # A double-faced card: Scryfall names it "Front // Back", but exports usually write "Front".
    {"id": "00000000-0000-0000-0000-0000000000d1",
     "name": "Delver of Secrets // Insectile Aberration", "set": "ISD", "collector_number": "51",
     "rarity": "common", "cmc": 1, "layout": "transform", "released_at": "2011-09-30",
     "legalities": {"modern": "legal", "standard": "not_legal"},
     "card_faces": [
         {"name": "Delver of Secrets", "type_line": "Creature — Human Wizard", "colors": ["U"]},
         {"name": "Insectile Aberration", "type_line": "Creature — Human Insect",
          "colors": ["U"]},
     ]},
    # Art-series printing of the same card: named "Name // Name" and not_legal everywhere, so the
    # front-face fallback must rank it below the real printing.
    {"id": "00000000-0000-0000-0000-0000000000d2",
     "name": "Delver of Secrets // Delver of Secrets", "set": "SLD", "collector_number": "999",
     "rarity": "common", "layout": "art_series", "released_at": "2021-01-01",
     "legalities": {"modern": "not_legal", "standard": "not_legal"},
     "card_faces": [
         {"name": "Delver of Secrets"}, {"name": "Delver of Secrets"},
     ]},
]


async def seed_cards(session) -> None:
    for raw in _CARDS:
        session.add(Card(**card_to_columns(raw)))
    await session.commit()
