"""Command-line entrypoint for operational tasks.

Usage:
    python -m src.cli ingest [--force]      # download + ingest Default Cards bulk
    python -m src.cli backfill-images       # cache images for owned cards
    python -m src.cli seed-demo [--limit N] # add sample cards to the collection (demo)
"""

from __future__ import annotations

import argparse
import asyncio

from src.demo import DEFAULT_LIMIT, seed_demo
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


def main() -> None:
    parser = argparse.ArgumentParser(prog="scryme")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest the Scryfall Default Cards bulk file")
    p_ingest.add_argument("--force", action="store_true", help="Ignore the 24h cache guard")

    sub.add_parser("backfill-images", help="Cache images for owned cards")

    p_demo = sub.add_parser("seed-demo", help="Add sample cards to the collection (demo)")
    p_demo.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="How many cards to add")

    args = parser.parse_args()
    if args.command == "ingest":
        asyncio.run(_ingest(args.force))
    elif args.command == "backfill-images":
        asyncio.run(_backfill())
    elif args.command == "seed-demo":
        asyncio.run(_seed_demo(args.limit))


if __name__ == "__main__":
    main()
