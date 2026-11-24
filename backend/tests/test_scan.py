"""Batch scan ingest (#164): resolution, increment semantics, locations, and idempotent replay.

The contract these pin is the one scanme consumes (#209): a batch identified by Scryfall id *or*
set+number, increment-merged, honouring a per-batch ``Idempotency-Key``, reporting matched and
unmatched rows. The idempotency tests are the important ones — a scanner retries, and "add these
40 cards" applied twice is a silently wrong collection rather than a visible error.
"""

import uuid

import pytest
from sqlalchemy import select
from src.models import Card, CollectionCard, ScanBatch
from src.scan import ingest_scan, to_import_row
from src.scryfall.mapping import card_to_columns

BOLT = "00000000-0000-0000-0000-00000000ca01"
ELVES = "00000000-0000-0000-0000-00000000ca02"
DFC = "00000000-0000-0000-0000-00000000ca03"

_CARDS = [
    {"id": BOLT, "name": "Lightning Bolt", "set": "mh2", "collector_number": "122",
     "rarity": "uncommon", "cmc": 1, "type_line": "Instant", "colors": ["R"]},
    {"id": ELVES, "name": "Llanowar Elves", "set": "m19", "collector_number": "314",
     "rarity": "common", "cmc": 1, "type_line": "Creature — Elf Druid", "colors": ["G"]},
    {"id": DFC, "name": "Delver of Secrets // Insectile Aberration", "set": "isd",
     "collector_number": "51", "rarity": "common", "cmc": 1, "layout": "transform",
     "legalities": {"modern": "legal"},
     "card_faces": [{"name": "Delver of Secrets"}, {"name": "Insectile Aberration"}]},
]


async def _seed(session):
    for raw in _CARDS:
        session.add(Card(**card_to_columns(raw)))
    await session.commit()


async def _stacks(session):
    return list((await session.execute(select(CollectionCard))).scalars().all())


def _row(**kw):
    return to_import_row(**kw)


# --- resolution ----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_resolves_by_scryfall_id_set_number_and_name(session):
    await _seed(session)
    report = await ingest_scan(session, [
        _row(scryfall_id=BOLT, quantity=2),
        _row(set_code="M19", collector_number="314"),
        _row(name="Delver of Secrets"),
    ])
    assert (report.total_rows, report.matched, report.unmatched) == (3, 3, 0)
    assert [r.method for r in report.rows] == ["scryfall_id", "set_number", "name_front_face"]
    # The set code is normalized on the way in, so a scanner may send it in any case.
    assert report.rows[1].scryfall_id == ELVES


@pytest.mark.asyncio
async def test_a_row_with_no_name_still_resolves_on_its_printing(session):
    """A scanner reads a collector number off the card; it often has no text at all."""
    await _seed(session)
    report = await ingest_scan(session, [_row(set_code="mh2", collector_number="122")])
    assert report.rows[0].matched and report.rows[0].scryfall_id == BOLT
    # The resolved name comes back so the scanner can show what it actually landed on.
    assert report.rows[0].name == "Lightning Bolt"
    assert report.rows[0].set_code == "mh2" and report.rows[0].collector_number == "122"


@pytest.mark.asyncio
async def test_unmatched_rows_are_reported_without_failing_the_batch(session):
    await _seed(session)
    report = await ingest_scan(session, [
        _row(scryfall_id=BOLT),
        _row(name="Not A Real Card"),
        _row(set_code="zzz", collector_number="9"),
    ])
    assert (report.matched, report.unmatched) == (1, 2)
    assert [r.matched for r in report.rows] == [True, False, False]
    # The unmatched row echoes back what was sent, so the scanner can show it for a manual fix.
    assert report.rows[1].name == "Not A Real Card"
    assert report.rows[2].set_code == "zzz" and report.rows[2].collector_number == "9"
    # Only the matched row was added.
    assert sum(s.quantity for s in await _stacks(session)) == 1


