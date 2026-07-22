"""Coverage tests for src/cli.py — every subcommand dispatch, with the worker mocked out.

The CLI's worker coroutines import their dependencies lazily (inside the function body), so we
patch each dependency on its *source* module; the lazy import then picks up the fake at call time.
Each test drives ``cli.main()`` with a fake ``sys.argv`` (main() runs the coroutine via
asyncio.run itself), mirroring tests/test_cli.py.
"""

from __future__ import annotations

import sys
import types

import pytest
import src.cli as cli
from src.backup import RestoreResult


def _run(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["scryme", *argv])
    cli.main()


def test_ingest_reports_and_skipped(monkeypatch, capsys):
    from src.scryfall.ingest import IngestResult

    async def fake_ingest(force=False):
        return IngestResult(skipped=False, card_count=3, source_updated_at=None)

    monkeypatch.setattr(cli, "ingest_default_cards", fake_ingest)
    _run(monkeypatch, ["ingest"])
    assert "Ingested 3 cards" in capsys.readouterr().out

    async def fake_skip(force=False):
        return IngestResult(skipped=True, card_count=9, source_updated_at=None)

    monkeypatch.setattr(cli, "ingest_default_cards", fake_skip)
    _run(monkeypatch, ["ingest"])
    assert "Skipped (cached); 9 cards" in capsys.readouterr().out


def test_backfill_images(monkeypatch, capsys):
    async def fake_backfill(self, *a, **k):
        return 2

    monkeypatch.setattr(cli.ImageCache, "backfill_owned", fake_backfill)
    _run(monkeypatch, ["backfill-images"])
    assert "Cached 2 new images" in capsys.readouterr().out


def test_seed_demo(monkeypatch, capsys):
    async def fake_seed():
        return 4

    async def fake_decks():
        return 1

    monkeypatch.setattr(cli, "seed_demo", fake_seed)
    monkeypatch.setattr(cli, "seed_demo_decks", fake_decks)
    _run(monkeypatch, ["seed-demo"])
    out = capsys.readouterr().out
    assert "Added 4 cards" in out and "Created 1 example deck" in out


def test_snapshot_prices_reports(monkeypatch, capsys):
    snap = types.SimpleNamespace(total_usd=1234.5, card_count=7)

    async def fake_take_snapshot():
        return snap

    monkeypatch.setattr("src.prices.take_snapshot", fake_take_snapshot)
    _run(monkeypatch, ["snapshot-prices"])
    out = capsys.readouterr().out
    assert "Captured snapshot" in out and "$1,234.50" in out and "7 cards" in out


def test_snapshot_prices_no_cards(monkeypatch, capsys):
    async def fake_take_snapshot():
        return None

    monkeypatch.setattr("src.prices.take_snapshot", fake_take_snapshot)
    _run(monkeypatch, ["snapshot-prices"])
    assert "nothing to snapshot" in capsys.readouterr().out


def test_seed_price_history_reports(monkeypatch, capsys):
    async def fake_seed(session, months=24):
        return months

    monkeypatch.setattr("src.prices.seed_price_history", fake_seed)
    _run(monkeypatch, ["seed-price-history", "--months", "12"])
    assert "Seeded 12 monthly" in capsys.readouterr().out


def test_seed_price_history_no_cards(monkeypatch, capsys):
    async def fake_seed(session, months=24):
        return 0

    monkeypatch.setattr("src.prices.seed_price_history", fake_seed)
    _run(monkeypatch, ["seed-price-history"])
    assert "nothing to seed" in capsys.readouterr().out


def test_prune_digital(monkeypatch, capsys):
    async def fake_prune(*a, **k):
        return 4

    monkeypatch.setattr("src.scryfall.ingest.prune_digital_only", fake_prune)
    _run(monkeypatch, ["prune-digital"])
    assert "Removed 4 digital-only" in capsys.readouterr().out


def test_backfill_embeddings_owned(monkeypatch, capsys):
    seen = {}

    async def fake_backfill(scope):
        seen["scope"] = scope
        return 11

    monkeypatch.setattr("src.embeddings.run_backfill", fake_backfill)
    _run(monkeypatch, ["backfill-embeddings"])
    assert seen["scope"] == "owned"
    assert "Embedded 11 card(s) (owned)" in capsys.readouterr().out


def test_backfill_embeddings_all(monkeypatch, capsys):
    async def fake_backfill(scope):
        assert scope == "all"
        return 99

    monkeypatch.setattr("src.embeddings.run_backfill", fake_backfill)
    _run(monkeypatch, ["backfill-embeddings", "--all"])
    assert "Embedded 99 card(s) (all)" in capsys.readouterr().out


def test_backfill_embeddings_runtime_error(monkeypatch, capsys):
    async def fake_backfill(scope):
        raise RuntimeError("no model configured")

    monkeypatch.setattr("src.embeddings.run_backfill", fake_backfill)
    _run(monkeypatch, ["backfill-embeddings"])
    assert "Error: no model configured" in capsys.readouterr().out


