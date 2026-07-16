"""Coverage tests for src/market_prices.py: guards, DB writes, downloads, and syncs.

Everything external (MTGJSON / ManaPool HTTP) is mocked — no real network. The gzip-JSON
fixtures are streamed through the real ``ijson``/``_stream_kv`` path so the parsing branches run.
"""

import datetime
import gzip
import json
import uuid

import pytest
import src.market_prices as mp
from sqlalchemy import text
from src.db import SessionLocal
from src.models import Card, IngestState

# --- fakes ---------------------------------------------------------------------------------

class _FakeStream:
    def __init__(self, chunks, status_ok=True):
        self._chunks = chunks
        self._ok = status_ok

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        if not self._ok:
            raise RuntimeError("http error")

    async def aiter_bytes(self):
        for c in self._chunks:
            yield c


class _FakeStreamClient:
    """Stand-in for httpx.AsyncClient used by ``_download`` (streaming)."""

    def __init__(self, chunks, status_ok=True, **kwargs):
        self._chunks = chunks
        self._ok = status_ok

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def stream(self, method, url):
        return _FakeStream(self._chunks, self._ok)


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _FakeGetClient:
    """Stand-in for httpx.AsyncClient used by ``sync_manapool`` (single GET)."""

    def __init__(self, payload, **kwargs):
        self._payload = payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get(self, url):
        return _FakeResp(self._payload)


def _write_gzip_json(path, obj):
    with gzip.open(path, "wb") as fh:
        fh.write(json.dumps(obj).encode())


async def _seed_card(session, *, mtgjson_id=None, prices=None):
    card = Card(
        scryfall_id=uuid.uuid4(),
        name="Priced",
        set_code="tst",
        collector_number="1",
        prices=prices or {},
        raw={"name": "Priced"},
        mtgjson_id=mtgjson_id,
    )
    session.add(card)
    await session.commit()
    return card


async def _get_market(sid):
    async with SessionLocal() as s:
        card = await s.get(Card, sid)
        return card.market_prices


# --- _guard_allows -------------------------------------------------------------------------

def test_guard_allows():
    assert mp._guard_allows(None) is True
    st = IngestState(bulk_type=mp.CK_STATE, last_downloaded_at=None)
    assert mp._guard_allows(st) is True
    now = datetime.datetime.now(datetime.UTC)
    fresh = IngestState(bulk_type=mp.CK_STATE, last_downloaded_at=now)
    assert mp._guard_allows(fresh) is False
    stale = IngestState(
        bulk_type=mp.CK_STATE,
        last_downloaded_at=now - datetime.timedelta(hours=mp._REFRESH_HOURS + 1),
    )
    assert mp._guard_allows(stale) is True


def test_ua_returns_configured_agent():
    assert "scryme" in mp._ua().lower()


def test_latest_and_parsers_edge_cases():
    assert mp._latest(None) is None
    assert mp._latest({}) is None
    assert mp._latest({"2026-07-14": 1.0, "2026-07-15": 2.0}) == 2.0
    # Card Kingdom with only a normal price (foil day-map absent -> _latest(None) -> None).
    assert mp.cardkingdom_price(
        {"paper": {"cardkingdom": {"retail": {"normal": {"2026-07-15": 3.0}}}}}
    ) == {"usd": "3.00"}
    # ManaPool etched-only row.
    assert mp.manapool_price({"price_cents_etched": 999}) == {"usd_etched": "9.99"}


# --- _record -------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_record_creates_then_updates(session):
    await mp._record("cov_state", 5)
    async with SessionLocal() as s:
        st = await s.get(IngestState, "cov_state")
        assert st is not None and st.card_count == 5 and st.status == "idle"
        assert st.last_downloaded_at is not None
    # Re-record hits the "already exists" branch and updates the count.
    await mp._record("cov_state", 9)
    async with SessionLocal() as s:
        st = await s.get(IngestState, "cov_state")
        assert st.card_count == 9
    # cleanup (ingest_state is not truncated between tests)
    async with SessionLocal() as s:
        await s.execute(text("DELETE FROM ingest_state WHERE bulk_type = 'cov_state'"))
        await s.commit()


