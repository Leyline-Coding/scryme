"""Coverage for the missing-seed-file branch in seed_demo_decks."""

import pytest
from src import demo


@pytest.mark.asyncio
async def test_seed_demo_decks_skips_missing_files(monkeypatch, tmp_path):
    """When a seed-deck file is absent, that deck is skipped (warned) rather than created."""
    # Point the deck dir at an empty temp dir so every EXAMPLE_DECKS file is "missing".
    monkeypatch.setattr(demo, "_DECK_DIR", tmp_path)
    created = await demo.seed_demo_decks()
    assert created == 0
