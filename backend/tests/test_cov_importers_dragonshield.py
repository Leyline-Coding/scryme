"""Coverage for src/importers/dragonshield.py — no sep= line + blank-name skip."""

from src.importers.dragonshield import DragonShieldImporter

# No leading "sep=," line, so _strip_sep returns the text unchanged; second row blank name.
_CSV = (
    "Folder Name,Quantity,Trade Quantity,Card Name,Set Code,Card Number,Condition,"
    "Printing,Language,Price Bought\n"
    "Box A,3,0,Black Lotus,LEA,232,NearMint,Foil,English,10\n"
    ",1,0,,MH2,122,,,,\n"
)


def test_parse_without_sep_line_and_blank_name():
    rows = DragonShieldImporter.parse(_CSV)
    assert len(rows) == 1
    row = rows[0]
    assert row.name == "Black Lotus"
    assert row.set_code == "lea"
    assert row.collector_number == "232"
    assert row.finish == "foil"
    assert row.quantity == 3
    assert row.binder_name == "Box A"


def test_strip_sep_line_still_parses():
    csv = "sep=,\n" + _CSV
    rows = DragonShieldImporter.parse(csv)
    assert [r.name for r in rows] == ["Black Lotus"]
