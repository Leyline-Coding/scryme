"""Card matching: Scryfall ID -> set+number -> name -> unmatched."""

from pathlib import Path

import pytest
from src.importers.base import ImportRow
from src.importers.manabox import ManaBoxImporter
from src.importers.matching import match_rows

from tests.seed_cards import BLACK_LOTUS, seed_cards

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


DFC = "00000000-0000-0000-0000-0000000000d1"
ART_SERIES = "00000000-0000-0000-0000-0000000000d2"


async def _match_names(session, *names: str):
    rows = [ImportRow(name=n) for n in names]
    return await match_rows(session, rows)


@pytest.mark.asyncio
async def test_name_match_is_case_insensitive(session):
    await seed_cards(session)
    (m,) = await _match_names(session, "bLaCk LoTuS")
    assert m.method == "name"
    assert m.scryfall_id == BLACK_LOTUS


@pytest.mark.asyncio
async def test_front_face_name_resolves_to_full_printing(session):
    """Exports write "Delver of Secrets"; Scryfall names it "Delver of Secrets // ..."."""
    await seed_cards(session)
    (m,) = await _match_names(session, "Delver of Secrets")
    assert m.method == "name_front_face"
    assert m.scryfall_id == DFC


@pytest.mark.asyncio
async def test_front_face_fallback_skips_art_series(session):
    """The art-series printing is newer but not_legal everywhere — playable ranks first."""
    await seed_cards(session)
    (m,) = await _match_names(session, "delver of secrets")
    assert m.scryfall_id == DFC
    assert m.scryfall_id != ART_SERIES


@pytest.mark.asyncio
async def test_full_dfc_name_still_matches_exactly(session):
    await seed_cards(session)
    (m,) = await _match_names(session, "Delver of Secrets // Insectile Aberration")
    assert m.method == "name"
    assert m.scryfall_id == DFC


@pytest.mark.asyncio
async def test_back_face_name_does_not_match(session):
    """Only the front face is a legitimate shorthand; a back-face name is ambiguous."""
    await seed_cards(session)
    (m,) = await _match_names(session, "Insectile Aberration")
    assert m.method == "unmatched"


@pytest.mark.asyncio
async def test_blank_names_are_not_queried(session):
    await seed_cards(session)
    (m,) = await _match_names(session, "")
    assert m.method == "unmatched"
