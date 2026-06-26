"""Physical-only filtering: digital (Arena/MTGO) cards are excluded at ingest and prunable."""

import json
import uuid

import pytest
from sqlalchemy import select
from src.db import SessionLocal
from src.models import Card
from src.scryfall.ingest import _is_paper, ingest_from_path, prune_digital_only
from src.scryfall.mapping import card_to_columns


def test_is_paper():
    assert _is_paper({"games": ["paper", "mtgo"]})
    assert _is_paper({})  # no games info -> kept (conservative)
    assert not _is_paper({"games": ["arena"]})
    assert not _is_paper({"games": ["mtgo", "arena"]})


def _card(name, games, n):
    return {"id": str(uuid.uuid4()), "name": name, "set": "tst",
            "collector_number": str(n), "games": games}


@pytest.mark.asyncio
async def test_ingest_excludes_digital(tmp_path, session):
    cards = [_card("Paper Card", ["paper", "mtgo"], 1), _card("Arena Card", ["arena"], 2)]
    path = tmp_path / "bulk.json"
    path.write_text(json.dumps(cards))

    ingested = await ingest_from_path(path, session_factory=SessionLocal)
    assert ingested == 1  # only the paper card
    names = set(await session.scalars(select(Card.name)))
    assert names == {"Paper Card"}


@pytest.mark.asyncio
async def test_prune_removes_existing_digital(session):
    session.add(Card(**card_to_columns(_card("Keep", ["paper"], 1))))
    session.add(Card(**card_to_columns(_card("Drop", ["arena"], 2))))
    await session.commit()

    removed = await prune_digital_only(SessionLocal)
    assert removed == 1
    assert set(await session.scalars(select(Card.name))) == {"Keep"}
