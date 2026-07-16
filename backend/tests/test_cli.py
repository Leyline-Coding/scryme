"""CLI tests: ingest + backfill commands with the underlying work mocked out."""

import sys

import src.cli as cli
from src.scryfall.ingest import IngestResult


def test_cli_ingest_reports_count(monkeypatch, capsys):
    async def fake_ingest(force=False):
        assert force is True
        return IngestResult(skipped=False, card_count=5, source_updated_at=None)

    monkeypatch.setattr(cli, "ingest_default_cards", fake_ingest)
    monkeypatch.setattr(sys, "argv", ["scryme", "ingest", "--force"])
    cli.main()
    assert "Ingested 5 cards" in capsys.readouterr().out


def test_cli_ingest_skipped(monkeypatch, capsys):
    async def fake_ingest(force=False):
        return IngestResult(skipped=True, card_count=42, source_updated_at=None, reason="cached")

    monkeypatch.setattr(cli, "ingest_default_cards", fake_ingest)
    monkeypatch.setattr(sys, "argv", ["scryme", "ingest"])
    cli.main()
    assert "Skipped" in capsys.readouterr().out


def test_cli_backfill(monkeypatch, capsys):
    async def fake_backfill(self, *args, **kwargs):
        return 3

    monkeypatch.setattr(cli.ImageCache, "backfill_owned", fake_backfill)
    monkeypatch.setattr(sys, "argv", ["scryme", "backfill-images"])
    cli.main()
    assert "Cached 3 new images" in capsys.readouterr().out


def test_cli_seed_demo(monkeypatch, capsys):
    async def fake_seed():
        return 5

    monkeypatch.setattr(cli, "seed_demo", fake_seed)
    monkeypatch.setattr(sys, "argv", ["scryme", "seed-demo"])
    cli.main()
    assert "Added 5 cards" in capsys.readouterr().out
