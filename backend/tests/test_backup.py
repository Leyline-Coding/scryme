"""Backup & restore: export shape, round-trip, FK skipping, dry-run, and routes."""

import json
import uuid

import pytest
from sqlalchemy import func, select
from src.backup import export_backup, restore_backup, validate_backup
from src.models import Binder, Card, CollectionCard, Location, SavedSearch, WishlistItem
from src.scryfall.mapping import card_to_columns


async def _card(session, name="Aaa", n=1):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
           "collector_number": str(n), "rarity": "rare", "prices": {"usd": "1.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.commit()
    return c


async def _seed_user_data(session):
    c = await _card(session, "Aaa", 1)
    binder = Binder(name="Box")
    session.add(binder)
    await session.commit()  # Commit to ensure the binder is saved so that it gets an ID
    location_id = await session.scalar(
        select(Location.id).where((Location.type == "binder") & (Location.foreign_id == binder.id))
    )
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=3, finish="foil",
                               location=location_id, tags=["trade"]))
    session.add(SavedSearch(name="reds", query="c:r", scope="all"))
    session.add(WishlistItem(scryfall_id=c.scryfall_id, quantity=2, note="want"))
    await session.commit()
    return c


def test_validate_backup():
    assert validate_backup({"version": 1, "tables": {}}) is None
    assert validate_backup({"version": 99, "tables": {}}) is not None
    assert validate_backup("nope") is not None
    assert validate_backup({"version": 1}) is not None


@pytest.mark.asyncio
async def test_export_then_restore_round_trips(session):
    c = await _seed_user_data(session)
    data = json.loads(json.dumps(await export_backup(session)))  # ensure it's JSON-serializable
    assert data["version"] == 1
    assert len(data["tables"]["collection_card"]) == 1
    assert data["tables"]["collection_card"][0]["tags"] == ["trade"]

    # Wipe user data (card stays), then restore from the backup.
    await session.execute(CollectionCard.__table__.delete())
    await session.execute(WishlistItem.__table__.delete())
    await session.execute(SavedSearch.__table__.delete())
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(CollectionCard)) == 0

    result = await restore_backup(session, data, dry_run=False)
    assert result.ok and result.applied
    assert result.counts["collection_card"] == 1
    stack = (await session.execute(select(CollectionCard))).scalar_one()
    assert stack.quantity == 3 and stack.finish == "foil" and stack.tags == ["trade"]
    assert str(stack.scryfall_id) == str(c.scryfall_id)
    assert await session.scalar(select(WishlistItem.note)) == "want"


@pytest.mark.asyncio
async def test_restore_skips_rows_for_missing_cards(session):
    # A backup that references a card not present in this DB.
    ghost = str(uuid.uuid4())
    data = {
        "version": 1,
        "tables": {
            "collection_card": [
                {"id": 1, "scryfall_id": ghost, "quantity": 1, "finish": "normal",
                 "language": "en", "added_at": None}
            ],
            "wishlist": [{"id": 1, "scryfall_id": ghost, "quantity": 1, "added_at": None}],
        },
    }
    preview = await restore_backup(session, data, dry_run=True)
    assert preview.ok and not preview.applied
    assert preview.counts.get("collection_card", 0) == 0
    assert preview.skipped_missing_cards == 2


@pytest.mark.asyncio
async def test_restore_dry_run_does_not_write(session):
    await _seed_user_data(session)
    data = await export_backup(session)
    before = await session.scalar(select(func.count()).select_from(CollectionCard))
    result = await restore_backup(session, data, dry_run=True)
    assert result.ok and not result.applied
    # No truncation happened.
    assert await session.scalar(select(func.count()).select_from(CollectionCard)) == before


@pytest.mark.asyncio
async def test_backup_routes(client, session):
    await _seed_user_data(session)
    dl = await client.post("/backup/download", data={})
    assert dl.status_code == 200
    assert "attachment" in dl.headers["content-disposition"]
    payload = json.loads(dl.text)
    assert payload["version"] == 1

    # Preview via upload.
    files = {"file": ("backup.json", json.dumps(payload), "application/json")}
    preview = await client.post("/backup/restore", data={"mode": "preview"}, files=files)
    assert preview.status_code == 200
    assert "Preview" in preview.text and "Collection cards" in preview.text


