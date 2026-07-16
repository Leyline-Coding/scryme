"""Coverage for src/importers/archidekt.py — blank-name skip."""

from src.importers.archidekt import ArchidektImporter

_CSV = (
    "Quantity,Name,Finish,Condition,Date Added,Language,Purchase Price,Tags,Edition Name,"
    "Edition Code,Multiverse Id,Scryfall ID,MTGO ID,Collector Number\n"
    "1,Black Lotus,Normal,NM,,English,5,,Alpha,LEA,,,,232\n"
    "1,,Foil,,,,,,,MH2,,,,122\n"  # blank name -> skipped
)


def test_blank_name_skipped():
    rows = ArchidektImporter.parse(_CSV)
    assert len(rows) == 1
    assert rows[0].name == "Black Lotus"
    assert rows[0].set_code == "lea"
    assert rows[0].collector_number == "232"
    assert rows[0].finish == "normal"
