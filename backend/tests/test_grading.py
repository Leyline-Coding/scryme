"""Graded/slabbed cards + condition photos (#179)."""

import uuid

import pytest
from src.grading import clear_grade, save_grade_photo, set_grade
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.search import SearchScope
from src.search.engine import run_search
from src.valuation import valuation_report


class FakeUpload:
    def __init__(self, data=b"\x89PNG\r\n", filename="card.png", content_type="image/png"):
        self._data = data
        self.filename = filename
        self.content_type = content_type

    async def read(self):
        return self._data


async def _own(session, name="Black Lotus", usd="10000.00"):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
         "set": "lea", "collector_number": "233", "prices": {"usd": usd}}
    ))
    session.add(c)
    await session.flush()
    stack = CollectionCard(scryfall_id=c.scryfall_id, quantity=1)
    session.add(stack)
    await session.commit()
    return c, stack


@pytest.mark.asyncio
async def test_set_and_clear_grade(session):
    _, stack = await _own(session)
    await set_grade(session, stack.id, company="PSA", grade="9", cert="12345",
                    value_override=25000.0)
    await session.refresh(stack)
    assert (stack.grade_company, stack.grade, stack.cert_number) == ("PSA", "9", "12345")
    assert stack.value_override == 25000.0

    await clear_grade(session, stack.id)
    await session.refresh(stack)
    assert stack.grade_company is None and stack.value_override is None


@pytest.mark.asyncio
async def test_save_grade_photo(session):
    _, stack = await _own(session)
    assert await save_grade_photo(session, stack.id, FakeUpload()) is True
    await session.refresh(stack)
    assert stack.grade_photo and stack.grade_photo.endswith(".png")
    # A bad content type is rejected.
    assert await save_grade_photo(
        session, stack.id, FakeUpload(content_type="application/pdf")
    ) is False


@pytest.mark.asyncio
async def test_value_override_in_valuation(session):
    _, stack = await _own(session, usd="100.00")   # market $100
    await set_grade(session, stack.id, company="BGS", grade="9.5", cert=None,
                    value_override=5000.0)
    r = await valuation_report(session, "usd")
    assert r.total_value == pytest.approx(5000.0)   # override, not $100
    assert r.top_cards[0].value == pytest.approx(5000.0)


@pytest.mark.asyncio
async def test_is_graded_search(session):
    _, graded = await _own(session, "Graded One")
    await _own(session, "Raw One")
    await set_grade(session, graded.id, company="CGC", grade="10", cert=None, value_override=None)

    res = await run_search(session, "is:graded", scope=SearchScope.COLLECTION)
    names = [c.name for c in res.cards]
    assert "Graded One" in names and "Raw One" not in names


@pytest.mark.asyncio
async def test_grade_route_and_photo(client, session):
    card, stack = await _own(session)
    resp = await client.post(
        f"/collection/stack/{stack.id}/grade",
        data={"company": "PSA", "grade": "10", "cert": "999", "value_override": "50000"},
        files={"photo": ("slab.png", b"\x89PNG\r\n\x1a\n", "image/png")},
    )
    assert resp.status_code == 200 and "PSA" in resp.text
    await session.refresh(stack)
    assert stack.grade_company == "PSA" and stack.grade_photo

    photo = await client.get(f"/grades/{stack.grade_photo}")
    assert photo.status_code == 200
    # Path traversal is rejected.
    assert (await client.get("/grades/..%2f..%2fetc%2fpasswd")).status_code == 404
