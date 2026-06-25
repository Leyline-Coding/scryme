"""Upload route tests: form, preview, confirm (end-to-end), and read-only guard."""

import re
from pathlib import Path

import pytest
from sqlalchemy import func, select
from src.config import get_settings
from src.models import CollectionCard

from tests.seed_cards import seed_cards

FIXTURES = Path(__file__).parent / "fixtures"
CSV_BYTES = (FIXTURES / "manabox_sample.csv").read_bytes()


def _files():
    return {"file": ("manabox_sample.csv", CSV_BYTES, "text/csv")}


@pytest.mark.asyncio
async def test_upload_form(client):
    resp = await client.get("/upload")
    assert resp.status_code == 200
    assert "Upload a collection" in resp.text


@pytest.mark.asyncio
async def test_preview_then_confirm(client, session):
    await seed_cards(session)

    preview = await client.post("/upload", files=_files())
    assert preview.status_code == 200
    assert "Review import" in preview.text
    assert "Matched" in preview.text
    # 4 of 5 rows match; 1 unmatched.
    assert "Totally Fake Card" in preview.text  # listed as unmatched

    token = re.search(r'name="token" value="([^"]+)"', preview.text).group(1)

    confirm = await client.post(
        "/upload/confirm", data={"token": token, "strategy": "increment"}
    )
    assert confirm.status_code == 303
    assert confirm.headers["location"] == "/search"

    total = await session.scalar(select(func.coalesce(func.sum(CollectionCard.quantity), 0)))
    # Black Lotus 1 + Lightning Bolt 2 + Llanowar 3 + Goblin 1 = 7
    assert total == 7


@pytest.mark.asyncio
async def test_unknown_format_shows_error(client):
    files = {"file": ("x.csv", b"Foo,Bar\n1,2\n", "text/csv")}
    resp = await client.post("/upload", files=files)
    assert resp.status_code == 200
    assert "Unrecognized file" in resp.text


@pytest.mark.asyncio
async def test_read_only_blocks_upload(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.post("/upload", files=_files())
    assert resp.status_code == 403