@pytest.mark.asyncio
async def test_restore_blocked_in_read_only(client, session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    files = {"file": ("b.json", json.dumps({"version": 1, "tables": {}}), "application/json")}
    resp = await client.post("/backup/restore", data={"mode": "apply"}, files=files)
    assert resp.status_code == 403
    # Preview is still allowed (read-only safe).
    ok = await client.post("/backup/restore", data={"mode": "preview"}, files=files)
    assert ok.status_code == 200


# --- places: boxes, binders and the location rows that file a stack in one ------------------------

async def _place_of(session, stack) -> tuple[str, str] | None:
    """(type, name) of the place a stack is filed in."""
    from src.locations import get_location_name

    if stack.location is None:
        return None
    location = await session.get(Location, stack.location)
    return location.type, await get_location_name(session, location)


@pytest.mark.asyncio
async def test_backup_carries_places_so_a_restore_refiles_every_stack(session):
    from src.locations import find_or_create_location_by_name
    from src.models import Box

    filed, loose = await _card(session, "Filed", 1), await _card(session, "Loose", 2)
    box_a = await find_or_create_location_by_name(session, "Box A")
    session.add(CollectionCard(scryfall_id=filed.scryfall_id, quantity=2, location=box_a))
    session.add(CollectionCard(scryfall_id=loose.scryfall_id, quantity=1))
    await session.commit()
    data = json.loads(json.dumps(await export_backup(session)))
    assert [b["name"] for b in data["tables"]["box"]] == ["Box A"]
    assert len(data["tables"]["location"]) == 1

    # A different install: other boxes, other ids, nothing filed.
    for model in (CollectionCard, Location, Box):
        await session.execute(model.__table__.delete())
    session.add(Box(name="Someone else's box"))
    await session.commit()

    result = await restore_backup(session, data, dry_run=False)
    assert result.ok and result.applied
    stacks = {s.quantity: s for s in (await session.execute(select(CollectionCard))).scalars()}
    assert await _place_of(session, stacks[2]) == ("box", "Box A")
    assert stacks[1].location is None
    assert list(await session.scalars(select(Box.name))) == ["Box A"]  # replaced, not merged


def _legacy_backup(*stacks: dict) -> dict:
    """A backup as written before the location system: free-text places, no place tables."""
    return {"version": 1, "tables": {"collection_card": [
        {"id": i, "finish": "normal", "language": "en", **s} for i, s in enumerate(stacks, 1)
    ]}}


@pytest.mark.asyncio
async def test_restoring_a_pre_location_backup_files_stacks_by_name(session):
    from src.models import Box

    cards = [await _card(session, f"Card {n}", n) for n in range(1, 5)]
    session.add(Binder(name="Trades"))  # already in this database; the old backup has no binders
    await session.commit()
    data = _legacy_backup(
        {"scryfall_id": str(cards[0].scryfall_id), "quantity": 1, "location": "Shelf 3"},
        {"scryfall_id": str(cards[1].scryfall_id), "quantity": 2, "location": " shelf 3 "},
        {"scryfall_id": str(cards[2].scryfall_id), "quantity": 3, "location": "trades",
         "binder_name": "ManaBox Binder"},
        {"scryfall_id": str(cards[3].scryfall_id), "quantity": 4, "location": None},
    )

    result = await restore_backup(session, data, dry_run=False)
    assert result.ok and result.applied and result.counts["collection_card"] == 4
    stacks = {s.quantity: s for s in (await session.execute(select(CollectionCard))).scalars()}
    assert await _place_of(session, stacks[1]) == ("box", "Shelf 3")
    assert stacks[2].location == stacks[1].location  # same place, typed differently
    assert await _place_of(session, stacks[3]) == ("binder", "Trades")
    assert stacks[3].source_location_name == "ManaBox Binder"
    assert stacks[4].location is None
    assert list(await session.scalars(select(Box.name))) == ["Shelf 3"]
    assert list(await session.scalars(select(Binder.name))) == ["Trades"]  # left alone


@pytest.mark.asyncio
async def test_previewing_a_pre_location_backup_creates_nothing(session):
    from src.models import Box

    card = await _card(session, "Card", 1)
    data = _legacy_backup(
        {"scryfall_id": str(card.scryfall_id), "quantity": 1, "location": "Shelf"}
    )
    result = await restore_backup(session, data, dry_run=True)
    assert result.ok and not result.applied and result.counts["collection_card"] == 1
    assert list(await session.scalars(select(Box.name))) == []
    assert await session.scalar(select(func.count()).select_from(CollectionCard)) == 0


@pytest.mark.asyncio
async def test_a_location_id_from_another_database_is_unfiled_not_misfiled(session):
    """A backup with numeric places but no place tables can't say what the numbers meant."""
    card = await _card(session, "Card", 1)
    data = _legacy_backup({"scryfall_id": str(card.scryfall_id), "quantity": 1, "location": 42})
    result = await restore_backup(session, data, dry_run=False)
    assert result.ok and result.applied
    assert (await session.execute(select(CollectionCard))).scalar_one().location is None
