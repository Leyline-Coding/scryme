"""On-disk backups: write, list/prune/resolve, restore-from-path, and the routes."""

import uuid

import pytest
from sqlalchemy import func, select
from src.backup import (
    list_backups,
    prune_backups,
    resolve_backup,
    restore_from_path,
    write_backup,
)
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _seed(session):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "tst",
           "collector_number": "1", "rarity": "rare", "prices": {"usd": "1.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=3, tags=["keep"]))
    await session.commit()
    return c


def _touch(directory, name):
    (directory / name).write_text('{"version": 1, "tables": {}}')


def test_list_and_prune(tmp_path):
    for n in ("scryme-backup-20260101-000000.json", "scryme-backup-20260102-000000.json",
              "scryme-backup-20260103-000000.json", "not-a-backup.json"):
        _touch(tmp_path, n)
    names = [b.name for b in list_backups(tmp_path)]
    assert names == ["scryme-backup-20260103-000000.json", "scryme-backup-20260102-000000.json",
                     "scryme-backup-20260101-000000.json"]  # newest first, non-backup ignored
    removed = prune_backups(tmp_path, keep=2)
    assert removed == 1
    assert [b.name for b in list_backups(tmp_path)] == [
        "scryme-backup-20260103-000000.json", "scryme-backup-20260102-000000.json"]


def test_resolve_backup_blocks_traversal(tmp_path):
    _touch(tmp_path, "scryme-backup-20260101-000000.json")
    assert resolve_backup(tmp_path, "scryme-backup-20260101-000000.json") is not None
    assert resolve_backup(tmp_path, "../secret.json") is None
    assert resolve_backup(tmp_path, "evil.txt") is None
    assert resolve_backup(tmp_path, "missing-scryme-backup-x.json") is None


@pytest.mark.asyncio
async def test_write_then_restore_from_path(session, tmp_path):
    await _seed(session)
    path = await write_backup(session, tmp_path)
    assert path.exists() and path.name.startswith("scryme-backup-")
    assert len(list_backups(tmp_path)) == 1

    await session.execute(CollectionCard.__table__.delete())
    await session.commit()
    assert await session.scalar(select(func.count()).select_from(CollectionCard)) == 0

    result = await restore_from_path(session, path, dry_run=False)
    assert result.ok and result.applied
    stack = (await session.execute(select(CollectionCard))).scalar_one()
    assert stack.quantity == 3 and stack.tags == ["keep"]


@pytest.mark.asyncio
async def test_disk_routes(client, session, tmp_path, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
    await _seed(session)

    # Back up now -> writes a file and redirects.
    made = await client.post("/backup/disk", follow_redirects=False)
    assert made.status_code == 303
    backups = list_backups(tmp_path)
    assert len(backups) == 1
    name = backups[0].name

    # The page lists it.
    page = await client.get("/backup")
    assert "Backups on disk" in page.text and name in page.text

    # Download it.
    dl = await client.get(f"/backup/disk/download?name={name}")
    assert dl.status_code == 200

    # Preview a restore from disk.
    preview = await client.post("/backup/disk/restore", data={"name": name, "mode": "preview"})
    assert preview.status_code == 200 and "Preview" in preview.text


@pytest.mark.asyncio
async def test_disk_backup_blocked_in_read_only(client, session, tmp_path, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.post("/backup/disk")
    assert resp.status_code == 403
