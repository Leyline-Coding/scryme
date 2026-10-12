"""Batch scan ingest (#164): apply a scanner's batch straight to the collection.

The point of the scanner is to skip the ManaBox/Delver **scan → export CSV → upload** loop, so this
is the CSV import pipeline with the file taken out of the middle. Rows arrive already structured,
are resolved by the same resolver a CSV upload uses (:mod:`src.importers.matching` — Scryfall id →
set+number → name → front face), and are merged with the same stack semantics as a manual add.

Three deliberate differences from the upload path:

**No staging step.** An upload stages a preview because a human is about to confirm a merge over
their whole collection. A scan is a handful of cards a person is physically holding, and the
scanner shows them what it read before sending; a confirm round-trip on a phone, per box, buys
nothing and costs a network hop.

**Increment only.** The other merge strategies exist to answer "is this file my collection, or an
addition to it?" A scan is unambiguously an addition — these cards are in my hand — so ``replace``
and ``per_card`` have no meaning here and aren't offered.

**Merged through** :func:`src.collection_edit.add_or_increment` **rather than**
:func:`src.importers.merge.apply_merge`. The import merge keys a stack on
(card, finish, condition, language, binder), which predates storage locations; a scan can target a
location, and "the copies I just put in Box A" has to be a different stack from the same printing
sitting unfiled. ``add_or_increment`` already keys on the full six-field key the ``collection_card``
unique constraint uses, and already bumps ``version`` for the concurrent-edit guard (#207), so
reusing it keeps scans, manual adds and trade commits agreeing on what a stack is.

Retries are handled a layer up, in the route, via the ``scan_batch`` replay record.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.collection_edit import add_or_increment
from src.importers.base import ImportRow
from src.importers.matching import MatchedRow, match_rows
from src.models import Card, CollectionCard

# A scanner batch is a person holding a stack of cards, not a collection dump. The cap is a
# guardrail against a client looping rather than a considered throughput limit.
MAX_ROWS = 500

_FINISHES = ("normal", "foil", "etched")


@dataclass
class ScanRowResult:
    """What became of one submitted row, echoed back in submission order."""

    index: int
    matched: bool
    method: str
    quantity: int
    scryfall_id: str | None = None
    name: str | None = None
    set_code: str | None = None
    collector_number: str | None = None


@dataclass
class ScanReport:
    total_rows: int
    matched: int
    unmatched: int
    inserted: int
    updated: int
    total_quantity: int
    location: str | None
    rows: list[ScanRowResult]


def normalize_finish(value: str | None) -> str:
    return value if value in _FINISHES else "normal"


def to_import_row(
    *,
    name: str | None = None,
    quantity: int = 1,
    set_code: str | None = None,
    collector_number: str | None = None,
    scryfall_id: str | None = None,
    finish: str | None = None,
    condition: str | None = None,
    language: str | None = None,
) -> ImportRow:
    """Normalize one submitted row into the shape the shared resolver expects.

    ``name`` is optional here in a way it is not for a CSV: a scanner identifies a card by its
    printing (a Scryfall id, or a set and collector number read off the card), and often has no
    text at all. The resolver treats an empty name as "no name to match on" and falls through to
    the identifiers, which is exactly right.
    """
    return ImportRow(
        name=(name or "").strip(),
        quantity=max(1, int(quantity or 1)),
        set_code=(set_code or "").strip().lower() or None,
        collector_number=(collector_number or "").strip() or None,
        scryfall_id=(scryfall_id or "").strip() or None,
        finish=normalize_finish(finish),
        condition=(condition or "").strip() or None,
        language=((language or "en").strip().lower() or "en"),
    )


async def _display_names(session: AsyncSession, matched: list[MatchedRow]) -> dict[str, tuple]:
    """Resolved (name, set, collector number) per matched id, so the client can show what it got.

    A scanner that submitted only a set and number needs the name back to render a confirmation,
    and one that guessed a printing needs to see which printing it actually landed on.
    """
    ids = {m.scryfall_id for m in matched if m.scryfall_id}
    if not ids:
        return {}
    res = await session.execute(
        select(Card.scryfall_id, Card.name, Card.set_code, Card.collector_number)
        .where(Card.scryfall_id.in_(ids))
    )
    return {str(sid): (name, set_code, cn) for sid, name, set_code, cn in res.all()}


def _unmatched(index: int, row: ImportRow, method: str) -> ScanRowResult:
    """A row that resolved to nothing, echoing back what was sent so it can be fixed by hand."""
    return ScanRowResult(
        index=index, matched=False, method=method, quantity=row.quantity,
        name=row.name or None, set_code=row.set_code, collector_number=row.collector_number,
    )


async def ingest_scan(
    session: AsyncSession,
    rows: list[ImportRow],
    *,
    location: str | None = None,
    commit: bool = True,
) -> ScanReport:
    """Resolve and increment-merge a scanned batch. Unmatched rows are reported, never guessed at.

    The whole batch lands at once: a box scan is a single act, and half of it applying because row
    30 was unreadable is worse than a clear failure. Unmatched rows do not fail the batch — the
    scanner is expected to surface them for the person to resolve by hand, which is the same
    contract the CSV importer offers.

    ``commit=False`` leaves the increments pending so the caller can commit them together with
    something else. The route uses it to write the idempotency record in the *same* transaction as
    the merge, which is what makes a duplicate batch key roll the whole thing back rather than
    needing the applied rows to be un-applied afterwards.
    """
    matched = await match_rows(session, rows)
    cards = await _display_names(session, matched)

    # Which stacks existed before this batch, so the report can distinguish a stack the scan
    # created from one it grew. Comparing ids against a snapshot keeps the stack-key logic in
    # ``add_or_increment`` alone — re-deriving "did this already exist?" here is exactly the
    # duplication that function's ``commit=False`` contract exists to avoid.
    pre_existing = set(
        (await session.execute(select(CollectionCard.id))).scalars().all()
    )
    touched_new: set[int] = set()
    touched_old: set[int] = set()

    results: list[ScanRowResult] = []
    for index, m in enumerate(matched):
        row = m.row
        if not m.scryfall_id:
            results.append(_unmatched(index, row, m.method))
            continue

        stack = await add_or_increment(
            session, m.scryfall_id, row.quantity,
            finish=row.finish, condition=row.condition, language=row.language,
            location=location, commit=False,
        )
        if stack is None:
            # The resolver saw this printing but it is gone now — `prune-digital` or a re-ingest
            # can drop a card mid-batch. Report it as unmatched rather than failing the box.
            results.append(_unmatched(index, row, "unmatched"))
            continue
        (touched_old if stack.id in pre_existing else touched_new).add(stack.id)

        name, set_code, collector_number = cards.get(m.scryfall_id, (row.name, None, None))
        results.append(
            ScanRowResult(
                index=index, matched=True, method=m.method, quantity=row.quantity,
                scryfall_id=m.scryfall_id, name=name, set_code=set_code,
                collector_number=collector_number,
            )
        )

    if commit:
        await session.commit()
    inserted, updated = len(touched_new), len(touched_old)

    matched_count = sum(1 for r in results if r.matched)
    return ScanReport(
        total_rows=len(results),
        matched=matched_count,
        unmatched=len(results) - matched_count,
        inserted=inserted,
        updated=updated,
        total_quantity=sum(r.quantity for r in results if r.matched),
        location=location,
        rows=results,
    )
