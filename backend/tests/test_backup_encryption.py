"""Encrypted backups: round-trip, wrong-passphrase failure, and restore integration."""

import json
import uuid

import pytest
from sqlalchemy import func, select
from src.backup import restore_backup, write_backup
from src.cryptobackup import (
    BackupDecryptError,
    decrypt_backup,
    encrypt_backup,
    is_encrypted,
)
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


def test_encrypt_decrypt_round_trip():
    data = {"version": 1, "tables": {"deck": [{"id": 1, "name": "Burn"}]}}
    env = encrypt_backup(data, "hunter2")
    assert is_encrypted(env) and "ciphertext" in env
    assert "Burn" not in json.dumps(env)  # plaintext isn't visible
    assert decrypt_backup(env, "hunter2") == data


def test_wrong_passphrase_fails_cleanly():
    env = encrypt_backup({"version": 1, "tables": {}}, "right")
    with pytest.raises(BackupDecryptError):
        decrypt_backup(env, "wrong")
    with pytest.raises(BackupDecryptError):
        decrypt_backup(env, "")  # missing passphrase


def test_is_encrypted():
    assert not is_encrypted({"version": 1, "tables": {}})
    assert is_encrypted({"scryme_encrypted": 1})


async def _seed(session):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "Aaa", "set": "tst",
           "collector_number": "1", "rarity": "rare", "prices": {"usd": "1.00"}}
    c = Card(**card_to_columns(raw))
    session.add(c)
    await session.flush()
    session.add(CollectionCard(scryfall_id=c.scryfall_id, quantity=4, tags=["keep"]))
    await session.commit()


@pytest.mark.asyncio
async def test_write_encrypted_then_restore(session, tmp_path):
    await _seed(session)
    path = await write_backup(session, tmp_path, passphrase="s3cret")
    assert path.name.endswith(".enc.json")
    assert is_encrypted(json.loads(path.read_text()))  # on disk it's an envelope

    await session.execute(CollectionCard.__table__.delete())
    await session.commit()

    data = json.loads(path.read_text())
    # Wrong/missing passphrase is reported, not applied.
    bad = await restore_backup(session, data, dry_run=False, passphrase="nope")
    assert not bad.ok and bad.needs_passphrase
    assert await session.scalar(select(func.count()).select_from(CollectionCard)) == 0

    good = await restore_backup(session, data, dry_run=False, passphrase="s3cret")
    assert good.ok and good.applied
    stack = (await session.execute(select(CollectionCard))).scalar_one()
    assert stack.quantity == 4 and stack.tags == ["keep"]


@pytest.mark.asyncio
async def test_download_and_restore_encrypted_via_routes(client, session):
    await _seed(session)
    dl = await client.post("/backup/download", data={"passphrase": "pw"})
    assert dl.status_code == 200
    payload = json.loads(dl.text)
    assert is_encrypted(payload)

    files = {"file": ("b.enc.json", dl.text, "application/json")}
    # No passphrase -> prompt; correct passphrase -> preview.
    miss = await client.post("/backup/restore", data={"mode": "preview"}, files=files)
    assert "passphrase is required" in miss.text or "encrypted" in miss.text.lower()
    ok = await client.post("/backup/restore", data={"mode": "preview", "passphrase": "pw"},
                           files=files)
    assert "Preview" in ok.text
