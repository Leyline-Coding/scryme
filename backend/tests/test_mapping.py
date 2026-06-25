"""Unit tests for raw Scryfall -> column mapping (no DB)."""

import json
from pathlib import Path

from src.scryfall.mapping import card_to_columns, image_url

FIXTURES = Path(__file__).parent / "fixtures"
CARDS = json.loads((FIXTURES / "scryfall_sample.json").read_text())
BLACK_LOTUS, BOLT, DELVER = CARDS


def test_simple_card_columns():
    cols = card_to_columns(BOLT)
    assert cols["name"] == "Lightning Bolt"
    assert cols["set_code"] == "mh2"  # lowercased
    assert cols["colors"] == ["R"]
    assert cols["cmc"] == 1.0
    assert cols["oracle_text"].startswith("Lightning Bolt deals 3")
    assert cols["released_at"].year == 2021
    assert cols["raw"] is BOLT


def test_double_faced_card_falls_back_to_faces():
    cols = card_to_columns(DELVER)
    # No top-level oracle_text / type_line / mana_cost -> joined from faces.
    assert " // " in cols["oracle_text"]
    assert "Flying" in cols["oracle_text"]
    assert cols["type_line"].startswith("Creature — Human Wizard //")
    assert cols["mana_cost"] == "{U}"  # second face has empty mana cost, filtered out
    assert cols["colors"] == ["U"]  # unioned from faces
    assert cols["keywords"] == ["Flying"]


def test_bad_date_is_none():
    cols = card_to_columns({**BOLT, "released_at": "not-a-date"})
    assert cols["released_at"] is None


def test_image_url_prefers_front_face():
    assert image_url(BLACK_LOTUS, "normal").endswith("black-lotus.jpg")
    assert image_url(DELVER, "normal").endswith("delver-front.jpg")  # front face
    assert image_url({"name": "x"}, "normal") is None
