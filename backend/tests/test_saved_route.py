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
