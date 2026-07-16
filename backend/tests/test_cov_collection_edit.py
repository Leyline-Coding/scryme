"""Coverage tests for src/collection_edit.py: update_stack, edit merges, organize, summaries."""

import uuid

import pytest
from sqlalchemy import select
from src.collection_edit import (
    add_or_increment,
    adjust_quantity,
    bulk_add_tag,
    bulk_add_to_collection,
    color_identity_group,
    delete_stack,
    edit_stack,
    location_summary,
    organize_by_color_identity,
    owned_for_oracle,
    remove_copies,
    update_stack,
)
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _card(session, *, oracle=None, n=1, color_identity=None):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(oracle or uuid.uuid4()), "name": "Card",
           "set": "tst", "collector_number": str(n), "rarity": "rare",
           "prices": {"usd": "1.00"}, "color_identity": color_identity or []}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.commit()
    return c


# --- add_or_increment / adjust / delete ---------------------------------------------------------

@pytest.mark.asyncio
async def test_add_unknown_returns_none(session):
    assert await add_or_increment(session, uuid.uuid4(), 1) is None


@pytest.mark.asyncio
async def test_add_increments_existing_stack(session):
    c = await _card(session)
    s1 = await add_or_increment(session, c.scryfall_id, 2, finish="foil")
    s2 = await add_or_increment(session, c.scryfall_id, 3, finish="foil")
    assert s2.id == s1.id and s2.quantity == 5


@pytest.mark.asyncio
async def test_adjust_quantity_and_delete_at_zero(session):
    c = await _card(session)
    s = await add_or_increment(session, c.scryfall_id, 2)
    assert str(await adjust_quantity(session, s.id, 1)) == str(c.scryfall_id)
    await session.refresh(s)
    assert s.quantity == 3
    await adjust_quantity(session, s.id, -5)  # to zero -> deleted
    assert await session.get(CollectionCard, s.id) is None
    assert await adjust_quantity(session, 999999, 1) is None


@pytest.mark.asyncio
async def test_delete_stack(session):
    c = await _card(session)
    s = await add_or_increment(session, c.scryfall_id, 1)
    assert str(await delete_stack(session, s.id)) == str(c.scryfall_id)
    assert await delete_stack(session, 999999) is None


# --- update_stack -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_update_stack_all_fields(session):
    c = await _card(session)
    s = await add_or_increment(session, c.scryfall_id, 2)
    out = await update_stack(session, s.id, quantity=0, finish="foil", language="",
                             condition="NM", binder="B1", location="Shelf", tags=["a"])
    assert out.quantity == 1  # clamped to >= 1
    assert out.finish == "foil" and out.language == "en"
    assert out.condition == "NM" and out.binder_name == "B1" and out.location == "Shelf"
    assert out.tags == ["a"]


@pytest.mark.asyncio
async def test_update_stack_clear_with_none(session):
    c = await _card(session)
    s = await add_or_increment(session, c.scryfall_id, 1, condition="NM", binder="B")
    out = await update_stack(session, s.id, condition=None, binder=None, location=None, tags=[])
    assert out.condition is None and out.binder_name is None and out.tags is None


@pytest.mark.asyncio
async def test_update_stack_missing(session):
    assert await update_stack(session, 999999, quantity=1) is None


# --- edit_stack edge cases ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_edit_stack_missing(session):
    assert await edit_stack(session, 999999, quantity=1) is None


@pytest.mark.asyncio
async def test_edit_stack_unknown_target_printing(session):
    c = await _card(session)
    s = await add_or_increment(session, c.scryfall_id, 1)
    assert await edit_stack(session, s.id, scryfall_id=str(uuid.uuid4())) is None


@pytest.mark.asyncio
async def test_edit_stack_move_printing_no_collision(session):
    oracle = uuid.uuid4()
    p1 = await _card(session, oracle=oracle, n=1)
    p2 = await _card(session, oracle=oracle, n=2)
    s = await add_or_increment(session, p1.scryfall_id, 2)
    out = await edit_stack(session, s.id, quantity=4, scryfall_id=str(p2.scryfall_id))
    assert str(out.scryfall_id) == str(p2.scryfall_id) and out.quantity == 4


