"""Card detail route tests: page render, owned/printings, 404s, and lazy rulings."""

import uuid

import pytest
from src.models import Card, CollectionCard
from src.routes import card as card_route
from src.scryfall.mapping import card_to_columns


async def _seed(session):
    oracle = str(uuid.uuid4())
    main = {"id": str(uuid.uuid4()), "oracle_id": oracle, "name": "Lightning Bolt", "set": "MH2",
            "collector_number": "122", "rarity": "uncommon", "cmc": 1, "type_line": "Instant",
            "colors": ["R"], "color_identity": ["R"], "released_at": "2021-06-18",
            "oracle_text": "Lightning Bolt deals 3 damage to any target.",
            "prices": {"usd": "2.50"}, "legalities": {"modern": "legal", "standard": "not_legal"},
            "scryfall_uri": "https://scryfall.test/bolt", "artist": "Christopher Rush"}
    other = {"id": str(uuid.uuid4()), "oracle_id": oracle, "name": "Lightning Bolt", "set": "LEA",
             "collector_number": "161", "rarity": "common", "cmc": 1, "type_line": "Instant",
             "colors": ["R"], "color_identity": ["R"], "released_at": "1993-08-05",
             "oracle_text": "Lightning Bolt deals 3 damage to any target."}
    main_card = Card(**card_to_columns(main))
    session.add(main_card)
    session.add(Card(**card_to_columns(other)))
    await session.flush()
    session.add(CollectionCard(scryfall_id=main_card.scryfall_id, quantity=3, finish="foil"))
    await session.commit()
    return main_card


@pytest.mark.asyncio
async def test_foil_etched_animation_by_treatment(client, session):
    # Foil/etched shimmer is a property of the printing, regardless of ownership (#9):
    # etched and foil-only printings shimmer; an ordinary nonfoil+foil card does not.
    def raw(name, finishes):
        return {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
                "set": "TST", "collector_number": "1", "type_line": "Creature", "cmc": 1,
                "finishes": finishes, "legalities": {},
                "image_uris": {"normal": "https://img.test/x.png", "small": "https://img.test/s.png"}}
    etched = Card(**card_to_columns(raw("Etchy", ["etched"])))
    foil_only = Card(**card_to_columns(raw("Foily", ["foil"])))
    normal = Card(**card_to_columns(raw("Normie", ["nonfoil", "foil"])))
    session.add_all([etched, foil_only, normal])
    await session.commit()
    assert "card-art etched " in (await client.get(f"/card/{etched.scryfall_id}")).text
    assert "card-art foil " in (await client.get(f"/card/{foil_only.scryfall_id}")).text
    body = (await client.get(f"/card/{normal.scryfall_id}")).text
    assert "card-art foil " not in body and "card-art etched " not in body


@pytest.mark.asyncio
async def test_card_page_renders(client, session):
    card = await _seed(session)
    resp = await client.get(f"/card/{card.scryfall_id}")
    assert resp.status_code == 200
    body = resp.text
    assert "Lightning Bolt" in body
    assert "deals 3 damage" in body
    assert "Christopher Rush" in body
    assert "In your collection" in body
    assert "3 total" in body  # owned quantity (now shown with the inline +/- controls)
    assert "Other printings" in body  # the LEA printing shares the oracle_id
    assert "Legalities" in body


async def _add(session, raw):
    raw.setdefault("id", str(uuid.uuid4()))
    raw.setdefault("oracle_id", str(uuid.uuid4()))
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.commit()
    return c


@pytest.mark.asyncio
async def test_transform_button_for_double_faced(client, session):
    card = await _add(session, {
        "name": "Delver of Secrets // Insectile Aberration", "set": "isd",
        "collector_number": "51", "rarity": "common", "layout": "transform",
        "type_line": "Creature — Human Wizard", "colors": ["U"], "color_identity": ["U"],
        "card_faces": [
            {"name": "Delver of Secrets", "image_uris": {"normal": "https://img.test/front.jpg"}},
            {"name": "Insectile Aberration", "image_uris": {"normal": "https://img.test/back.jpg"}},
        ],
    })
    body = (await client.get(f"/card/{card.scryfall_id}")).text
    assert "⇅ Transform" in body
    assert "https://img.test/back.jpg" in body   # back face available for the flip
    assert "⟳ Rotate" not in body


@pytest.mark.asyncio
async def test_rotate_button_for_planar_and_aftermath(client, session):
    plane = await _add(session, {
        "name": "Naar Isle", "set": "hop", "collector_number": "1", "rarity": "common",
        "layout": "planar", "type_line": "Plane — Dominaria",
        "image_uris": {"normal": "https://img.test/plane.jpg"},
    })
    body = (await client.get(f"/card/{plane.scryfall_id}")).text
    assert "⟳ Rotate" in body and "⇅ Transform" not in body

    aftermath = await _add(session, {
        "name": "Commit // Memory", "set": "akh", "collector_number": "211", "rarity": "rare",
        "layout": "split", "type_line": "Instant", "keywords": ["Aftermath"],
        "image_uris": {"normal": "https://img.test/after.jpg"},
    })
    body = (await client.get(f"/card/{aftermath.scryfall_id}")).text
    assert "⟳ Rotate" in body

    # Battles are stored with layout "transform" (detected by type line): both buttons appear.
    battle = await _add(session, {
        "name": "Invasion of Test", "set": "mom", "collector_number": "1", "rarity": "rare",
        "layout": "transform", "type_line": "Battle — Siege",
        "card_faces": [
            {"image_uris": {"normal": "https://img.test/front.jpg"}},
            {"image_uris": {"normal": "https://img.test/back.jpg"}},
        ],
    })
    body = (await client.get(f"/card/{battle.scryfall_id}")).text
    assert "⟳ Rotate" in body and "⇅ Transform" in body


@pytest.mark.asyncio
async def test_normal_card_has_no_flip_or_rotate(client, session):
    card = await _seed(session)
    body = (await client.get(f"/card/{card.scryfall_id}")).text
    assert "⇅ Transform" not in body and "⟳ Rotate" not in body


@pytest.mark.asyncio
async def test_footer_shows_version(client, session):
    from src import __version__
    card = await _seed(session)
    body = (await client.get(f"/card/{card.scryfall_id}")).text
    assert f"scryme v{__version__}" in body


@pytest.mark.asyncio
async def test_card_404(client):
    assert (await client.get("/card/not-a-uuid")).status_code == 404
    assert (await client.get("/card/00000000-0000-0000-0000-000000000000")).status_code == 404


@pytest.mark.asyncio
async def test_rulings_render_from_cache(client, session):
    card = await _seed(session)
    sid = str(card.scryfall_id)
    card_route._rulings_cache[sid] = [
        {"published_at": "2020-01-01", "comment": "A test ruling about timing."}
    ]
    try:
        resp = await client.get(f"/card/{sid}/rulings")
        assert resp.status_code == 200
        assert "A test ruling about timing." in resp.text
    finally:
        card_route._rulings_cache.pop(sid, None)


@pytest.mark.asyncio
async def test_rulings_missing_uri_degrades(client, session):
    # The seeded card has no rulings_uri, so the fetch path fails gracefully (no network).
    card = await _seed(session)
    card_route._rulings_cache.pop(str(card.scryfall_id), None)
    resp = await client.get(f"/card/{card.scryfall_id}/rulings")
    assert resp.status_code == 200
    assert "couldn't be loaded" in resp.text
