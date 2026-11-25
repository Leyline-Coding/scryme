"""Coverage tests for src/scryfall/ingest.py — parsing, progress, guard, and full ingest flow."""

from __future__ import annotations

import datetime
import gzip
import json
import shutil
import uuid
from pathlib import Path

import pytest
from sqlalchemy import func, select
from src.models import Card, IngestState
from src.scryfall import ingest as ingest_mod
from src.scryfall.ingest import (
    IngestResult,
    _guard_allows_refresh,
    _is_gzip,
    _is_paper,
    _parse_dt,
    _read_batches,
    bulk_download_uri,
    current_card_count,
    get_ingest_progress,
    ingest_default_cards,
    ingest_from_path,
    prune_digital_only,
)

FIXTURES = Path(__file__).parent / "fixtures"
SAMPLE = FIXTURES / "scryfall_sample.jsonl"


def test_is_paper_and_is_gzip(tmp_path):
    assert _is_paper({}) is True  # no games key -> assume paper
    assert _is_paper({"games": ["paper", "mtgo"]}) is True
    assert _is_paper({"games": ["arena", "mtgo"]}) is False

    plain = tmp_path / "plain.json"
    plain.write_text("[]")
    assert _is_gzip(plain) is False
    gz = tmp_path / "c.json.gz"
    with gzip.open(gz, "wb") as fh:
        fh.write(b"[]")
    assert _is_gzip(gz) is True


def test_parse_dt():
    assert _parse_dt(None) is None
    dt = _parse_dt("2026-06-25T09:00:00Z")
    assert dt is not None
    assert dt.year == 2026 and dt.tzinfo is not None


def test_read_batches_filters_digital_and_batches(tmp_path):
    cards = [
        {"id": "1", "name": "Paper A", "games": ["paper"]},
        {"id": "2", "name": "Arena B", "games": ["arena"]},  # filtered out
        {"id": "3", "name": "No games C"},  # kept (assume paper)
        {"id": "4", "name": "Paper D", "games": ["paper", "mtgo"]},
    ]
    f = tmp_path / "cards.jsonl"
    f.write_text("\n".join(json.dumps(card) for card in cards) + "\n", encoding="utf-8")
    batches = list(_read_batches(f, batch_size=2))
    flat = [c["id"] for b in batches for c in b]
    assert flat == ["1", "3", "4"]  # arena dropped
    assert len(batches) == 2  # batch_size=2 -> [2, 1]


def test_get_ingest_progress_is_a_copy():
    snap = get_ingest_progress()
    assert set(snap) >= {"phase", "ingested", "total", "error"}
    snap["phase"] = "mutated"
    assert get_ingest_progress()["phase"] != "mutated"  # returned a copy


@pytest.mark.asyncio
async def test_ingest_from_path_and_progress(session):
    count = await ingest_from_path(SAMPLE)
    assert count == 3
    assert await session.scalar(select(func.count()).select_from(Card)) == 3
    assert get_ingest_progress()["ingested"] == 3
    assert await current_card_count() == 3


@pytest.mark.asyncio
async def test_prune_digital_only(session):
    paper = {"id": str(uuid.uuid4()), "name": "P", "set": "t", "collector_number": "1",
             "games": ["paper"]}
    digital = {"id": str(uuid.uuid4()), "name": "D", "set": "t", "collector_number": "2",
               "games": ["arena", "mtgo"]}
    from src.scryfall.mapping import card_to_columns

    session.add(Card(**card_to_columns(paper)))
    session.add(Card(**card_to_columns(digital)))
    await session.commit()

    removed = await prune_digital_only()
    assert removed == 1
    remaining = (await session.execute(select(Card.name))).scalars().all()
    assert remaining == ["P"]


def test_guard_allows_refresh_rules():
    now = datetime.datetime.now(datetime.UTC)
    assert _guard_allows_refresh(None, now, 24) is True
    fresh = IngestState(bulk_type="default_cards", source_updated_at=now,
                        last_downloaded_at=now, card_count=3)
    assert _guard_allows_refresh(fresh, now, 24) is False
    assert _guard_allows_refresh(fresh, now + datetime.timedelta(hours=1), 24) is True
    stale = IngestState(bulk_type="default_cards", source_updated_at=now,
                        last_downloaded_at=now - datetime.timedelta(hours=30), card_count=3)
    assert _guard_allows_refresh(stale, now, 24) is True
    # No last_downloaded_at recorded -> allowed.
    partial = IngestState(bulk_type="default_cards", source_updated_at=now, card_count=0)
    assert _guard_allows_refresh(partial, now, 24) is True