# --- _apply_prices -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_apply_prices_empty_is_zero():
    assert await mp._apply_prices("manapool", {}, SessionLocal) == 0


@pytest.mark.asyncio
async def test_apply_prices_writes_to_market_prices(session):
    card = await _seed_card(session, prices={"usd": "1.00"})
    written = await mp._apply_prices(
        "manapool", {str(card.scryfall_id): {"usd": "2.50", "usd_foil": "9.00"}}, SessionLocal
    )
    assert written == 1
    market = await _get_market(card.scryfall_id)
    assert market["manapool"] == {"usd": "2.50", "usd_foil": "9.00"}


@pytest.mark.asyncio
async def test_apply_prices_batches_over_1000(session):
    """>1000 entries triggers the mid-loop flush; unmatched sids still count as written."""
    card = await _seed_card(session, prices={"usd": "1.00"})
    by_sid = {str(uuid.uuid4()): {"usd": "0.10"} for _ in range(1000)}
    by_sid[str(card.scryfall_id)] = {"usd": "3.33"}
    written = await mp._apply_prices("cardkingdom", by_sid, SessionLocal)
    assert written == 1001
    market = await _get_market(card.scryfall_id)
    assert market["cardkingdom"] == {"usd": "3.33"}


# --- _download / _stream_kv ----------------------------------------------------------------

@pytest.mark.asyncio
async def test_download_streams_to_file(monkeypatch, tmp_path):
    monkeypatch.setattr(mp.httpx, "AsyncClient",
                        lambda **kw: _FakeStreamClient([b"BULK", b"DATA"]))
    dest = tmp_path / "nested" / "out.json.gz"
    result = await mp._download("https://example.test/file", dest)
    assert result == dest
    assert dest.read_bytes() == b"BULKDATA"
    assert not dest.with_suffix(dest.suffix + ".part").exists()


@pytest.mark.asyncio
async def test_download_raises_on_http_error(monkeypatch, tmp_path):
    monkeypatch.setattr(mp.httpx, "AsyncClient",
                        lambda **kw: _FakeStreamClient([], status_ok=False))
    with pytest.raises(RuntimeError):
        await mp._download("https://example.test/file", tmp_path / "x.gz")


def test_stream_kv_reads_gzip_object(tmp_path):
    path = tmp_path / "data.json.gz"
    _write_gzip_json(path, {"data": {"a": {"n": 1}, "b": {"n": 2}}})
    items = dict(mp._stream_kv(path, "data"))
    assert items == {"a": {"n": 1}, "b": {"n": 2}}


# --- backfill_mtgjson_ids ------------------------------------------------------------------

@pytest.mark.asyncio
async def test_backfill_mtgjson_ids_maps_cards(session, monkeypatch):
    card = await _seed_card(session)
    sid = str(card.scryfall_id)

    async def fake_download(url, dest):
        # >1000 mappings so the mid-loop batch flush runs; only the real card is matched
        # but every mapping counts toward ``written``.
        data = {f"uuid-{i}": {"identifiers": {"scryfallId": str(uuid.uuid4())}}
                for i in range(1000)}
        data["uuid-known"] = {"identifiers": {"scryfallId": sid}}
        data["uuid-nosid"] = {"identifiers": {}}  # no scryfallId -> skipped
        _write_gzip_json(dest, {"data": data})
        return dest

    monkeypatch.setattr(mp, "_download", fake_download)
    written = await mp.backfill_mtgjson_ids(SessionLocal, force=True)
    assert written == 1001
    async with SessionLocal() as s:
        got = await s.get(Card, card.scryfall_id)
        assert got.mtgjson_id == "uuid-known"


