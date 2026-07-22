"""Command-line entrypoint for operational tasks.

Usage:
    python -m src.cli ingest [--force]      # download + ingest Default Cards bulk
    python -m src.cli backfill-images       # cache images for owned cards
    python -m src.cli seed-demo            # add the curated demo collection
"""

from __future__ import annotations

import argparse
import asyncio

from src.demo import seed_demo, seed_demo_decks
from src.scryfall.images import ImageCache
from src.scryfall.ingest import ingest_default_cards


async def _ingest(force: bool) -> None:
    result = await ingest_default_cards(force=force)
    if result.skipped:
        print(f"Skipped (cached); {result.card_count} cards already ingested.")
    else:
        print(f"Ingested {result.card_count} cards.")


async def _backfill() -> None:
    fetched = await ImageCache().backfill_owned()
    print(f"Cached {fetched} new images.")


async def _seed_demo() -> None:
    added = await seed_demo()
    print(f"Added {added} cards to the demo collection.")
    decks = await seed_demo_decks()
    print(f"Created {decks} example deck(s).")


async def _snapshot_prices() -> None:
    from src.prices import take_snapshot

    snap = await take_snapshot()
    if snap is None:
        print("No owned cards; nothing to snapshot.")
    else:
        print(f"Captured snapshot: ${snap.total_usd:,.2f} across {snap.card_count} cards.")


async def _seed_price_history(months: int) -> None:
    from src.db import SessionLocal
    from src.prices import seed_price_history

    async with SessionLocal() as session:
        n = await seed_price_history(session, months=months)
    print(f"Seeded {n} monthly price snapshots with per-card history."
          if n else "No owned, priced cards; nothing to seed.")


async def _refresh_fx() -> None:
    from src.fx import FX_RATES, refresh_fx_rates

    n = await refresh_fx_rates(force=True)
    if n:
        rates = ", ".join(f"{c}={FX_RATES[c]:.4f}" for c in sorted(FX_RATES))
        print(f"Refreshed {n} FX rate(s): {rates}")
    else:
        print("FX rates unchanged (fetch failed or nothing returned).")


async def _backfill_fx_history(code: str | None) -> None:
    from src.db import SessionLocal
    from src.fx import HIST_CODES, ensure_fx_history
    from src.prices import earliest_snapshot_date

    codes = [code.lower()] if code else list(HIST_CODES)
    async with SessionLocal() as session:
        start = await earliest_snapshot_date(session)
        if start is None:
            print("No price snapshots yet; nothing to backfill.")
            return
        for c in codes:
            ok = await ensure_fx_history(session, c, start)
            print(f"{c}: {'ok' if ok else 'no data (fetch failed?)'}")


async def _prune_digital() -> None:
    from src.scryfall.ingest import prune_digital_only

    removed = await prune_digital_only()
    print(f"Removed {removed} digital-only (Arena/MTGO) card(s).")


async def _backfill_embeddings(scope: str) -> None:
    from src.embeddings import run_backfill

    try:
        count = await run_backfill(scope)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return
    print(f"Embedded {count} card(s) ({scope}).")


async def _backfill_rules(file_path: str | None) -> None:
    from src.rules_rag import run_backfill_rules

    try:
        count = await run_backfill_rules(file_path)
    except RuntimeError as exc:
        print(f"Error: {exc}")
        return
    print(f"Embedded {count} comprehensive-rules chunk(s).")


async def _backfill_mtgjson_ids() -> None:
    from src.market_prices import backfill_mtgjson_ids

    n = await backfill_mtgjson_ids(force=True)
    print(f"Mapped {n} card(s) to MTGJSON ids (needed for Card Kingdom prices).")


async def _sync_market_prices(force: bool) -> None:
    from src.market_prices import sync_market_prices

    result = await sync_market_prices(force=force)
    print("Synced market prices: "
          f"Card Kingdom {result.get('cardkingdom', 0)}, ManaPool {result.get('manapool', 0)}.")
    if result.get("cardkingdom", 0) == 0:
        print("  (Card Kingdom empty? run `backfill-mtgjson-ids` once first.)")


async def _organize_locations() -> None:
    from src.collection_edit import organize_by_color_identity
    from src.db import SessionLocal

    async with SessionLocal() as session:
        n = await organize_by_color_identity(session)
    print(f"Filed {n} stack(s) into color-identity locations.")


async def _refresh_sets() -> None:
    from src.db import SessionLocal
    from src.set_calendar import refresh_sets

    async with SessionLocal() as session:
        n = await refresh_sets(session, force=True)
    print(f"Synced {n} set(s) for the release calendar.")


async def _backup(directory: str | None, passphrase: str | None) -> None:
    from pathlib import Path

    from src.backup import take_disk_backup
    from src.config import get_settings

    settings = get_settings()
    target = Path(directory) if directory else settings.backup_dir
    if target is None:
        print("No backup directory. Pass --dir or set SCRYME_BACKUP_DIR.")
        return
    path = await take_disk_backup(target, keep=settings.backup_keep,
                                  passphrase=passphrase or settings.backup_passphrase)
    print(f"Wrote backup: {path}")


