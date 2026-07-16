"""Coverage for src/importers/mapping.py — sep= handling, empty CSV, blank-name skip."""

from src.importers.mapping import csv_headers, parse_with_mapping


def test_csv_headers_honors_sep_line():
    headers = csv_headers("sep=,\nCard Name,Qty\nBlack Lotus,1\n")
    assert headers == ["Card Name", "Qty"]


def test_csv_headers_empty_after_sep_returns_none():
    # "sep=," with nothing after -> reader raises StopIteration -> None.
    assert csv_headers("sep=,\n") is None


def test_csv_headers_blank_is_none():
    assert csv_headers("   ") is None


def test_parse_with_mapping_honors_sep_line_and_skips_blank_name():
    csv = "sep=,\nCard Name,Qty,Set\nBlack Lotus,2,LEA\n,1,MH2\n"
    rows = parse_with_mapping(csv, {"name": "Card Name", "quantity": "Qty", "set_code": "Set"})
    assert len(rows) == 1  # blank-name row skipped
    assert rows[0].name == "Black Lotus"
    assert rows[0].quantity == 2
    assert rows[0].set_code == "lea"
