"""Coverage tests for src/routes/backup.py — download, upload restore, and on-disk routes."""

from __future__ import annotations

import json
import uuid

import pytest
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


@pytest.mark.asyncio
async def test_backup_page_renders(client, session, tmp_path, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
    page = await client.get("/backup")
    assert page.status_code == 200


@pytest.mark.asyncio
async def test_backup_page_without_dir(client, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", None)
    page = await client.get("/backup")
    assert page.status_code == 200  # empty backups list branch


@pytest.mark.asyncio
async def test_download_plain_and_encrypted(client, session):
    await _seed(session)
    dl = await client.post("/backup/download", data={})
    assert dl.status_code == 200
    assert "scryme-backup-" in dl.headers["content-disposition"]
    assert json.loads(dl.text)["version"] == 1

    enc = await client.post("/backup/download", data={"passphrase": "pw"})
    assert ".enc.json" in enc.headers["content-disposition"]
    assert "scryme_encrypted" in enc.text


@pytest.mark.asyncio
async def test_restore_upload_preview_and_bad_json(client, session):
    await _seed(session)
    payload = json.loads((await client.post("/backup/download", data={})).text)
    files = {"file": ("b.json", json.dumps(payload), "application/json")}
    preview = await client.post("/backup/restore", data={"mode": "preview"}, files=files)
    assert preview.status_code == 200 and "Preview" in preview.text

    # Invalid JSON upload -> error branch, no crash.
    bad = {"file": ("b.json", b"\xff\xfenot json", "application/json")}
    resp = await client.post("/backup/restore", data={"mode": "preview"}, files=bad)
    assert resp.status_code == 200 and "valid JSON" in resp.text


@pytest.mark.asyncio
async def test_restore_apply_blocked_read_only(client, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "read_only", True)
    files = {"file": ("b.json", json.dumps({"version": 1, "tables": {}}), "application/json")}
    resp = await client.post("/backup/restore", data={"mode": "apply"}, files=files)
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_disk_routes_full_flow(client, session, tmp_path, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
    await _seed(session)

    made = await client.post("/backup/disk", follow_redirects=False)
    assert made.status_code == 303
    from src.backup import list_backups

    name = list_backups(tmp_path)[0].name

    dl = await client.get(f"/backup/disk/download?name={name}")
    assert dl.status_code == 200

    preview = await client.post("/backup/disk/restore", data={"name": name, "mode": "preview"})
    assert preview.status_code == 200 and "Preview" in preview.text


@pytest.mark.asyncio
async def test_disk_download_not_found(client, tmp_path, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
    resp = await client.get("/backup/disk/download?name=scryme-backup-nope.json")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_disk_restore_not_found(client, tmp_path, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
    resp = await client.post("/backup/disk/restore",
                             data={"name": "scryme-backup-nope.json", "mode": "preview"})
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_disk_route_no_dir_configured(client, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", None)
    resp = await client.post("/backup/disk")
    assert resp.status_code == 404  # _backup_dir raises when unconfigured


@pytest.mark.asyncio
async def test_disk_backup_blocked_read_only(client, tmp_path, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.post("/backup/disk")
    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_disk_restore_apply_blocked_read_only(client, session, tmp_path, monkeypatch):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", tmp_path)
    await _seed(session)
    await client.post("/backup/disk", follow_redirects=False)
    from src.backup import list_backups

    name = list_backups(tmp_path)[0].name

    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.post("/backup/disk/restore", data={"name": name, "mode": "apply"})
    assert resp.status_code == 403