class FakeScryfallClient:
    def __init__(self, updated_at="2026-06-25T09:00:00+00:00", source=SAMPLE):
        self._updated_at = updated_at
        self._source = source

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_bulk_entry(self, bulk_type="default_cards"):
        return {"type": bulk_type, "updated_at": self._updated_at,
                "download_uri": "https://example.test/default-cards.json"}

    async def download_to_file(self, url, dest: Path):
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(self._source, dest)
        return dest


@pytest.mark.asyncio
async def test_ingest_from_path_prunes_preexisting_digital(session):
    from src.scryfall.mapping import card_to_columns

    # A digital-only card already in the DB (e.g. from an older unfiltered ingest).
    digital = {"id": str(uuid.uuid4()), "name": "Arena Only", "set": "t", "collector_number": "9",
               "games": ["arena", "mtgo"]}
    session.add(Card(**card_to_columns(digital)))
    await session.commit()

    await ingest_from_path(SAMPLE)  # adds the 3 paper cards and prunes the digital one
    names = set((await session.execute(select(Card.name))).scalars().all())
    assert "Arena Only" not in names


async def _clear_ingest_state(session):
    # ingest_state isn't in conftest's TRUNCATE list, so wipe it for a clean "absent" precondition.
    await session.execute(IngestState.__table__.delete())
    await session.commit()


@pytest.mark.asyncio
async def test_record_success_creates_state_when_absent(session):
    await _clear_ingest_state(session)
    await ingest_mod._record_success(None, 7)  # inserts a fresh row (lines 221-222)
    state = await session.get(IngestState, "default_cards")
    assert state is not None and state.card_count == 7 and state.status == "idle"


@pytest.mark.asyncio
async def test_set_status_creates_state_when_absent(session):
    await _clear_ingest_state(session)
    await ingest_mod._set_status("running")  # inserts a fresh row (lines 211-212)
    state = await session.get(IngestState, "default_cards")
    assert state is not None and state.status == "running"


@pytest.mark.asyncio
async def test_ingest_default_cards_end_to_end(session):
    result = await ingest_default_cards(client=FakeScryfallClient(), force=True)
    assert result.skipped is False and result.card_count == 3
    state = await session.get(IngestState, "default_cards")
    assert state.status == "idle" and state.card_count == 3
    assert get_ingest_progress()["phase"] == "done"


@pytest.mark.asyncio
async def test_ingest_default_cards_cache_guard(session):
    client = FakeScryfallClient()
    await ingest_default_cards(client=client, force=True)
    result = await ingest_default_cards(client=client, force=False)
    assert result.skipped is True and result.reason == "cached"
    assert isinstance(result, IngestResult)


@pytest.mark.asyncio
async def test_ingest_default_cards_parse_error_sets_error_status(session, monkeypatch):
    async def boom(path):
        raise RuntimeError("parse blew up")

    monkeypatch.setattr(ingest_mod, "ingest_from_path", boom)
    with pytest.raises(RuntimeError, match="parse blew up"):
        await ingest_default_cards(client=FakeScryfallClient(), force=True)

    state = await session.get(IngestState, "default_cards")
    assert state.status == "error"
    prog = get_ingest_progress()
    assert prog["phase"] == "error" and "parse blew up" in prog["error"]


# --- Scryfall's move to gzipped JSON Lines bulk files (#366) --------------------------------

_JSONL_CARDS = [
    {"id": "1", "name": "Paper A", "games": ["paper"], "cmc": 2.0},
    {"id": "2", "name": "Arena B", "games": ["arena"]},  # filtered out
    {"id": "3", "name": "No games C"},
]


def _jsonl(cards) -> bytes:
    return "".join(json.dumps(c) + "\n" for c in cards).encode()


@pytest.mark.parametrize("compress", [False, True])
def test_read_batches_reads_json_lines(tmp_path, compress):
    f = tmp_path / "cards.jsonl"
    f.write_bytes(gzip.compress(_jsonl(_JSONL_CARDS)) if compress else _jsonl(_JSONL_CARDS))
    flat = [c for b in _read_batches(f, batch_size=2) for c in b]
    assert [c["id"] for c in flat] == ["1", "3"]
    assert isinstance(flat[0]["cmc"], float)  # stays JSONB-serializable, as with the array parser


