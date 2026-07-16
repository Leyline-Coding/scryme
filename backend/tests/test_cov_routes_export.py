"""Coverage for src/routes/export.py — the export handler branches, called directly.

(The HTTP route body runs in a greenlet context the default coverage config does not trace.)
"""

import uuid
from types import SimpleNamespace

import pytest
import src.routes.export as export_mod
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


def test_stream_generators_directly():
    """Consume the CSV/decklist/ManaBox generators in-process (their bodies otherwise run in a
    threadpool via StreamingResponse and escape coverage)."""
    cards = [SimpleNamespace(
        name="Lightning Bolt", set_name="Modern Horizons 2", set_code="mh2",
        collector_number="122", rarity="uncommon", mana_cost="{R}", cmc=1,
        type_line="Instant", prices={"usd": "2.50"}, scryfall_id="sid-1")]
    qmap = {"sid-1": 3}

    csv_out = "".join(export_mod._csv_stream(cards, qmap))
    assert csv_out.splitlines()[0].startswith("Name,Set name,Set code")
    assert "Lightning Bolt" in csv_out and ",3,2.50" in csv_out

    # A card missing cmc/mana_cost/prices exercises the None-handling branches.
    bare = [SimpleNamespace(
        name="Nameless", set_name=None, set_code="tst", collector_number="1", rarity=None,
        mana_cost=None, cmc=None, type_line=None, prices=None, scryfall_id="sid-2")]
    csv_bare = "".join(export_mod._csv_stream(bare, {}))
    assert "Nameless" in csv_bare

    txt_out = "".join(export_mod._txt_stream(cards, qmap))
    assert txt_out == "3x Lightning Bolt (MH2) 122\n"
    # Unowned (no qmap entry) defaults the quantity to 1.
    assert "".join(export_mod._txt_stream(cards, {})) == "1x Lightning Bolt (MH2) 122\n"

    stack = SimpleNamespace(binder_name="Reds", finish="foil", rarity=None, quantity=3,
                            purchase_price=2.5, condition="near_mint", language="en")
    card = SimpleNamespace(name="Lightning Bolt", set_code="mh2", set_name="MH2",
                           collector_number="122", scryfall_id="sid-1", rarity="uncommon")
    mb = "".join(export_mod._manabox_stream([(stack, card)]))
    assert "Scryfall ID" in mb and ",foil," in mb and "near_mint" in mb and "Reds" in mb
    # A stack without a purchase price leaves the price/currency columns blank.
    stack2 = SimpleNamespace(binder_name=None, finish="normal", quantity=1, purchase_price=None,
                             condition=None, language="en")
    mb2 = "".join(export_mod._manabox_stream([(stack2, card)]))
    assert "Lightning Bolt" in mb2


async def _drain(resp) -> str:
    chunks = [chunk async for chunk in resp.body_iterator]
    return b"".join(
        c if isinstance(c, bytes) else c.encode() for c in chunks
    ).decode()


async def _seed(session):
    owned = {"id": str(uuid.uuid4()), "name": "Lightning Bolt", "set": "MH2",
             "collector_number": "122", "rarity": "uncommon", "cmc": 1, "type_line": "Instant",
             "colors": ["R"], "color_identity": ["R"], "prices": {"usd": "2.50"}}
    unowned = {"id": str(uuid.uuid4()), "name": "Black Lotus", "set": "LEA",
               "collector_number": "232", "rarity": "rare", "cmc": 0, "type_line": "Artifact",
               "color_identity": [], "prices": {"usd": "9999.99"}}
    oc = Card(**card_to_columns(owned))
    session.add(oc)
    session.add(Card(**card_to_columns(unowned)))
    await session.flush()
    session.add(CollectionCard(scryfall_id=oc.scryfall_id, quantity=3, finish="foil",
                               condition="near_mint", language="en", binder_name="Reds",
                               purchase_price=2.5))
    await session.commit()
    return oc


@pytest.mark.asyncio
async def test_export_csv(session):
    await _seed(session)
    resp = await export_mod.export(fmt="csv", scope="collection", session=session)
    body = await _drain(resp)
    assert resp.headers["content-disposition"].endswith('scryme-export.csv"')
    assert body.splitlines()[0].startswith("Name,Set name,Set code")
    assert any(r.startswith("Lightning Bolt,") and ",3,2.50" in r for r in body.splitlines()[1:])


@pytest.mark.asyncio
async def test_export_csv_all_scope_includes_unowned(session):
    await _seed(session)
    resp = await export_mod.export(fmt="csv", scope="all", q="lotus", session=session)
    body = await _drain(resp)
    assert any(r.startswith("Black Lotus,") and r.endswith(",0,9999.99") for r in body.splitlines())


@pytest.mark.asyncio
async def test_export_txt(session):
    await _seed(session)
    resp = await export_mod.export(fmt="txt", scope="collection", session=session)
    body = await _drain(resp)
    assert "3x Lightning Bolt (MH2) 122" in body


@pytest.mark.asyncio
async def test_export_manabox(session):
    await _seed(session)
    resp = await export_mod.export(fmt="manabox", scope="collection", session=session)
    body = await _drain(resp)
    assert "Scryfall ID" in body and "ManaBox ID" in body
    line = next(r for r in body.splitlines() if "Lightning Bolt" in r)
    assert ",foil," in line and ",near_mint," in line and "Reds" in line


@pytest.mark.asyncio
async def test_export_unknown_fmt_and_bad_query(session):
    await _seed(session)
    # Unknown fmt falls back to csv.
    resp = await export_mod.export(fmt="bogus", session=session)
    assert resp.headers["content-disposition"].endswith('scryme-export.csv"')
    # Invalid query + bad sort/dir -> header only, no 500.
    resp2 = await export_mod.export(fmt="csv", q="bogus:value", sort="nope", dir="desc",
                                    session=session)
    body = await _drain(resp2)
    assert len([ln for ln in body.splitlines() if ln.strip()]) == 1
