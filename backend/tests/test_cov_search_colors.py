"""Unit coverage for src/search/colors.py — color expression parsing."""

import pytest
from src.search.colors import ColorParseError, parse_colors


def test_special_colorless():
    assert parse_colors("c") == (set(), "colorless")
    assert parse_colors("colorless") == (set(), "colorless")


def test_special_multicolor():
    assert parse_colors("m") == (set(), "multicolor")
    assert parse_colors("multicolor") == (set(), "multicolor")
    assert parse_colors("multicolored") == (set(), "multicolor")


def test_full_color_name():
    assert parse_colors("white") == ({"w"}, "")
    assert parse_colors("Green") == ({"g"}, "")  # case-insensitive


def test_guild_and_shard_names():
    assert parse_colors("azorius") == ({"w", "u"}, "")
    assert parse_colors("bant") == ({"g", "w", "u"}, "")
    assert parse_colors("rainbow") == ({"w", "u", "b", "r", "g"}, "")


def test_letter_set():
    assert parse_colors("wu") == ({"w", "u"}, "")
    assert parse_colors(" RG ") == ({"r", "g"}, "")  # stripped + lowered


def test_unrecognized_raises():
    with pytest.raises(ColorParseError):
        parse_colors("xyz")
    with pytest.raises(ColorParseError):
        parse_colors("wp")  # p is not a WUBRG letter
