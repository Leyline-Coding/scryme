"""Coverage tests for src/grading.py: set/clear grade, photo save/replace, safe paths."""

import uuid

import pytest
from src.grading import (
    _clean,
    clear_grade,
    grades_dir,
    safe_photo_path,
    save_grade_photo,
    set_grade,
)
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


class FakeUpload:
    def __init__(self, data=b"\x89PNG\r\n", filename="c.png", content_type="image/png"):
        self._data = data
        self.filename = filename
        self.content_type = content_type

    async def read(self):
        return self._data


async def _stack(session):
    c = Card(**card_to_columns(
        {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": "G",
         "set": "tst", "collector_number": "1", "prices": {"usd": "1.00"}}))
    session.add(c)
    await session.flush()
    s = CollectionCard(scryfall_id=c.scryfall_id, quantity=1)
    session.add(s)
    await session.commit()
    return s


def test_clean_truncates_and_empties():
    assert _clean("  ") is None
    assert _clean(None) is None
    assert _clean("x" * 40) == "x" * 32


def test_safe_photo_path_rejects_traversal():
    assert safe_photo_path("") is None
    assert safe_photo_path("../etc") is None
    assert safe_photo_path("a\\b") is None
    assert safe_photo_path(".hidden") is None
    assert safe_photo_path("missing.png") is None  # not on disk


def test_safe_photo_path_returns_existing(tmp_path):
    d = grades_dir()
    fname = f"{uuid.uuid4().hex}.png"
    (d / fname).write_bytes(b"data")
    try:
        assert safe_photo_path(fname) == d / fname
    finally:
        (d / fname).unlink(missing_ok=True)


@pytest.mark.asyncio
async def test_set_grade_missing_stack(session):
    assert await set_grade(session, 999999, company="PSA", grade="9", cert="1",
                           value_override=1.0) is None


@pytest.mark.asyncio
async def test_set_grade_negative_override_becomes_none(session):
    s = await _stack(session)
    out = await set_grade(session, s.id, company="PSA", grade="10", cert="x",
                          value_override=-5.0)
    assert out.value_override is None


@pytest.mark.asyncio
async def test_save_photo_replaces_old(session):
    s = await _stack(session)
    assert await save_grade_photo(session, s.id, FakeUpload()) is True
    await session.refresh(s)
    first = s.grade_photo
    # A second upload deletes the first and stores a new file.
    assert await save_grade_photo(session, s.id, FakeUpload(filename="c2.png")) is True
    await session.refresh(s)
    assert s.grade_photo != first
    assert not (grades_dir() / first).exists()


@pytest.mark.asyncio
async def test_save_photo_rejections(session):
    s = await _stack(session)
    assert await save_grade_photo(session, 999999, FakeUpload()) is False  # missing stack
    assert await save_grade_photo(session, s.id, None) is False            # no upload
    assert await save_grade_photo(session, s.id,
                                  FakeUpload(filename="")) is False         # no filename
    assert await save_grade_photo(session, s.id,
                                  FakeUpload(content_type="text/plain")) is False  # bad type
    assert await save_grade_photo(session, s.id, FakeUpload(data=b"")) is False     # empty
    big = FakeUpload(data=b"x" * (8 * 1024 * 1024 + 1))
    assert await save_grade_photo(session, s.id, big) is False              # too big


@pytest.mark.asyncio
async def test_clear_grade_removes_photo(session):
    s = await _stack(session)
    await save_grade_photo(session, s.id, FakeUpload())
    await session.refresh(s)
    fname = s.grade_photo
    await set_grade(session, s.id, company="PSA", grade="9", cert="1", value_override=10.0)
    out = await clear_grade(session, s.id)
    assert out.grade_company is None and out.value_override is None and out.grade_photo is None
    assert not (grades_dir() / fname).exists()


@pytest.mark.asyncio
async def test_clear_grade_missing(session):
    assert await clear_grade(session, 999999) is None