def test_backfill_rules_default(monkeypatch, capsys):
    seen = {}

    async def fake_rules(file_path):
        seen["file"] = file_path
        return 42

    monkeypatch.setattr("src.rules_rag.run_backfill_rules", fake_rules)
    _run(monkeypatch, ["backfill-rules"])
    assert seen["file"] is None
    assert "Embedded 42 comprehensive-rules chunk(s)" in capsys.readouterr().out


def test_backfill_rules_with_file_and_error(monkeypatch, capsys):
    async def fake_rules(file_path):
        assert file_path == "/tmp/rules.txt"
        raise RuntimeError("missing file")

    monkeypatch.setattr("src.rules_rag.run_backfill_rules", fake_rules)
    _run(monkeypatch, ["backfill-rules", "--file", "/tmp/rules.txt"])
    assert "Error: missing file" in capsys.readouterr().out


def test_backfill_mtgjson_ids(monkeypatch, capsys):
    async def fake_backfill(force):
        assert force is True
        return 8

    monkeypatch.setattr("src.market_prices.backfill_mtgjson_ids", fake_backfill)
    _run(monkeypatch, ["backfill-mtgjson-ids"])
    assert "Mapped 8 card(s) to MTGJSON ids" in capsys.readouterr().out


def test_sync_market_prices(monkeypatch, capsys):
    async def fake_sync(force=False):
        assert force is True
        return {"cardkingdom": 3, "manapool": 5}

    monkeypatch.setattr("src.market_prices.sync_market_prices", fake_sync)
    _run(monkeypatch, ["sync-market-prices", "--force"])
    out = capsys.readouterr().out
    assert "Card Kingdom 3, ManaPool 5" in out
    assert "backfill-mtgjson-ids" not in out  # hint only shown when CK is empty


def test_sync_market_prices_empty_ck_hint(monkeypatch, capsys):
    async def fake_sync(force=False):
        return {"cardkingdom": 0, "manapool": 2}

    monkeypatch.setattr("src.market_prices.sync_market_prices", fake_sync)
    _run(monkeypatch, ["sync-market-prices"])
    assert "run `backfill-mtgjson-ids` once first" in capsys.readouterr().out


def test_organize_locations(monkeypatch, capsys):
    async def fake_org(session):
        return 6

    monkeypatch.setattr("src.collection_edit.organize_by_color_identity", fake_org)
    _run(monkeypatch, ["organize-locations"])
    assert "Filed 6 stack(s)" in capsys.readouterr().out


def test_refresh_sets(monkeypatch, capsys):
    async def fake_refresh(session, force=False):
        assert force is True
        return 12

    monkeypatch.setattr("src.set_calendar.refresh_sets", fake_refresh)
    _run(monkeypatch, ["refresh-sets"])
    assert "Synced 12 set(s)" in capsys.readouterr().out


def test_backup_with_dir(monkeypatch, capsys, tmp_path):
    async def fake_take(directory, keep=0, passphrase=""):
        return tmp_path / "scryme-backup-x.json"

    monkeypatch.setattr("src.backup.take_disk_backup", fake_take)
    _run(monkeypatch, ["backup", "--dir", str(tmp_path), "--passphrase", "pw"])
    assert "Wrote backup:" in capsys.readouterr().out


def test_backup_no_dir_configured(monkeypatch, capsys):
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "backup_dir", None)
    _run(monkeypatch, ["backup"])
    assert "No backup directory" in capsys.readouterr().out


def test_restore_dry_run(monkeypatch, capsys, tmp_path):
    async def fake_restore(session, path, dry_run=True, passphrase=""):
        assert dry_run is True
        return RestoreResult(ok=True, applied=False, counts={"collection_card": 2},
                             skipped_missing_cards=1)

    monkeypatch.setattr("src.backup.restore_from_path", fake_restore)
    f = tmp_path / "b.json"
    f.write_text("{}")
    _run(monkeypatch, ["restore", str(f)])
    out = capsys.readouterr().out
    assert "Would restore 2 rows" in out
    assert "1 skipped" in out
    assert "Dry run" in out


def test_restore_apply(monkeypatch, capsys, tmp_path):
    async def fake_restore(session, path, dry_run=True, passphrase=""):
        assert dry_run is False
        return RestoreResult(ok=True, applied=True, counts={"deck": 1})

    monkeypatch.setattr("src.backup.restore_from_path", fake_restore)
    f = tmp_path / "b.json"
    f.write_text("{}")
    _run(monkeypatch, ["restore", str(f), "--apply"])
    out = capsys.readouterr().out
    assert "Restored 1 rows" in out
    assert "Dry run" not in out


def test_restore_error(monkeypatch, capsys, tmp_path):
    async def fake_restore(session, path, dry_run=True, passphrase=""):
        return RestoreResult(ok=False, error="bad file")

    monkeypatch.setattr("src.backup.restore_from_path", fake_restore)
    f = tmp_path / "b.json"
    f.write_text("{}")
    _run(monkeypatch, ["restore", str(f)])
    assert "Error: bad file" in capsys.readouterr().out


def test_no_command_exits(monkeypatch):
    # A required subparser -> argparse errors out with SystemExit when none is given.
    monkeypatch.setattr(sys, "argv", ["scryme"])
    with pytest.raises(SystemExit):
        cli.main()
