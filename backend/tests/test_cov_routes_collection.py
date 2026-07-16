"""Coverage tests for src/routes/collection.py: add/adjust/edit/remove/locate/grade/bulk/boxes."""

import uuid

import pytest
from sqlalchemy import select
from src.binder_service import create_binder
from src.box_service import create_box
from src.decks import create_deck
from src.models import Card, CollectionCard
from src.routes import collection as coll_route
from src.scryfall.mapping import card_to_columns


async def _card(session, *, oracle=None, name="Sol Ring", n=1):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(oracle or uuid.uuid4()), "name": name,
           "set": "tst", "collector_number": str(n), "rarity": "rare",
           "prices": {"usd": "1.00"}, "color_identity": ["R"]}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.commit()
    return c


async def _own(session, card, quantity=2, **kw):
    stack = CollectionCard(scryfall_id=card.scryfall_id, quantity=quantity, **kw)
    session.add(stack)
    await session.commit()
    await session.refresh(stack)
    return stack


# --- printing_options / location_choices helpers -------------------------------------------------

@pytest.mark.asyncio
async def test_printing_options_unknown_card_returns_empty(session):
    assert await coll_route.printing_options(session, uuid.uuid4()) == []


@pytest.mark.asyncio
async def test_printing_options_lists_siblings(session):
    oracle = uuid.uuid4()
    p1 = await _card(session, oracle=oracle, n=1)
    await _card(session, oracle=oracle, n=2)
    opts = await coll_route.printing_options(session, str(p1.scryfall_id))
    assert len(opts) == 2
    assert all("TST" in label for _, label in opts)


# --- add ----------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_add_route(client, session):
    c = await _card(session)
    resp = await client.post("/collection/add",
                             data={"scryfall_id": str(c.scryfall_id), "quantity": 3,
                                   "finish": "foil", "condition": "NM", "language": "en",
                                   "binder": "Box A", "location": "Shelf"})
    assert resp.status_code == 200
    assert "3 total" in resp.text


# --- adjust -------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_adjust_route_and_404(client, session):
    c = await _card(session)
    s = await _own(session, c, 2)
    ok = await client.post(f"/collection/stack/{s.id}/adjust", data={"delta": 1})
    assert ok.status_code == 200 and "3 total" in ok.text
    missing = await client.post("/collection/stack/999999/adjust", data={"delta": 1})
    assert missing.status_code == 404


# --- delete stack -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_stack_route_and_404(client, session):
    c = await _card(session)
    s = await _own(session, c, 2)
    ok = await client.post(f"/collection/stack/{s.id}/delete")
    assert ok.status_code == 200
    missing = await client.post("/collection/stack/999999/delete")
    assert missing.status_code == 404


# --- edit stack ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_stack_bad_quantity_falls_back(client, session):
    c = await _card(session)
    s = await _own(session, c, 2)
    resp = await client.post(f"/collection/stack/{s.id}/edit",
                             data={"card_id": str(c.scryfall_id), "quantity": "notanint",
                                   "finish": "foil"})
    assert resp.status_code == 200
    await session.refresh(s)
    assert s.quantity == 2 and s.finish == "foil"


@pytest.mark.asyncio
async def test_edit_stack_printing_change_redirects(client, session):
    oracle = uuid.uuid4()
    p1 = await _card(session, oracle=oracle, n=1)
    p2 = await _card(session, oracle=oracle, n=2)
    s = await _own(session, p1, 1)
    resp = await client.post(f"/collection/stack/{s.id}/edit",
                             data={"card_id": str(p1.scryfall_id),
                                   "printing": str(p2.scryfall_id)},
                             follow_redirects=False)
    assert resp.headers.get("HX-Redirect") == f"/card/{p2.scryfall_id}"


# --- remove modal -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_modal_with_oracle(client, session):
    oracle = uuid.uuid4()
    p1 = await _card(session, oracle=oracle, n=1)
    await _own(session, p1, 2)
    resp = await client.get(f"/card/{p1.scryfall_id}/remove-modal")
    assert resp.status_code == 200 and "Remove from collection" in resp.text


