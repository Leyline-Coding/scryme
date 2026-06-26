"""Export route tests: CSV, decklist, ManaBox round-trip, format/scope handling."""

import uuid

import pytest
from src.importers.base import detect_format
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _seed(session):
    owned = {"id": str(uuid.uuid4()), "name": "Lightning Bolt", "set": "MH2",
             "collector_number": "122", "rarity": "uncommon", "cmc": 1, "type_line": "Instant",
             "colors": ["R"], "color_identity": ["R"], "prices": {"usd": "2.50"}}
    unowned = {"id": str(uuid.uuid4()), "name": "Black Lotus", "set": "LEA",
               "collector_number": "232", "rarity": "rare", "cmc": 0, "type_line": "Artifact",
               "colors": [], "color_identity": [], "prices": {"usd": "9999.99"}}
    owned_card = Card(**card_to_columns(owned))
    session.add(owned_card)
    session.add(Card(**card_to_columns(unowned)))
    await session.flush()
    session.add(CollectionCard(
        scryfall_id=owned_card.scryfall_id, quantity=3, finish="foil",
        condition="near_mint", language="en", binder_name="Reds", purchase_price=2.5))
    await session.commit()
    return owned_card


@pytest.mark.asyncio
async def test_csv_export_quantity_and_headers(client, session):
    await _seed(session)
    resp = await client.get("/export", params={"fmt": "csv", "scope": "collection"})
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    lines = resp.text.splitlines()
    assert lines[0].startswith("Name,Set name,Set code")
    assert any(row.startswith("Lightning Bolt,") and ",3,2.50" in row for row in lines[1:])
    assert "Black Lotus" not in resp.text  # not owned -> excluded in collection scope


@pytest.mark.asyncio
async def test_csv_all_scope_includes_unowned_zero_qty(client, session):
    await _seed(session)
    resp = await client.get("/export", params={"fmt": "csv", "scope": "all", "q": "lotus"})
    rows = [r for r in resp.text.splitlines() if r.startswith("Black Lotus,")]
    assert rows and rows[0].endswith(",0,9999.99")  # unowned -> quantity 0


@pytest.mark.asyncio
async def test_decklist_export(client, session):
    await _seed(session)
    resp = await client.get("/export", params={"fmt": "txt", "scope": "collection"})
    assert resp.headers["content-type"].startswith("text/plain")
    assert "3x Lightning Bolt (MH2) 122" in resp.text


@pytest.mark.asyncio
async def test_manabox_export_round_trips(client, session):
    await _seed(session)
    resp = await client.get("/export", params={"fmt": "manabox", "scope": "collection"})
    body = resp.text
    # The importer must recognize our own export as ManaBox.
    importer = detect_format(body)
    assert importer is not None and importer.format_name == "manabox"
    assert "Scryfall ID" in body and "ManaBox ID" in body
    line = next(r for r in body.splitlines() if "Lightning Bolt" in r)
    assert ",foil," in line and ",near_mint," in line and "Reds" in line


@pytest.mark.asyncio
async def test_unknown_format_defaults_to_csv(client, session):
    await _seed(session)
    resp = await client.get("/export", params={"fmt": "bogus"})
    assert resp.headers["content-disposition"].endswith('scryme-export.csv"')


@pytest.mark.asyncio
async def test_invalid_query_exports_header_only(client, session):
    await _seed(session)
    resp = await client.get("/export", params={"fmt": "csv", "q": "bogus:value"})
    assert resp.status_code == 200
    lines = [ln for ln in resp.text.splitlines() if ln.strip()]
    assert len(lines) == 1  # header only, no rows, no 500
