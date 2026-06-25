"""Admin endpoint tests (status + ingest trigger), with ingestion mocked out."""

import pytest
import src.routes.admin as admin_mod
from src.config import get_settings


@pytest.mark.asyncio
async def test_status_reports_counts(client):
    resp = await client.get("/admin/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["card_count"] == 0
    assert body["ingest"]["status"] == "never"


@pytest.mark.asyncio
async def test_trigger_ingest_accepted(client, monkeypatch):
    called = {}

    async def fake_ingest(force=False):
        called["force"] = force

    monkeypatch.setattr(admin_mod, "ingest_default_cards", fake_ingest)
    resp = await client.post("/admin/ingest?force=true")
    assert resp.status_code == 202
    assert resp.json() == {"accepted": True, "force": True}
    assert called == {"force": True}


@pytest.mark.asyncio
async def test_trigger_ingest_blocked_in_read_only(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "read_only", True)
    resp = await client.post("/admin/ingest")
    assert resp.status_code == 403
