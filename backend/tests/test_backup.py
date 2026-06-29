"""Backup & restore: export shape, round-trip, FK skipping, dry-run, and routes."""

import json
import uuid

import pytest
from sqlalchemy import func, select
from src.backup import export_backup, restore_backup, validate_backup
from src.models import Card, CollectionCard, SavedSearch, WishlistItem
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
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=3, finish="foil",
                               binder_name="Box", tags=["trade"]))
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
    dl = await client.get("/backup/download")
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