# --- merge semantics -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_scanning_increments_an_existing_stack(session):
    await _seed(session)
    await ingest_scan(session, [_row(scryfall_id=BOLT, quantity=2)])
    report = await ingest_scan(session, [_row(scryfall_id=BOLT, quantity=3)])

    stacks = await _stacks(session)
    assert len(stacks) == 1 and stacks[0].quantity == 5
    assert (report.inserted, report.updated) == (0, 1)
    # The concurrent-edit guard sees the scan, so a stale absolute edit can't overwrite it (#207).
    assert stacks[0].version > 1


@pytest.mark.asyncio
async def test_repeated_rows_in_one_batch_collapse_into_one_stack(session):
    await _seed(session)
    report = await ingest_scan(session, [_row(scryfall_id=BOLT)] * 3)
    stacks = await _stacks(session)
    assert len(stacks) == 1 and stacks[0].quantity == 3
    # One stack was created, not three — inserted/updated count stacks, not rows.
    assert (report.inserted, report.updated) == (1, 0)
    assert report.total_quantity == 3


@pytest.mark.asyncio
async def test_finish_and_condition_make_separate_stacks(session):
    await _seed(session)
    await ingest_scan(session, [
        _row(scryfall_id=BOLT, finish="foil"),
        _row(scryfall_id=BOLT, finish="normal"),
        _row(scryfall_id=BOLT, finish="normal", condition="LP"),
    ])
    stacks = await _stacks(session)
    assert len(stacks) == 3
    assert {(s.finish, s.condition) for s in stacks} == {
        ("foil", None), ("normal", None), ("normal", "LP")
    }


@pytest.mark.asyncio
async def test_an_unknown_finish_falls_back_to_normal(session):
    await _seed(session)
    await ingest_scan(session, [_row(scryfall_id=BOLT, finish="sparkly")])
    assert (await _stacks(session))[0].finish == "normal"


@pytest.mark.asyncio
async def test_quantity_is_floored_at_one(session):
    """A scanner sending 0 or a negative means "one card", not "remove some"."""
    await _seed(session)
    await ingest_scan(session, [_row(scryfall_id=BOLT, quantity=0)])
    assert (await _stacks(session))[0].quantity == 1


# --- locations -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_located_scan_is_a_separate_stack_from_the_unfiled_one(session):
    """The whole reason this doesn't reuse the CSV merge: that keys stacks without a location.

    "The copies I just put in Box A" has to be distinguishable from the same printing sitting
    unfiled, or filing a box silently moves cards that were never in it.
    """
    await _seed(session)
    await ingest_scan(session, [_row(scryfall_id=BOLT, quantity=2)])
    await ingest_scan(session, [_row(scryfall_id=BOLT, quantity=3)], location="Box A")

    stacks = sorted(await _stacks(session), key=lambda s: s.quantity)
    assert [(s.location, s.quantity) for s in stacks] == [(None, 2), ("Box A", 3)]


@pytest.mark.asyncio
async def test_rescanning_the_same_location_increments_that_stack(session):
    await _seed(session)
    await ingest_scan(session, [_row(scryfall_id=BOLT)], location="Box A")
    report = await ingest_scan(session, [_row(scryfall_id=BOLT)], location="Box A")
    stacks = await _stacks(session)
    assert len(stacks) == 1 and stacks[0].quantity == 2
    assert report.location == "Box A" and (report.inserted, report.updated) == (0, 1)


# --- the HTTP surface ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_post_scan_applies_a_batch(client, session):
    await _seed(session)
    resp = await client.post("/api/v1/scan", json={
        "location": "Box A",
        "rows": [{"scryfall_id": BOLT, "quantity": 2},
                 {"set": "m19", "collector_number": "314", "finish": "foil"}],
    })
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] and not body["replayed"] and body["idempotency_key"] is None
    assert (body["matched"], body["unmatched"], body["inserted"]) == (2, 0, 2)
    assert body["total_quantity"] == 3 and body["location"] == "Box A"
    assert body["rows"][1]["set"] == "m19" and body["rows"][1]["name"] == "Llanowar Elves"


