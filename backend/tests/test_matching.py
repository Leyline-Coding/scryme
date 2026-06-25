"""Card matching: Scryfall ID -> set+number -> name -> unmatched."""

from pathlib import Path

import pytest
from src.importers.manabox import ManaBoxImporter
from src.importers.matching import match_rows

from tests.seed_cards import seed_cards

FIXTURES = Path(__file__).parent / "fixtures"
CSV = (FIXTURES / "manabox_sample.csv").read_text()


@pytest.mark.asyncio
async def test_match_methods(session):
    await seed_cards(session)
    rows = ManaBoxImporter.parse(CSV)
    matched = await match_rows(session, rows)
    by_name = {m.row.name: m for m in matched}

    assert by_name["Black Lotus"].method == "scryfall_id"
    assert by_name["Lightning Bolt"].method == "scryfall_id"
    assert by_name["Llanowar Elves"].method == "set_number"  # blank id, set+cn matches
    assert by_name["Goblin Guide"].method == "name"  # bad id + wrong set, name matches
    assert by_name["Totally Fake Card"].method == "unmatched"
    assert by_name["Totally Fake Card"].scryfall_id is None


@pytest.mark.asyncio
async def test_unmatched_when_db_empty(session):
    rows = ManaBoxImporter.parse(CSV)
    matched = await match_rows(session, rows)
    assert all(not m.matched for m in matched)
