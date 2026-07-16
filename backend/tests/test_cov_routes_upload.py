"""Coverage for src/routes/upload.py error/guard branches."""

import pytest
import src.routes.upload as upload_mod


@pytest.mark.asyncio
async def test_undo_with_no_snapshot_redirects(client):
    # undo_last is a no-op when there's no snapshot; the route still redirects.
    resp = await client.post("/upload/undo", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/collection?tab=stats"


@pytest.mark.asyncio
async def test_file_too_large_shows_error(client, monkeypatch):
    monkeypatch.setattr(upload_mod, "MAX_UPLOAD_BYTES", 8)
    files = {"file": ("big.csv", b"x" * 64, "text/csv")}
    resp = await client.post("/upload", files=files)
    assert resp.status_code == 200
    assert "too large" in resp.text


@pytest.mark.asyncio
async def test_mapped_upload_without_name_shows_wizard_error(client):
    # Submitting the wizard without mapping the name column re-renders with an error.
    data = {"csv": "A,B\n1,2\n", "map_quantity": "A"}
    resp = await client.post("/upload/mapped", data=data)
    assert resp.status_code == 200
    assert "Map your columns" in resp.text


@pytest.mark.asyncio
async def test_confirm_bad_strategy_returns_400(client):
    resp = await client.post(
        "/upload/confirm", data={"token": "whatever", "strategy": "bogus"}
    )
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_confirm_expired_token_returns_404(client):
    resp = await client.post(
        "/upload/confirm", data={"token": "not-a-uuid", "strategy": "increment"}
    )
    assert resp.status_code == 404
