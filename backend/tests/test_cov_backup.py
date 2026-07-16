"""Coverage tests for src/backup.py — export/restore, JSON coercion, and on-disk backups."""

from __future__ import annotations

import datetime
import json
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from src.backup import (
    RestoreResult,
    _from_json,
    _to_json,
    export_backup,
    list_backups,
    prune_backups,
    resolve_backup,
    restore_backup,
    restore_from_path,
    take_disk_backup,
    validate_backup,
    write_backup,
)
from src.models import Card, CollectionCard, SavedSearch, WishlistItem
from src.scryfall.mapping import card_to_columns


async def _card(session, name="Aaa", n=1):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "tst",
           "collector_number": str(n), "rarity": "rare", "prices": {"usd": "1.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.commit()
    return c


async def _seed(session):
    c = await _card(session)
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=3, finish="foil",
                               binder_name="Box", tags=["trade"]))
    session.add(SavedSearch(name="reds", query="c:r", scope="all"))
    session.add(WishlistItem(scryfall_id=c.scryfall_id, quantity=2, note="want"))
    await session.commit()
    return c


# --- pure helpers --------------------------------------------------------------------------------

def test_to_json_coerces_types():
    u = uuid.uuid4()
    assert _to_json(u) == str(u)
    assert _to_json(datetime.date(2026, 1, 2)) == "2026-01-02"
    assert _to_json(datetime.datetime(2026, 1, 2, 3, 4)) == "2026-01-02T03:04:00"
    assert _to_json(5) == 5
    assert _to_json("s") == "s"


def test_from_json_coerces_by_column_type():
    cols = {c.name: c for c in CollectionCard.__table__.columns}
    assert _from_json(cols["scryfall_id"], None) is None
    u = uuid.uuid4()
    assert _from_json(cols["scryfall_id"], str(u)) == u  # PGUUID
    got = _from_json(cols["added_at"], "2026-01-02T03:04:00")  # DateTime
    assert isinstance(got, datetime.datetime)
    assert _from_json(cols["quantity"], 4) == 4  # passthrough


def test_from_json_date_column():
    cols = {c.name: c for c in Card.__table__.columns}
    got = _from_json(cols["released_at"], "2021-06-18")  # Date
    assert got == datetime.date(2021, 6, 18)


def test_validate_backup_branches():
    assert validate_backup({"version": 1, "tables": {}}) is None
    assert validate_backup("nope") is not None
    assert validate_backup({"version": 99, "tables": {}}) is not None
    assert validate_backup({"version": 1}) is not None  # missing tables
    assert validate_backup({"version": 1, "tables": []}) is not None  # tables not a dict


def test_restore_result_total():
    r = RestoreResult(ok=True, counts={"a": 2, "b": 3})
    assert r.total == 5


# --- export / restore ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_export_round_trips(session):
    c = await _seed(session)
    data = json.loads(json.dumps(await export_backup(session)))
    assert data["version"] == 1
    assert data["tables"]["collection_card"][0]["tags"] == ["trade"]

    await session.execute(CollectionCard.__table__.delete())
    await session.execute(WishlistItem.__table__.delete())
    await session.execute(SavedSearch.__table__.delete())
    await session.commit()

    result = await restore_backup(session, data, dry_run=False)
    assert result.ok and result.applied and result.counts["collection_card"] == 1
    stack = (await session.execute(select(CollectionCard))).scalar_one()
    assert stack.quantity == 3 and str(stack.scryfall_id) == str(c.scryfall_id)


@pytest.mark.asyncio
async def test_restore_dry_run_and_missing_card_skip(session):
    ghost = str(uuid.uuid4())
    data = {"version": 1, "tables": {
        "collection_card": [{"id": 1, "scryfall_id": ghost, "quantity": 1}],
        "wishlist": [{"id": 1, "scryfall_id": ghost, "quantity": 1}],
    }}
    preview = await restore_backup(session, data, dry_run=True)
    assert preview.ok and not preview.applied
    assert preview.skipped_missing_cards == 2
    assert preview.counts.get("collection_card", 0) == 0


@pytest.mark.asyncio
async def test_restore_invalid_backup(session):
    bad = await restore_backup(session, {"version": 99, "tables": {}}, dry_run=True)
    assert not bad.ok and bad.error


@pytest.mark.asyncio
async def test_restore_encrypted_wrong_passphrase(session):
    from src.cryptobackup import encrypt_backup

    env = encrypt_backup({"version": 1, "tables": {}}, "right")
    res = await restore_backup(session, env, dry_run=True, passphrase="wrong")
    assert not res.ok and res.needs_passphrase


# --- on-disk backups -----------------------------------------------------------------------------

def test_list_prune_resolve(tmp_path):
    assert list_backups(tmp_path / "missing") == []  # not a dir -> []
    for n in ("scryme-backup-20260101-000000.json", "scryme-backup-20260102-000000.json",
              "scryme-backup-20260103-000000.json", "not-a-backup.json"):
        (tmp_path / n).write_text('{"version": 1, "tables": {}}')
    names = [b.name for b in list_backups(tmp_path)]
    assert names[0] == "scryme-backup-20260103-000000.json" and len(names) == 3

    assert resolve_backup(tmp_path, names[0]) is not None
    assert resolve_backup(tmp_path, "../evil.json") is None
    assert resolve_backup(tmp_path, "not-a-backup.json") is None  # wrong prefix
    assert resolve_backup(tmp_path, "scryme-backup-nope.json") is None  # missing file

    assert prune_backups(tmp_path, keep=1) == 2
    assert len(list_backups(tmp_path)) == 1


@pytest.mark.asyncio
async def test_write_backup_plain_and_prune(session, tmp_path):
    await _seed(session)
    p1 = await write_backup(session, tmp_path)
    assert p1.name.startswith("scryme-backup-") and p1.suffix == ".json"
    payload = json.loads(p1.read_text())
    assert payload["version"] == 1


@pytest.mark.asyncio
async def test_write_backup_encrypted(session, tmp_path):
    from src.cryptobackup import is_encrypted

    await _seed(session)
    path = await write_backup(session, tmp_path, keep=5, passphrase="pw")
    assert path.name.endswith(".enc.json")
    assert is_encrypted(json.loads(path.read_text()))


@pytest.mark.asyncio
async def test_take_disk_backup_opens_session(tmp_path):
    path = await take_disk_backup(tmp_path)
    assert path.exists() and path.name.startswith("scryme-backup-")


@pytest.mark.asyncio
async def test_restore_from_path_success_and_bad_file(session, tmp_path):
    await _seed(session)
    path = await write_backup(session, tmp_path)
    await session.execute(CollectionCard.__table__.delete())
    await session.commit()

    result = await restore_from_path(session, path, dry_run=False)
    assert result.ok and result.applied
    assert await session.scalar(select(func.count()).select_from(CollectionCard)) == 1

    # Unreadable / non-JSON file -> reported, not raised.
    junk = tmp_path / "junk.json"
    junk.write_text("not json{")
    bad = await restore_from_path(session, junk, dry_run=True)
    assert not bad.ok and bad.error

    missing = await restore_from_path(session, Path(tmp_path / "does-not-exist.json"))
    assert not missing.ok  # OSError path
