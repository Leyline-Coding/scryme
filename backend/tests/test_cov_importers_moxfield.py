"""Coverage for src/importers/moxfield.py — blank-name skip."""

from src.importers.moxfield import MoxfieldImporter

_CSV = (
    "Count,Tradelist Count,Name,Edition,Condition,Language,Foil,Tags,Last Modified,"
    "Collector Number,Alter,Proxy,Purchase Price\n"
    "1,0,Black Lotus,LEA,NM,English,,,,232,False,False,5\n"
    "1,0,,MH2,,,,,,122,,,\n"  # blank name -> skipped
)


def test_blank_name_skipped():
    rows = MoxfieldImporter.parse(_CSV)
    assert len(rows) == 1
    assert rows[0].name == "Black Lotus"
    assert rows[0].set_code == "lea"
    assert rows[0].collector_number == "232"
    assert rows[0].purchase_price == 5.0
