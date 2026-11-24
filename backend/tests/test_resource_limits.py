"""Bounds on what one request can cost: statement timeout, regex and restore limits, bad input."""

import base64
import json

import pytest
from sqlalchemy import text
from src import cryptobackup
from src.config import get_settings
from src.search.errors import SearchError
from src.search.parser import MAX_QUERY_LENGTH, parse

pytestmark = pytest.mark.asyncio


# --- statement timeout ----------------------------------------------------------------------------

async def test_request_sessions_carry_a_statement_timeout(monkeypatch):
    from src.db import get_session

    monkeypatch.setattr(get_settings(), "statement_timeout_ms", 1234)
    async for session in get_session():
        assert await session.scalar(text("SHOW statement_timeout")) == "1234ms"


async def test_statement_timeout_can_be_disabled(monkeypatch):
    from src.db import get_session

    monkeypatch.setattr(get_settings(), "statement_timeout_ms", 0)
    async for session in get_session():
        assert await session.scalar(text("SHOW statement_timeout")) == "0"


async def test_timeout_does_not_leak_past_the_request_transaction(monkeypatch):
    from src.db import get_session

    monkeypatch.setattr(get_settings(), "statement_timeout_ms", 1234)
    async for session in get_session():
        await session.commit()
        assert await session.scalar(text("SHOW statement_timeout")) == "0"


async def test_a_statement_that_overruns_is_stopped_with_a_503(client, monkeypatch):
    # Before: a slow search held a database backend until it finished, however long that took.
    from src.routes import health

    monkeypatch.setattr(get_settings(), "statement_timeout_ms", 50)

    from fastapi import Depends
    from src.db import get_session
    from src.main import app

    async def slow(session=Depends(get_session)):
        await session.execute(text("SELECT pg_sleep(2)"))
        return {"ok": True}

    app.add_api_route("/_test/slow", slow)
    try:
        resp = await client.get("/_test/slow")
    finally:
        app.router.routes[:] = [r for r in app.router.routes
                                if getattr(r, "path", "") != "/_test/slow"]
    assert resp.status_code == 503
    assert "too long" in resp.json()["detail"]
    assert health  # imported for parity with other route tests


# --- regular expressions --------------------------------------------------------------------------

async def test_backreference_regex_is_rejected(client):
    # ``o:/(.+)\1/`` ran for minutes at full CPU.
    resp = await client.get("/api/v1/search", params={"q": r"o:/(.+)\1/", "scope": "all"})
    assert resp.status_code == 400
    assert "Back-references" in resp.json()["detail"]


async def test_escaped_backslash_before_a_digit_is_not_a_backreference(client):
    resp = await client.get("/api/v1/search", params={"q": r"o:/a\\1/", "scope": "all"})
    assert resp.status_code == 200


async def test_overlong_regex_and_query_are_rejected(client):
    resp = await client.get("/api/v1/search", params={"q": "o:/" + "a" * 300 + "/"})
    assert resp.status_code == 400 and "too long" in resp.json()["detail"]
    with pytest.raises(SearchError):
        parse("a" * (MAX_QUERY_LENGTH + 1))


async def test_invalid_regex_is_a_400_not_a_500(client):
    resp = await client.get("/api/v1/search", params={"q": "o:/(/", "scope": "all"})
    assert resp.status_code == 400


async def test_ordinary_regex_still_works(client):
    resp = await client.get("/api/v1/search", params={"q": r"o:/draw (a|two) cards?/",
                                                      "scope": "all"})
    assert resp.status_code == 200


# --- restore --------------------------------------------------------------------------------------

def _envelope(iterations):
    return {"scryme_encrypted": 1, "kdf": "pbkdf2-sha256", "iterations": iterations,
            "salt": base64.b64encode(b"0" * 16).decode(), "ciphertext": "x"}


@pytest.mark.parametrize("iterations", [30_000_000, 2**31 - 1, 2**31, 2**63, 0, -1, "lots", None])
async def test_restore_refuses_attacker_chosen_kdf_cost(monkeypatch, iterations):
    # The count comes from the uploaded file. 30M froze the server for seconds; 2**31 panicked.
    called = []
    monkeypatch.setattr(cryptobackup, "_derive_key",
                        lambda *a, **k: called.append(a) or b"")
    with pytest.raises(cryptobackup.BackupDecryptError):
        cryptobackup.decrypt_backup(_envelope(iterations), "pw")
    assert called == []


