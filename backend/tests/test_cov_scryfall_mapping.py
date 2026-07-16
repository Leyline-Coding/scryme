"""Coverage tests for src/scryfall/mapping.py — every branch of the raw->column mapping."""

from __future__ import annotations

import datetime

from src.scryfall.mapping import card_to_columns, image_url


def test_minimal_card_defaults():
    # Only the required id; every optional field falls back to its default.
    cols = card_to_columns({"id": "abc"})
    assert cols["scryfall_id"] == "abc"
    assert cols["name"] == ""
    assert cols["set_code"] == ""  # (raw.get("set") or "").lower()
    assert cols["collector_number"] == ""
    assert cols["lang"] == "en"
    assert cols["mana_cost"] is None  # neither top-level nor faces
    assert cols["type_line"] is None
    assert cols["oracle_text"] is None
    assert cols["colors"] is None  # no colors, no faces -> _colors_union returns None
    assert cols["keywords"] is None  # empty keywords normalized to None
    assert cols["released_at"] is None
    assert cols["raw"] == {"id": "abc"}


def test_top_level_fields_win_over_faces():
    raw = {
        "id": "x", "name": "N", "set": "MH2", "set_name": "Modern Horizons 2",
        "colors": ["R"], "color_identity": ["R"], "mana_cost": "{R}",
        "type_line": "Instant", "oracle_text": "deal damage", "cmc": 1.0,
        "keywords": ["Haste"], "layout": "normal", "rarity": "common",
        "released_at": "2021-06-18",
        "card_faces": [{"oracle_text": "ignored", "mana_cost": "{G}"}],
    }
    cols = card_to_columns(raw)
    assert cols["set_code"] == "mh2"
    assert cols["oracle_text"] == "deal damage"  # top-level, not the face
    assert cols["mana_cost"] == "{R}"
    assert cols["keywords"] == ["Haste"]
    assert cols["released_at"] == datetime.date(2021, 6, 18)


def test_colors_union_from_faces_dedupes():
    raw = {
        "id": "y",
        "card_faces": [
            {"colors": ["U", "R"]},
            {"colors": ["R", "W"]},
        ],
    }
    cols = card_to_columns(raw)
    assert cols["colors"] == ["U", "R", "W"]  # unioned, order preserved, deduped


def test_face_join_multiple_parts():
    raw = {
        "id": "z",
        "card_faces": [
            {"oracle_text": "front text", "type_line": "Creature"},
            {"oracle_text": "back text", "type_line": "Land"},
        ],
    }
    cols = card_to_columns(raw)
    assert cols["oracle_text"] == "front text // back text"
    assert cols["type_line"] == "Creature // Land"


def test_bad_and_nonstring_date_is_none():
    assert card_to_columns({"id": "a", "released_at": "not-a-date"})["released_at"] is None
    assert card_to_columns({"id": "a", "released_at": 12345})["released_at"] is None  # TypeError
    assert card_to_columns({"id": "a", "released_at": None})["released_at"] is None


def test_image_url_top_level_and_size_fallback():
    raw = {"image_uris": {"normal": "http://x/normal.jpg", "small": "http://x/small.jpg"}}
    assert image_url(raw, "normal") == "http://x/normal.jpg"
    assert image_url(raw, "small") == "http://x/small.jpg"
    # Requested size missing at top level and no faces -> None.
    assert image_url(raw, "png") is None


def test_image_url_falls_back_to_face():
    raw = {
        "card_faces": [
            {"image_uris": {"normal": "http://x/front.jpg"}},
            {"image_uris": {"normal": "http://x/back.jpg"}},
        ]
    }
    assert image_url(raw, "normal") == "http://x/front.jpg"  # first face that has the size


def test_image_url_none_when_absent():
    assert image_url({}, "normal") is None
    assert image_url({"card_faces": [{"name": "no images"}]}, "normal") is None
