"""ManaBox parser + format detection unit tests."""

from pathlib import Path

from src.importers.base import detect_format
from src.importers.manabox import ManaBoxImporter

FIXTURES = Path(__file__).parent / "fixtures"
CSV = (FIXTURES / "manabox_sample.csv").read_text()


def test_detect_manabox():
    assert detect_format(CSV) is ManaBoxImporter


def test_detect_rejects_unknown():
    assert detect_format("Some,Other,Header\n1,2,3\n") is None


def test_parse_rows():
    rows = ManaBoxImporter.parse(CSV)
    assert len(rows) == 5

    lotus = rows[0]
    assert lotus.name == "Black Lotus"
    assert lotus.set_code == "lea"  # lowercased
    assert lotus.collector_number == "232"
    assert lotus.scryfall_id == "00000000-0000-0000-0000-0000000000b1"
    assert lotus.quantity == 1

    bolt = rows[1]
    assert bolt.finish == "foil"
    assert bolt.quantity == 2
    assert bolt.binder_name == "Spells"

    elves = rows[2]
    assert elves.scryfall_id is None  # blank Scryfall ID column
    assert elves.quantity == 3