def test_read_batches_json_lines_tolerates_blank_lines_and_no_trailing_newline(tmp_path):
    f = tmp_path / "cards.jsonl"
    f.write_bytes(b"\n" + _jsonl(_JSONL_CARDS[:1]) + b"\n\r\n" + _jsonl(_JSONL_CARDS[2:]).rstrip())
    assert [c["id"] for b in _read_batches(f) for c in b] == ["1", "3"]


def test_read_batches_still_reads_a_json_array_with_leading_whitespace(tmp_path):
    f = tmp_path / "cards.json"
    f.write_text("\n  " + json.dumps(_JSONL_CARDS))
    assert [c["id"] for b in _read_batches(f) for c in b] == ["1", "3"]


def test_read_batches_empty_file_yields_nothing(tmp_path):
    f = tmp_path / "empty.jsonl"
    f.write_bytes(b"")
    assert list(_read_batches(f)) == []


def test_bulk_download_uri_prefers_json_lines_and_falls_back():
    both = {"type": "default_cards", "download_uri": "https://x.test/a.json",
            "jsonl_download_uri": "https://x.test/a.jsonl.gz"}
    assert bulk_download_uri(both) == "https://x.test/a.jsonl.gz"
    assert bulk_download_uri({"download_uri": "https://x.test/a.json"}) == "https://x.test/a.json"
    with pytest.raises(RuntimeError, match="no download URI"):
        bulk_download_uri({"type": "default_cards", "updated_at": "2026-06-25T09:00:00Z"})


class JsonlScryfallClient(FakeScryfallClient):
    """The catalog as Scryfall serves it today: a gzipped JSONL file, no ``download_uri``."""

    def __init__(self, entry_extra, **kw):
        super().__init__(**kw)
        self._entry_extra = entry_extra
        self.downloaded: list[str] = []

    async def get_bulk_entry(self, bulk_type="default_cards"):
        return {"type": bulk_type, "updated_at": self._updated_at, **self._entry_extra}

    async def download_to_file(self, url, dest: Path):
        self.downloaded.append(url)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(gzip.compress(Path(self._source).read_bytes()))
        return dest


@pytest.mark.asyncio
async def test_ingest_default_cards_from_json_lines_catalog(session):
    url = "https://example.test/default-cards.jsonl.gz"
    client = JsonlScryfallClient({"jsonl_download_uri": url})
    result = await ingest_default_cards(force=True, client=client)
    assert client.downloaded == [url]
    assert result.skipped is False
    expected = sum(
        1 for line in SAMPLE.read_text().splitlines()
        if line.strip() and _is_paper(json.loads(line))
    )
    assert result.card_count == expected > 0
    assert get_ingest_progress()["phase"] == "done"


@pytest.mark.asyncio
async def test_ingest_default_cards_without_any_uri_fails_cleanly(session):
    client = JsonlScryfallClient({})
    with pytest.raises(RuntimeError, match="no download URI"):
        await ingest_default_cards(force=True, client=client)
    assert client.downloaded == []
    progress = get_ingest_progress()
    assert progress["phase"] == "error" and "no download URI" in progress["error"]
    state = await session.get(IngestState, "default_cards")
    assert state is None or state.status != "running"  # never left stuck mid-run


@pytest.mark.asyncio
async def test_failed_download_does_not_leave_the_state_running(session):
    # A download that failed used to leave status="running" for good (seen on the public demo).
    from src.scryfall.ingest import ingest_default_cards

    class FailingClient(FakeScryfallClient):
        async def download_to_file(self, url, dest: Path):
            raise RuntimeError("connection reset")

    await _clear_ingest_state(session)
    with pytest.raises(RuntimeError):
        await ingest_default_cards(force=True, client=FailingClient())
    session.expire_all()
    assert (await session.get(IngestState, "default_cards")).status == "error"


@pytest.mark.asyncio
async def test_last_ingest_age_days(session):
    import datetime

    from src.scryfall.ingest import last_ingest_age_days

    await _clear_ingest_state(session)
    assert await last_ingest_age_days() is None
    session.add(IngestState(
        bulk_type="default_cards",
        last_downloaded_at=datetime.datetime.now(datetime.UTC) - datetime.timedelta(days=10),
    ))
    await session.commit()
    assert 9.9 < await last_ingest_age_days() < 10.1
    await _clear_ingest_state(session)