async def test_own_encrypted_backups_still_round_trip():
    data = {"version": 1, "tables": {"box": []}}
    assert cryptobackup.decrypt_backup(cryptobackup.encrypt_backup(data, "pw"), "pw") == data
    assert cryptobackup.ITERATIONS <= cryptobackup.MAX_ITERATIONS


async def test_restore_upload_is_size_capped(client, monkeypatch):
    from src.routes import backup as backup_routes

    monkeypatch.setattr(backup_routes, "_max_restore_bytes", lambda: 1024)
    files = {"file": ("b.json", json.dumps({"pad": "x" * 4096}), "application/json")}
    resp = await client.post("/backup/restore", data={"mode": "preview"}, files=files)
    assert resp.status_code == 413


async def test_restore_preview_in_read_only_gets_a_smaller_cap(monkeypatch):
    from src.routes import backup as backup_routes

    normal = backup_routes._max_restore_bytes()
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert backup_routes._max_restore_bytes() < normal


# --- malformed input ------------------------------------------------------------------------------

@pytest.mark.parametrize("path", [
    "/card/not-a-uuid/image",
    "/decks/1/search-cards?q=a&limit=-1",
    "/decks/1/search-cards?q=a&limit=100000",
])
async def test_malformed_input_is_not_a_500(client, path):
    resp = await client.get(path, follow_redirects=False)
    assert resp.status_code < 500, (path, resp.status_code)


async def test_encrypted_backup_restores_through_the_route_layer(session):
    from src.backup import export_backup, restore_backup

    envelope = cryptobackup.encrypt_backup(await export_backup(session), "pw")
    result = await restore_backup(session, envelope, dry_run=True, passphrase="pw")
    assert result.ok
    wrong = await restore_backup(session, envelope, dry_run=True, passphrase="nope")
    assert not wrong.ok and wrong.needs_passphrase


# --- background jobs ------------------------------------------------------------------------------

async def test_finished_jobs_are_forgotten_past_the_cap(monkeypatch):
    from src import background_service as bg

    monkeypatch.setattr(bg, "jobs", {})
    monkeypatch.setattr(bg, "MAX_JOBS", 10)
    for i in range(25):
        bg.jobs[f"done-{i}"] = {"status": "completed", "result": "x", "type": "backfill-image"}
    bg.jobs["running"] = {"status": "processing", "result": None, "type": "ai_chat"}
    bg._prune_finished()
    assert len(bg.jobs) == 10
    assert "running" in bg.jobs and "done-0" not in bg.jobs and "done-24" in bg.jobs


async def test_ai_jobs_are_refused_when_the_queue_is_full(monkeypatch):
    from src import background_service as bg

    started = []
    monkeypatch.setattr(bg, "jobs", {})
    monkeypatch.setattr(bg, "MAX_PENDING_AI", 3)
    monkeypatch.setattr(bg.asyncio, "create_task", lambda coro: (coro.close(), started.append(1),
                                                                 _FakeTask())[2])
    for i in range(5):
        bg.queue_service(f"chat-{i}", "ai_chat")
    assert len(started) == 3
    assert [bg.jobs[f"chat-{i}"]["status"] for i in range(5)] == ["queued"] * 3 + ["failed"] * 2
    assert "Too many" in bg.jobs["chat-4"]["result"]
    # Other job kinds are unaffected by the AI cap.
    bg.queue_service("img", "backfill-image", [])
    assert bg.jobs["img"]["status"] == "queued"


class _FakeTask:
    def add_done_callback(self, _callback):
        pass


async def test_nl_search_stores_nothing_when_ai_is_off(client, session):
    # Every visitor's text used to be saved and a model job queued, AI configured or not.
    from sqlalchemy import func, select
    from src import background_service as bg
    from src.models.nlqcache import NLQCache

    before = len(bg.jobs)
    resp = await client.get("/search", params={"nl": "goblins that draw cards"})
    assert resp.status_code == 200
    assert await session.scalar(select(func.count()).select_from(NLQCache)) == 0
    assert len(bg.jobs) == before