@pytest.mark.asyncio
async def test_backfill_mtgjson_ids_respects_cache_guard(session, monkeypatch):
    async with SessionLocal() as s:
        await s.merge(IngestState(bulk_type=mp.IDMAP_STATE,
                                  last_downloaded_at=datetime.datetime.now(datetime.UTC)))
        await s.commit()

    async def boom(url, dest):
        raise AssertionError("should not download when cache guard blocks")

    monkeypatch.setattr(mp, "_download", boom)
    assert await mp.backfill_mtgjson_ids(SessionLocal, force=False) == 0


# --- sync_cardkingdom ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_cardkingdom_no_idmap(session):
    # No cards carry an mtgjson_id -> nothing to join -> 0.
    await _seed_card(session)
    assert await mp.sync_cardkingdom(SessionLocal, force=True) == 0


@pytest.mark.asyncio
async def test_sync_cardkingdom_prices_cards(session, monkeypatch):
    card = await _seed_card(session, mtgjson_id="uuid-ck", prices={"usd": "1.00"})

    async def fake_download(url, dest):
        _write_gzip_json(dest, {"data": {
            "uuid-ck": {"paper": {"cardkingdom": {"retail": {
                "normal": {"2026-07-15": 4.25},
                "foil": {"2026-07-15": 12.00},
            }}}},
            "uuid-unmapped": {"paper": {"cardkingdom": {"retail": {
                "normal": {"2026-07-15": 1.0}}}}},  # not in our idmap -> skipped
        }})
        return dest

    monkeypatch.setattr(mp, "_download", fake_download)
    written = await mp.sync_cardkingdom(SessionLocal, force=True)
    assert written == 1
    market = await _get_market(card.scryfall_id)
    assert market["cardkingdom"] == {"usd": "4.25", "usd_foil": "12.00"}


@pytest.mark.asyncio
async def test_sync_cardkingdom_cache_guard(session, monkeypatch):
    await _seed_card(session, mtgjson_id="uuid-ck")
    async with SessionLocal() as s:
        await s.merge(IngestState(bulk_type=mp.CK_STATE,
                                  last_downloaded_at=datetime.datetime.now(datetime.UTC)))
        await s.commit()

    async def boom(url, dest):
        raise AssertionError("guard should block the download")

    monkeypatch.setattr(mp, "_download", boom)
    assert await mp.sync_cardkingdom(SessionLocal, force=False) == 0


# --- sync_manapool -------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_manapool_prices_cards(session, monkeypatch):
    card = await _seed_card(session, prices={"usd": "1.00"})
    payload = {"data": [
        {"scryfall_id": str(card.scryfall_id), "price_market": 184, "price_market_foil": 2500},
        {"scryfall_id": None, "price_cents": 100},          # no sid -> skipped
        {"scryfall_id": str(uuid.uuid4())},                 # no price -> skipped
    ]}
    monkeypatch.setattr(mp.httpx, "AsyncClient", lambda **kw: _FakeGetClient(payload))
    written = await mp.sync_manapool(SessionLocal, force=True)
    assert written == 1
    market = await _get_market(card.scryfall_id)
    assert market["manapool"] == {"usd": "1.84", "usd_foil": "25.00"}


@pytest.mark.asyncio
async def test_sync_manapool_cache_guard(session, monkeypatch):
    async with SessionLocal() as s:
        await s.merge(IngestState(bulk_type=mp.MANAPOOL_STATE,
                                  last_downloaded_at=datetime.datetime.now(datetime.UTC)))
        await s.commit()

    def boom(**kw):
        raise AssertionError("guard should block the request")

    monkeypatch.setattr(mp.httpx, "AsyncClient", boom)
    assert await mp.sync_manapool(SessionLocal, force=False) == 0


# --- sync_market_prices --------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sync_market_prices_isolates_failures(monkeypatch):
    async def ok(session_factory, *, force=False):
        return 7

    async def boom(session_factory, *, force=False):
        raise RuntimeError("source down")

    monkeypatch.setattr(mp, "sync_cardkingdom", ok)
    monkeypatch.setattr(mp, "sync_manapool", boom)
    result = await mp.sync_market_prices(SessionLocal, force=True)
    assert result == {"cardkingdom": 7, "manapool": 0}
