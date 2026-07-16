"""Coverage tests for src.routes.saved: create/overwrite, list, delete, alerts, open.

Handlers are called directly so coverage records the lines past each DB ``await``.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from src.config import get_settings
from src.models import SavedSearch
from src.routes import saved as R


@pytest.mark.asyncio
async def test_create_overwrite_and_list(session):
    r1 = await R.create_saved(name="Dup", q="first", scope="collection", sort="name", dir="asc",
                              session=session)
    assert r1.status_code == 303 and "/search?" in r1.headers["location"]

    # Same name overwrites (single-user); also exercises scope=all + desc direction.
    await R.create_saved(name="Dup", q="second", scope="all", sort="mv", dir="desc",
                         session=session)
    assert await session.scalar(select(func.count()).select_from(SavedSearch)) == 1
    obj = await session.scalar(select(SavedSearch).where(SavedSearch.name == "Dup"))
    assert obj.query == "second" and obj.sort == "mv" and obj.scope == "all"
    assert obj.direction == "desc"

    listed = await R.list_saved(session)
    assert [s.name for s in listed] == ["Dup"]


@pytest.mark.asyncio
async def test_empty_name_and_invalid_normalized(session):
    with pytest.raises(HTTPException) as e:
        await R.create_saved(name="   ", q="x", scope="collection", sort="name", dir="asc",
                             session=session)
    assert e.value.status_code == 400

    # Bogus scope/sort/dir normalize to collection/name/asc.
    await R.create_saved(name="Norm", q="x", scope="bogus", sort="bogus", dir="bogus",
                         session=session)
    obj = await session.scalar(select(SavedSearch).where(SavedSearch.name == "Norm"))
    assert obj.scope == "collection" and obj.sort == "name" and obj.direction == "asc"


@pytest.mark.asyncio
async def test_delete(session):
    await R.create_saved(name="ToGo", q="x", scope="collection", sort="name", dir="asc",
                         session=session)
    obj = await session.scalar(select(SavedSearch).where(SavedSearch.name == "ToGo"))
    resp = await R.delete_saved(obj.id, session)
    assert resp.status_code == 303 and resp.headers["location"] == "/search"
    assert await session.scalar(select(func.count()).select_from(SavedSearch)) == 0
    # Deleting a missing row is a no-op redirect.
    assert (await R.delete_saved(999999, session)).status_code == 303


@pytest.mark.asyncio
async def test_alerts_and_open(session):
    alerts = await R.saved_alerts(session)
    assert alerts == {"total": 0}  # no saved searches yet

    await R.create_saved(name="Open", q="", scope="collection", sort="name", dir="asc",
                         session=session)
    obj = await session.scalar(select(SavedSearch).where(SavedSearch.name == "Open"))
    opened = await R.open_saved(obj.id, session)
    assert opened.status_code == 303 and "/search?" in opened.headers["location"]

    # A missing saved search opens home.
    missing = await R.open_saved(999999, session)
    assert missing.status_code == 303 and missing.headers["location"] == "/"


@pytest.mark.asyncio
async def test_readonly_guards(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    with pytest.raises(HTTPException) as e1:
        await R.create_saved(name="x", q="y", scope="collection", sort="name", dir="asc",
                             session=session)
    assert e1.value.status_code == 403
    with pytest.raises(HTTPException) as e2:
        await R.delete_saved(1, session)
    assert e2.value.status_code == 403
