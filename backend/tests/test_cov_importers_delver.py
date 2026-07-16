"""Coverage for src/importers/delver.py — row with neither name nor Scryfall ID is skipped."""

from src.importers.delver import DelverImporter

_CSV = (
    "Name,Scryfall ID,Quantity,Set Code,Card Number,Foil,Condition,Language\n"
    "Black Lotus,,2,LEA,232,,NM,English\n"
    ",,1,MH2,122,,,\n"  # no name and no scryfall id -> skipped
)


def test_skips_rows_without_name_or_id():
    rows = DelverImporter.parse(_CSV)
    assert len(rows) == 1
    assert rows[0].name == "Black Lotus"
    assert rows[0].set_code == "lea"
    assert rows[0].collector_number == "232"


def test_matches_by_scryfall_id_only():
    csv = ("Name,Scryfall ID,Quantity\n"
           ",00000000-0000-0000-0000-0000000000b1,1\n")
    rows = DelverImporter.parse(csv)
    assert len(rows) == 1
    assert rows[0].name == ""
    assert rows[0].scryfall_id == "00000000-0000-0000-0000-0000000000b1"