@pytest.mark.asyncio
async def test_remove_modal_no_oracle(client, session):
    # A card with no oracle_id takes the non-oracle branch.
    raw = {"id": str(uuid.uuid4()), "name": "Token", "set": "tst", "collector_number": "9",
           "rarity": "common"}
    cols = card_to_columns(raw)
    cols["oracle_id"] = None
    c = Card(**cols)
    session.add(c)
    await session.commit()
    await _own(session, c, 1)
    resp = await client.get(f"/card/{c.scryfall_id}/remove-modal")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_remove_modal_404(client):
    resp = await client.get(f"/card/{uuid.uuid4()}/remove-modal")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_remove_route(client, session):
    c = await _card(session)
    s = await _own(session, c, 4)
    resp = await client.post("/collection/remove",
                             data={"card_id": str(c.scryfall_id), "stack_id": str(s.id),
                                   "count": "4"})
    assert resp.status_code == 200
    from sqlalchemy import func
    remaining = await session.scalar(
        select(func.count()).select_from(CollectionCard).where(CollectionCard.id == s.id))
    assert remaining == 0


# --- locate stack -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_locate_stack_box(client, session):
    c = await _card(session)
    s = await _own(session, c, 1)
    await create_box(session, "Long Box")
    resp = await client.post(f"/collection/stack/{s.id}/locate",
                             data={"location_choice": "box:Long Box"})
    assert resp.status_code == 200
    await session.refresh(s)
    assert s.location == "Long Box"


