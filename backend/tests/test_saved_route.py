"""Saved-search route tests: create, overwrite, list, delete, and the read-only guard."""

import pytest
from sqlalchemy import func, select
from src.config import get_settings
from src.models import SavedSearch


async def _count(session):
    return await session.scalar(select(func.count()).select_from(SavedSearch))


@pytest.mark.asyncio
async def test_create_and_list(client, session):
    resp = await client.post(
        "/saved",
        data={"name": "Cheap whites", "q": "t:creature c:w mv<=2",
              "scope": "collection", "sort": "price", "dir": "asc"},
        follow_redirects=True,
    )
    assert resp.status_code == 200  # 303 -> followed to /search
    assert str(resp.url).startswith("http://test/search")
    assert await _count(session) == 1

    page = await client.get("/search")
    assert "Cheap whites" in page.text  # shows in the header menu


@pytest.mark.asyncio
async def test_same_name_overwrites(client, session):
    await client.post("/saved", data={"name": "Dup", "q": "first", "sort": "name"})
    await client.post("/saved", data={"name": "Dup", "q": "second", "sort": "mv"})
    assert await _count(session) == 1
    obj = await session.scalar(select(SavedSearch).where(SavedSearch.name == "Dup"))
    await session.refresh(obj)
    assert obj.query == "second" and obj.sort == "mv"


@pytest.mark.asyncio
async def test_empty_name_rejected(client):
    resp = await client.post("/saved", data={"name": "   ", "q": "x"})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_delete(client, session):
    await client.post("/saved", data={"name": "ToGo", "q": "x"})
    obj = await session.scalar(select(SavedSearch).where(SavedSearch.name == "ToGo"))
    resp = await client.post(f"/saved/{obj.id}/delete", follow_redirects=True)
    assert resp.status_code == 200  # redirect followed
    assert await _count(session) == 0


@pytest.mark.asyncio
async def test_invalid_scope_sort_normalized(client, session):
    await client.post(
        "/saved",
        data={"name": "Norm", "q": "x", "scope": "bogus", "sort": "bogus", "dir": "bogus"},
    )
    obj = await session.scalar(select(SavedSearch).where(SavedSearch.name == "Norm"))
    assert obj.scope == "collection" and obj.sort == "name" and obj.direction == "asc"


@pytest.mark.asyncio
async def test_read_only_blocks_mutations(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await client.post("/saved", data={"name": "x", "q": "y"})).status_code == 403
    assert (await client.post("/saved/1/delete")).status_code == 403


# --- JSON API: saved-search writes (/api/v1/saved) -----------------------------------------------

@pytest.mark.asyncio
async def test_api_create_and_list_saved(client, session):
    resp = await client.post("/api/v1/saved", json={
        "name": "Cheap whites", "query": "t:creature c:w mv<=2",
        "scope": "collection", "sort": "price", "direction": "asc",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Cheap whites" and body["query"] == "t:creature c:w mv<=2"
    assert body["new_count"] == 0

    listing = await client.get("/api/v1/saved")
    assert [s["name"] for s in listing.json()] == ["Cheap whites"]


@pytest.mark.asyncio
async def test_api_create_saved_overwrites_same_name(client, session):
    await client.post("/api/v1/saved", json={"name": "Dupe", "query": "a"})
    resp = await client.post("/api/v1/saved", json={"name": "Dupe", "query": "b"})
    assert resp.status_code == 201
    assert resp.json()["query"] == "b"
    assert await _count(session) == 1  # overwritten, not duplicated


@pytest.mark.asyncio
async def test_api_create_saved_normalizes_scope_sort_direction(client, session):
    resp = await client.post("/api/v1/saved", json={
        "name": "Junk", "scope": "nonsense", "sort": "nonsense", "direction": "nonsense",
    })
    body = resp.json()
    assert body["scope"] == "collection" and body["sort"] == "name" and body["direction"] == "asc"


@pytest.mark.asyncio
async def test_api_create_saved_rejects_blank_name(client, session):
    resp = await client.post("/api/v1/saved", json={"name": "   "})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_api_delete_saved(client, session):
    created = await client.post("/api/v1/saved", json={"name": "Bye"})
    saved_id = created.json()["id"]
    resp = await client.delete(f"/api/v1/saved/{saved_id}")
    assert resp.status_code == 200 and resp.json()["ok"] is True
    assert await _count(session) == 0


@pytest.mark.asyncio
async def test_api_delete_saved_is_idempotent(client, session):
    """A replayed offline delete must not look like a failure."""
    resp = await client.delete("/api/v1/saved/9999")
    assert resp.status_code == 200 and resp.json()["ok"] is True


@pytest.mark.asyncio
async def test_api_mark_reviewed_clears_new_count(client, session):
    created = await client.post("/api/v1/saved", json={"name": "Watched"})
    saved_id = created.json()["id"]
    obj = await session.get(SavedSearch, saved_id)
    obj.new_ids = ["a", "b", "c"]
    await session.commit()

    assert (await client.get("/api/v1/saved")).json()[0]["new_count"] == 3
    resp = await client.post(f"/api/v1/saved/{saved_id}/reviewed")
    assert resp.status_code == 200 and resp.json()["new_count"] == 0
    assert (await client.get("/api/v1/saved")).json()[0]["new_count"] == 0


@pytest.mark.asyncio
async def test_api_mark_reviewed_404_for_unknown(client, session):
    resp = await client.post("/api/v1/saved/9999/reviewed")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_saved_writes_blocked_read_only(client, session, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await client.post("/api/v1/saved", json={"name": "No"})).status_code == 403
    assert (await client.delete("/api/v1/saved/1")).status_code == 403
    assert (await client.post("/api/v1/saved/1/reviewed")).status_code == 403
