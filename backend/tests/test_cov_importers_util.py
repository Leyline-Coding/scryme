"""Unit coverage for src/importers/util.py shared helpers."""

from src.importers.util import normalize_finish, normalize_language, to_float, to_int


def test_to_int():
    assert to_int("5") == 5
    assert to_int(None) == 1        # default
    assert to_int("") == 1          # blank -> default
    assert to_int("abc") == 1       # ValueError -> default
    assert to_int("", default=0) == 0


def test_to_float():
    assert to_float("2.5") == 2.5
    assert to_float(None) is None
    assert to_float("") is None
    assert to_float("not-a-number") is None  # ValueError -> None


def test_normalize_language():
    assert normalize_language("English") == "en"
    assert normalize_language("Japanese") == "ja"
    assert normalize_language("chinese traditional") == "zht"
    assert normalize_language(None) == "en"
    assert normalize_language("Klingon") == "kl"   # unknown -> first two chars
    assert normalize_language("x") == "en"          # too short -> en


def test_normalize_finish():
    assert normalize_finish("Foil") == "foil"
    assert normalize_finish("etched") == "etched"
    assert normalize_finish("nonfoil") == "normal"
    assert normalize_finish("") == "normal"
    assert normalize_finish("yes") == "foil"        # Deckbox boolean
    assert normalize_finish("true") == "foil"
    assert normalize_finish("weird-value") == "normal"  # fallthrough default