@pytest.mark.asyncio
async def test_locate_stack_binder(client, session):
    c = await _card(session)
    s = await _own(session, c, 1)
    b = await create_binder(session, "Binder One")
    resp = await client.post(f"/collection/stack/{s.id}/locate",
                             data={"location_choice": f"binder:{b.id}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_locate_stack_deck(client, session):
    c = await _card(session)
    s = await _own(session, c, 1)
    d = await create_deck(session, "My Deck", "")
    resp = await client.post(f"/collection/stack/{s.id}/locate",
                             data={"location_choice": f"deck:{d.id}"})
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_locate_stack_unfile(client, session):
    c = await _card(session)
    s = await _own(session, c, 1, location="Old Spot")
    resp = await client.post(f"/collection/stack/{s.id}/locate",
                             data={"location_choice": ""})
    assert resp.status_code == 200
    await session.refresh(s)
    assert s.location is None


@pytest.mark.asyncio
async def test_locate_stack_404(client):
    resp = await client.post("/collection/stack/999999/locate",
                             data={"location_choice": "box:X"})
    assert resp.status_code == 404


# --- grade --------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_grade_stack_with_photo_and_bad_override(client, session):
    c = await _card(session)
    s = await _own(session, c, 1)
    resp = await client.post(f"/collection/stack/{s.id}/grade",
                             data={"company": "PSA", "grade": "10", "cert": "1",
                                   "value_override": "notafloat"},
                             files={"photo": ("slab.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    assert resp.status_code == 200
    await session.refresh(s)
    assert s.grade_company == "PSA" and s.grade_photo
    assert s.value_override is None  # bad float -> None


@pytest.mark.asyncio
async def test_grade_stack_404(client):
    resp = await client.post("/collection/stack/999999/grade", data={"company": "PSA"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_grade_clear_route_and_404(client, session):
    c = await _card(session)
    s = await _own(session, c, 1)
    await client.post(f"/collection/stack/{s.id}/grade",
                      data={"company": "PSA", "grade": "10", "cert": "", "value_override": ""})
    ok = await client.post(f"/collection/stack/{s.id}/grade/clear")
    assert ok.status_code == 200
    await session.refresh(s)
    assert s.grade_company is None
    missing = await client.post("/collection/stack/999999/grade/clear")
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_grade_photo_served(client, session):
    c = await _card(session)
    s = await _own(session, c, 1)
    await client.post(f"/collection/stack/{s.id}/grade",
                      data={"company": "PSA", "grade": "10", "cert": "", "value_override": ""},
                      files={"photo": ("slab.png", b"\x89PNG\r\n\x1a\n", "image/png")})
    await session.refresh(s)
    resp = await client.get(f"/grades/{s.grade_photo}")
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_grade_photo_not_found(client):
    resp = await client.get("/grades/nonexistent.png")
    assert resp.status_code == 404


# --- bulk ---------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_bulk_tag(client, session):
    c = await _card(session)
    await _own(session, c, 1)
    resp = await client.post("/collection/bulk",
                             data={"bulk_action": "tag", "scryfall_ids": [str(c.scryfall_id)],
                                   "tag": "trade", "q": "x"}, follow_redirects=False)
    assert resp.status_code == 303


@pytest.mark.asyncio
async def test_bulk_binder(client, session):
    c = await _card(session)
    b = await create_binder(session, "B")
    resp = await client.post("/collection/bulk",
                             data={"bulk_action": "binder", "scryfall_ids": [str(c.scryfall_id)],
                                   "binder_id": str(b.id)}, follow_redirects=False)
    assert resp.status_code == 303


@pytest.mark.asyncio
async def test_bulk_add(client, session):
    c = await _card(session)
    resp = await client.post("/collection/bulk",
                             data={"bulk_action": "add", "scryfall_ids": [str(c.scryfall_id)]},
                             follow_redirects=False)
    assert resp.status_code == 303
    assert await session.scalar(
        select(CollectionCard.id).where(CollectionCard.scryfall_id == c.scryfall_id))


@pytest.mark.asyncio
async def test_bulk_no_ids(client):
    resp = await client.post("/collection/bulk", data={"bulk_action": "add"},
                             follow_redirects=False)
    assert resp.status_code == 303


# --- locations redirect + boxes -----------------------------------------------------------------

@pytest.mark.asyncio
async def test_locations_redirect(client):
    resp = await client.get("/collection/locations", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/collection?tab=locations"


@pytest.mark.asyncio
async def test_box_new_rename_delete(client, session):
    from src.models.box import Box
    new = await client.post("/collection/boxes/new", data={"name": "New Box"},
                            follow_redirects=False)
    assert new.status_code == 303
    box_id = await session.scalar(select(Box.id).where(Box.name == "New Box"))
    ren = await client.post(f"/collection/boxes/{box_id}/rename", data={"name": "Renamed"},
                            follow_redirects=False)
    assert ren.status_code == 303
    dele = await client.post(f"/collection/boxes/{box_id}/delete", follow_redirects=False)
    assert dele.status_code == 303


@pytest.mark.asyncio
async def test_organize_by_identity(client, session):
    c = await _card(session)
    await _own(session, c, 1)
    resp = await client.post("/collection/organize-by-identity", follow_redirects=False)
    assert resp.status_code == 303
    await session.refresh(c)
    stack = await session.scalar(select(CollectionCard).where(
        CollectionCard.scryfall_id == c.scryfall_id))
    assert stack.location == "Red"


# --- read-only guards ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_read_only_blocks(client, session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    c = await _card(session)
    for url, data in [
        ("/collection/add", {"scryfall_id": str(c.scryfall_id)}),
        ("/collection/stack/1/adjust", {"delta": 1}),
        ("/collection/stack/1/delete", {}),
        ("/collection/stack/1/edit", {"card_id": str(c.scryfall_id)}),
        ("/collection/remove", {"card_id": str(c.scryfall_id)}),
        ("/collection/stack/1/locate", {"location_choice": ""}),
        ("/collection/stack/1/grade", {"company": "PSA"}),
        ("/collection/stack/1/grade/clear", {}),
        ("/collection/bulk", {"bulk_action": "add"}),
        ("/collection/boxes/new", {"name": "x"}),
        ("/collection/boxes/1/rename", {"name": "x"}),
        ("/collection/boxes/1/delete", {}),
        ("/collection/organize-by-identity", {}),
    ]:
        resp = await client.post(url, data=data)
        assert resp.status_code == 403, url