@pytest.mark.asyncio
async def test_post_scan_rejects_an_empty_batch(client, session):
    resp = await client.post("/api/v1/scan", json={"rows": []})
    assert resp.status_code == 400


@pytest.mark.asyncio
async def test_post_scan_rejects_an_oversized_batch(client, session):
    from src.scan import MAX_ROWS
    rows = [{"scryfall_id": BOLT}] * (MAX_ROWS + 1)
    resp = await client.post("/api/v1/scan", json={"rows": rows})
    assert resp.status_code == 413


@pytest.mark.asyncio
async def test_post_scan_is_refused_on_a_read_only_instance(client, session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.post("/api/v1/scan", json={"rows": [{"scryfall_id": BOLT}]})
    assert resp.status_code == 403


# --- idempotency ---------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_replaying_a_batch_key_does_not_add_the_cards_again(client, session):
    """The property the scanner depends on: a retry after a timeout is not a second import."""
    await _seed(session)
    payload = {"rows": [{"scryfall_id": BOLT, "quantity": 4}]}
    headers = {"Idempotency-Key": "box-a-001"}

    first = (await client.post("/api/v1/scan", json=payload, headers=headers)).json()
    second = (await client.post("/api/v1/scan", json=payload, headers=headers)).json()

    assert first["replayed"] is False and second["replayed"] is True
    # The replay is the *original* answer, so a scanner that never saw the first response can
    # still reconcile the batch from the second.
    assert second["rows"] == first["rows"]
    assert (second["matched"], second["inserted"]) == (first["matched"], first["inserted"])

    stacks = await _stacks(session)
    assert len(stacks) == 1 and stacks[0].quantity == 4


@pytest.mark.asyncio
async def test_a_replay_ignores_the_rows_it_is_sent(client, session):
    """A retry is "did my batch land?", not a new batch — its body must not be applied."""
    await _seed(session)
    headers = {"Idempotency-Key": "box-a-002"}
    await client.post("/api/v1/scan", json={"rows": [{"scryfall_id": BOLT}]}, headers=headers)
    body = (await client.post(
        "/api/v1/scan", json={"rows": [{"scryfall_id": ELVES, "quantity": 99}]}, headers=headers
    )).json()

    assert body["replayed"] is True
    stacks = await _stacks(session)
    assert len(stacks) == 1 and str(stacks[0].scryfall_id) == BOLT


@pytest.mark.asyncio
async def test_different_keys_are_different_batches(client, session):
    await _seed(session)
    payload = {"rows": [{"scryfall_id": BOLT, "quantity": 2}]}
    await client.post("/api/v1/scan", json=payload, headers={"Idempotency-Key": "a"})
    await client.post("/api/v1/scan", json=payload, headers={"Idempotency-Key": "b"})
    assert (await _stacks(session))[0].quantity == 4


@pytest.mark.asyncio
async def test_a_batch_without_a_key_is_applied_every_time(client, session):
    """Not sending a key is the caller's choice to accept double-counting on a retry."""
    await _seed(session)
    payload = {"rows": [{"scryfall_id": BOLT}]}
    await client.post("/api/v1/scan", json=payload)
    await client.post("/api/v1/scan", json=payload)
    assert (await _stacks(session))[0].quantity == 2
    assert await session.scalar(select(ScanBatch).where(ScanBatch.idempotency_key == "")) is None


@pytest.mark.asyncio
async def test_the_batch_record_stores_the_response_for_replay(client, session):
    await _seed(session)
    await client.post("/api/v1/scan", json={"rows": [{"scryfall_id": BOLT}]},
                      headers={"Idempotency-Key": "box-c"})
    row = await session.scalar(select(ScanBatch).where(ScanBatch.idempotency_key == "box-c"))
    assert row is not None and row.response["matched"] == 1
    assert row.response["rows"][0]["scryfall_id"] == BOLT


@pytest.mark.asyncio
async def test_a_key_that_loses_the_unique_index_replays_instead_of_double_applying(
    client, session, monkeypatch
):
    """Two retries racing past the pre-check must still apply the merge exactly once.

    Simulated by letting the batch record be written behind this request's back, so its own commit
    hits the unique index — which is precisely what the losing request of a real race sees.
    """
    await _seed(session)
    from src.routes import api as api_routes

    real_ingest = api_routes.ingest_scan
    state = {"raced": False}

    async def racing_ingest(sess, rows, **kw):
        report = await real_ingest(sess, rows, **kw)
        if not state["raced"]:
            state["raced"] = True
            # A separate session commits the same key first, exactly as the winning request would.
            from src.db import SessionLocal
            async with SessionLocal() as other:
                other.add(ScanBatch(idempotency_key="racy", response={
                    "ok": True, "replayed": False, "idempotency_key": "racy", "total_rows": 1,
                    "matched": 1, "unmatched": 0, "inserted": 1, "updated": 0,
                    "total_quantity": 7, "location": None, "rows": [],
                }))
                await other.commit()
        return report

    monkeypatch.setattr(api_routes, "ingest_scan", racing_ingest)
    body = (await client.post(
        "/api/v1/scan", json={"rows": [{"scryfall_id": BOLT, "quantity": 5}]},
        headers={"Idempotency-Key": "racy"},
    )).json()

    # The winner's answer is returned, and this request's increments were rolled back with its
    # transaction — the collection was incremented once, not twice.
    assert body["replayed"] is True and body["total_quantity"] == 7
    assert await _stacks(session) == []


@pytest.mark.asyncio
async def test_a_card_pruned_mid_batch_is_reported_unmatched(session, monkeypatch):
    """`prune-digital` or a re-ingest can drop a printing between resolution and the merge."""
    await _seed(session)
    from src import scan as scan_module

    async def gone(*a, **kw):
        return None

    monkeypatch.setattr(scan_module, "add_or_increment", gone)
    report = await ingest_scan(session, [_row(scryfall_id=BOLT)])
    assert (report.matched, report.unmatched) == (0, 1)
    assert report.rows[0].method == "unmatched"


@pytest.mark.asyncio
async def test_an_empty_batch_reports_nothing(session):
    report = await ingest_scan(session, [])
    assert (report.total_rows, report.matched, report.total_quantity) == (0, 0, 0)
    assert report.rows == []


def test_to_import_row_normalizes_its_input():
    row = to_import_row(name="  Bolt ", set_code=" MH2 ", collector_number=" 122 ",
                        scryfall_id="  ", finish=None, condition="  ", language="  EN ")
    assert row.name == "Bolt" and row.set_code == "mh2" and row.collector_number == "122"
    assert row.scryfall_id is None and row.condition is None
    assert row.finish == "normal" and row.language == "en" and row.quantity == 1


def test_to_import_row_defaults_a_blank_language_to_english():
    assert to_import_row(name="x", language="").language == "en"


@pytest.mark.asyncio
async def test_a_malformed_scryfall_id_falls_through_to_the_other_identifiers(session):
    """The resolver already tolerates this; pinning it because a scanner will send junk ids."""
    await _seed(session)
    report = await ingest_scan(session, [
        _row(scryfall_id="not-a-uuid", set_code="mh2", collector_number="122"),
    ])
    assert report.rows[0].matched and report.rows[0].scryfall_id == BOLT
    assert report.rows[0].method == "set_number"


@pytest.mark.asyncio
async def test_an_unknown_uuid_is_unmatched_rather_than_an_error(session):
    await _seed(session)
    report = await ingest_scan(session, [_row(scryfall_id=str(uuid.uuid4()))])
    assert report.rows[0].matched is False
