"""Dragon Shield parser + detection + matching tests."""

from pathlib import Path

import pytest
from src.importers.base import detect_format
from src.importers.dragonshield import DragonShieldImporter
from src.importers.manabox import ManaBoxImporter
from src.importers.matching import match_rows

from tests.seed_cards import seed_cards

FIXTURES = Path(__file__).parent / "fixtures"
DS_CSV = (FIXTURES / "dragonshield_sample.csv").read_text()
MB_CSV = (FIXTURES / "manabox_sample.csv").read_text()


def test_detect_dragonshield():
    assert detect_format(DS_CSV) is DragonShieldImporter
    # A ManaBox file is NOT claimed by Dragon Shield.
    assert detect_format(MB_CSV) is ManaBoxImporter


def test_parse_strips_sep_and_maps_fields():
    rows = DragonShieldImporter.parse(DS_CSV)
    assert len(rows) == 4

    lotus = rows[0]
    assert lotus.name == "Black Lotus"
    assert lotus.set_code == "lea"
    assert lotus.collector_number == "232"
    assert lotus.scryfall_id is None  # Dragon Shield has no Scryfall ID
    assert lotus.language == "en"  # English -> en
    assert lotus.source_location_name == "Vault"

    bolt = rows[1]
    assert bolt.finish == "foil"  # Printing=Foil
    assert bolt.quantity == 2


@pytest.mark.asyncio
async def test_match_by_set_and_number(session):
    await seed_cards(session)
    rows = DragonShieldImporter.parse(DS_CSV)
    matched = await match_rows(session, rows)
    by_name = {m.row.name: m for m in matched}

    assert by_name["Black Lotus"].method == "set_number"
    assert by_name["Llanowar Elves"].method == "set_number"
    assert by_name["Totally Fake DS Card"].method == "unmatched"
