"""Coverage for src/importers/manabox.py helper error paths + blank-name skip."""

from src.importers.manabox import ManaBoxImporter

# Quantity "abc" -> _to_int ValueError -> default 1; Purchase price "xyz" -> _to_float None;
# second row has a blank Name and must be skipped.
_CSV = (
    "Name,Set code,Collector number,Foil,Quantity,Scryfall ID,Purchase price,"
    "Condition,Language,Binder Name\n"
    "Black Lotus,LEA,232,normal,abc,,xyz,NM,English,Vault\n"
    ",MH2,122,foil,2,,3.00,,,\n"
)


def test_helper_error_paths_and_blank_name_skip():
    rows = ManaBoxImporter.parse(_CSV)
    assert len(rows) == 1  # blank-name row dropped
    lotus = rows[0]
    assert lotus.name == "Black Lotus"
    assert lotus.quantity == 1          # "abc" fell back to default
    assert lotus.purchase_price is None  # "xyz" fell back to None
    assert lotus.set_code == "lea"
    assert lotus.binder_name == "Vault"


def test_finish_normalization():
    csv = ("Name,Foil,Scryfall ID,ManaBox ID\n"
           "A,foil,,1\nB,etched,,2\nC,weird,,3\nD,,,,\n")
    rows = ManaBoxImporter.parse(csv)
    assert [r.finish for r in rows] == ["foil", "etched", "normal", "normal"]