async def _restore(path: str, apply: bool, passphrase: str | None) -> None:
    from pathlib import Path

    from src.backup import restore_from_path
    from src.config import get_settings
    from src.db import SessionLocal

    phrase = passphrase or get_settings().backup_passphrase
    async with SessionLocal() as session:
        result = await restore_from_path(session, Path(path), dry_run=not apply, passphrase=phrase)
    if not result.ok:
        print(f"Error: {result.error}")
        return
    verb = "Restored" if result.applied else "Would restore"
    print(f"{verb} {result.total} rows: " + ", ".join(f"{k}={v}" for k, v in result.counts.items()))
    if result.skipped_missing_cards:
        print(f"  ({result.skipped_missing_cards} skipped — card not in the database)")
    if not apply:
        print("Dry run. Re-run with --apply to replace your current data.")


def main() -> None:
    parser = argparse.ArgumentParser(prog="scryme")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest the Scryfall Default Cards bulk file")
    p_ingest.add_argument("--force", action="store_true", help="Ignore the 24h cache guard")

    sub.add_parser("backfill-images", help="Cache images for owned cards")

    sub.add_parser("seed-demo", help="Add sample cards to the collection (demo)")

    sub.add_parser("snapshot-prices", help="Capture a price snapshot of the owned collection")

    p_seedhist = sub.add_parser(
        "seed-price-history",
        help="Synthesize monthly per-card price history for the owned collection (dev/demo #5)",
    )
    p_seedhist.add_argument("--months", type=int, default=24,
                            help="How many months of history to generate (default: 24)")

    sub.add_parser("refresh-fx", help="Refresh FX rates for converted display currencies (#232)")

    p_fxhist = sub.add_parser(
        "backfill-fx-history",
        help="Download historical FX rates for the card price-history chart (#233)",
    )
    p_fxhist.add_argument("--code", help="One currency code (default: all convertible currencies)")

    sub.add_parser("prune-digital", help="Remove digital-only (Arena/MTGO) cards from the DB")

    p_embed = sub.add_parser("backfill-embeddings",
                             help="Compute card-text embeddings for semantic similarity")
    p_embed.add_argument("--all", action="store_true",
                         help="Embed every card (default: only owned cards)")

    p_rules = sub.add_parser("backfill-rules",
                             help="Embed the comprehensive rules for grounded rules Q&A")
    p_rules.add_argument("--file", help="Path to the comprehensive rules .txt (default: bundled)")

    sub.add_parser("organize-locations",
                   help="Set each card's storage location to its color-identity group")

    sub.add_parser("refresh-sets", help="Sync the set-release calendar from Scryfall")

    sub.add_parser("backfill-mtgjson-ids",
                   help="Map cards to MTGJSON ids (one-time; needed for Card Kingdom prices)")
    p_market = sub.add_parser("sync-market-prices",
                              help="Sync Card Kingdom (MTGJSON) + ManaPool prices (#231)")
    p_market.add_argument("--force", action="store_true", help="Ignore the daily cache guard")

    p_backup = sub.add_parser("backup", help="Write a backup of your data to disk")
    p_backup.add_argument("--dir", help="Target directory (default: SCRYME_BACKUP_DIR)")
    p_backup.add_argument("--passphrase", help="Encrypt the backup with this passphrase")

    p_restore = sub.add_parser("restore", help="Restore your data from a backup file")
    p_restore.add_argument("file", help="Path to a scryme backup .json file")
    p_restore.add_argument("--apply", action="store_true",
                           help="Apply the restore (default is a dry-run preview)")
    p_restore.add_argument("--passphrase", help="Passphrase for an encrypted backup")

    args = parser.parse_args()
    # command -> a no-arg callable returning the coroutine to run. Commands that take arguments use
    # a small lambda to bind them from `args`; the rest reference their worker directly.
    handlers = {
        "ingest": lambda: _ingest(args.force),
        "backfill-images": _backfill,
        "seed-demo": _seed_demo,
        "snapshot-prices": _snapshot_prices,
        "seed-price-history": lambda: _seed_price_history(args.months),
        "refresh-fx": _refresh_fx,
        "backfill-fx-history": lambda: _backfill_fx_history(args.code),
        "prune-digital": _prune_digital,
        "backfill-embeddings": lambda: _backfill_embeddings("all" if args.all else "owned"),
        "backfill-rules": lambda: _backfill_rules(args.file),
        "organize-locations": _organize_locations,
        "refresh-sets": _refresh_sets,
        "backfill-mtgjson-ids": _backfill_mtgjson_ids,
        "sync-market-prices": lambda: _sync_market_prices(args.force),
        "backup": lambda: _backup(args.dir, args.passphrase),
        "restore": lambda: _restore(args.file, args.apply, args.passphrase),
    }
    asyncio.run(handlers[args.command]())


if __name__ == "__main__":  # pragma: no cover
    main()
