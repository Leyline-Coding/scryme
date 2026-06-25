"""Command-line entrypoint for operational tasks.

Usage:
    python -m src.cli ingest [--force]      # download + ingest Default Cards bulk
    python -m src.cli backfill-images       # cache images for owned cards
"""

from __future__ import annotations

import argparse
import asyncio

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


def main() -> None:
    parser = argparse.ArgumentParser(prog="scryme")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest the Scryfall Default Cards bulk file")
    p_ingest.add_argument("--force", action="store_true", help="Ignore the 24h cache guard")

    sub.add_parser("backfill-images", help="Cache images for owned cards")

    args = parser.parse_args()
    if args.command == "ingest":
        asyncio.run(_ingest(args.force))
    elif args.command == "backfill-images":
        asyncio.run(_backfill())


if __name__ == "__main__":
    main()
