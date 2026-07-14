"""Command-line entrypoint for operational tasks.

Usage:
    python -m src.cli ingest [--force]      # download + ingest Default Cards bulk
    python -m src.cli backfill-images       # cache images for owned cards
    python -m src.cli seed-demo [--limit N] # add sample cards to the collection (demo)
"""

from __future__ import annotations

import argparse
import asyncio

from src.demo import DEFAULT_LIMIT, seed_demo, seed_demo_decks
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


async def _seed_demo(limit: int) -> None:
    added = await seed_demo(limit)
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


async def _organize_locations() -> None:
    from src.collection_edit import organize_by_color_identity
    from src.db import SessionLocal

    async with SessionLocal() as session:
        n = await organize_by_color_identity(session)
    print(f"Filed {n} stack(s) into color-identity locations.")


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

    p_demo = sub.add_parser("seed-demo", help="Add sample cards to the collection (demo)")
    p_demo.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="How many cards to add")

    sub.add_parser("snapshot-prices", help="Capture a price snapshot of the owned collection")

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

    p_backup = sub.add_parser("backup", help="Write a backup of your data to disk")
    p_backup.add_argument("--dir", help="Target directory (default: SCRYME_BACKUP_DIR)")
    p_backup.add_argument("--passphrase", help="Encrypt the backup with this passphrase")

    p_restore = sub.add_parser("restore", help="Restore your data from a backup file")
    p_restore.add_argument("file", help="Path to a scryme backup .json file")
    p_restore.add_argument("--apply", action="store_true",
                           help="Apply the restore (default is a dry-run preview)")
    p_restore.add_argument("--passphrase", help="Passphrase for an encrypted backup")

    args = parser.parse_args()
    if args.command == "ingest":
        asyncio.run(_ingest(args.force))
    elif args.command == "backfill-images":
        asyncio.run(_backfill())
    elif args.command == "seed-demo":
        asyncio.run(_seed_demo(args.limit))
    elif args.command == "snapshot-prices":
        asyncio.run(_snapshot_prices())
    elif args.command == "prune-digital":
        asyncio.run(_prune_digital())
    elif args.command == "backfill-embeddings":
        asyncio.run(_backfill_embeddings("all" if args.all else "owned"))
    elif args.command == "backfill-rules":
        asyncio.run(_backfill_rules(args.file))
    elif args.command == "organize-locations":
        asyncio.run(_organize_locations())
    elif args.command == "backup":
        asyncio.run(_backup(args.dir, args.passphrase))
    elif args.command == "restore":
        asyncio.run(_restore(args.file, args.apply, args.passphrase))


if __name__ == "__main__":
    main()