@pytest.mark.asyncio
async def test_owned_for_oracle(session):
    oracle = uuid.uuid4()
    p1 = await _card(session, oracle=oracle, n=1)
    p2 = await _card(session, oracle=oracle, n=2)
    await add_or_increment(session, p1.scryfall_id, 1)
    await add_or_increment(session, p2.scryfall_id, 1)
    owned = await owned_for_oracle(session, oracle)
    assert {str(c.scryfall_id) for _, c in owned} == {str(p1.scryfall_id), str(p2.scryfall_id)}


@pytest.mark.asyncio
async def test_bulk_add_and_tag(session):
    a = await _card(session, n=1)
    b = await _card(session, n=2)
    assert await bulk_add_to_collection(session,
                                        [str(a.scryfall_id), str(b.scryfall_id)], 1) == 2
    # An unknown id doesn't count.
    assert await bulk_add_to_collection(session, [str(uuid.uuid4())], 1) == 0
    assert await bulk_add_tag(session, [str(a.scryfall_id), str(b.scryfall_id)], "trade") == 2


@pytest.mark.asyncio
async def test_edit_stack_merge_keeps_tags(session):
    c = await _card(session)
    a = await add_or_increment(session, c.scryfall_id, 2, finish="normal")
    b = await add_or_increment(session, c.scryfall_id, 1, finish="foil")
    a.tags = ["x"]
    b.tags = ["y"]
    await session.commit()
    out = await edit_stack(session, a.id, finish="foil")
    assert out.quantity == 3
    assert set(out.tags) == {"x", "y"}


# --- remove_copies edge cases -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_remove_copies_skips_zero_and_missing(session):
    c = await _card(session)
    s = await add_or_increment(session, c.scryfall_id, 2)
    # count<=0 skipped, missing stack skipped, valid removes.
    assert await remove_copies(session, [(s.id, 0), (999999, 3), (s.id, 1)]) == 1
    # Nothing removed -> returns 0 (no commit path).
    assert await remove_copies(session, [(s.id, 0)]) == 0
    # Removing more than owned zeroes and deletes the stack.
    assert await remove_copies(session, [(s.id, 5)]) == 1
    assert await session.get(CollectionCard, s.id) is None


# --- color identity groups ----------------------------------------------------------------------

def test_color_identity_group_all_shapes():
    assert color_identity_group(None) == "Colorless"
    assert color_identity_group(["R"]) == "Red"
    assert color_identity_group(["W", "U"]) == "Azorius"
    assert color_identity_group(["W", "U", "B"]) == "Esper"
    assert color_identity_group(["W", "U", "B", "R"]) == "Four-color"
    assert color_identity_group(["W", "U", "B", "R", "G"]) == "Five-color"


@pytest.mark.asyncio
async def test_organize_by_color_identity(session):
    red = await _card(session, n=1, color_identity=["R"])
    colorless = await _card(session, n=2, color_identity=[])
    await add_or_increment(session, red.scryfall_id, 1)
    await add_or_increment(session, colorless.scryfall_id, 1)
    count = await organize_by_color_identity(session)
    assert count == 2
    locs = {s.location for s in (await session.execute(select(CollectionCard))).scalars().all()}
    assert locs == {"Red", "Colorless"}


# --- location summary ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_location_summary(session):
    c1 = await _card(session, n=1)
    c2 = await _card(session, n=2)
    await add_or_increment(session, c1.scryfall_id, 3, location="Box A")
    await add_or_increment(session, c2.scryfall_id, 2)  # unfiled
    summaries = await location_summary(session)
    by_loc = {s.location: (s.stacks, s.quantity) for s in summaries}
    assert by_loc["Box A"] == (1, 3)
    assert by_loc[None] == (1, 2)
