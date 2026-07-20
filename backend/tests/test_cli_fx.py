"""CLI `refresh-fx` / `backfill-fx-history` commands (#232/#233). FX layer mocked — no network."""

import datetime
import sys

import pytest
import src.cli as cli
from src import fx


@pytest.fixture(autouse=True)
def _clear_fx():
    fx.FX_RATES.clear()
    yield
    fx.FX_RATES.clear()


def test_refresh_fx_reports_rates(monkeypatch, capsys):
    async def fake_refresh(force=False):
        fx.FX_RATES.update({"gbp": 0.80, "jpy": 150.0})
        return 2

    monkeypatch.setattr("src.fx.refresh_fx_rates", fake_refresh)
    monkeypatch.setattr(sys, "argv", ["scryme", "refresh-fx"])
    cli.main()
    out = capsys.readouterr().out
    assert "Refreshed 2 FX rate(s)" in out
    assert "gbp=0.8000" in out and "jpy=150.0000" in out


def test_refresh_fx_unchanged(monkeypatch, capsys):
    async def fake_refresh(force=False):
        return 0

    monkeypatch.setattr("src.fx.refresh_fx_rates", fake_refresh)
    monkeypatch.setattr(sys, "argv", ["scryme", "refresh-fx"])
    cli.main()
    assert "unchanged" in capsys.readouterr().out


def test_backfill_fx_history_no_snapshots(monkeypatch, capsys):
    async def no_start(_session):
        return None

    monkeypatch.setattr("src.prices.earliest_snapshot_date", no_start)
    monkeypatch.setattr(sys, "argv", ["scryme", "backfill-fx-history"])
    cli.main()
    assert "No price snapshots yet" in capsys.readouterr().out


def test_backfill_fx_history_single_code(monkeypatch, capsys):
    async def a_start(_session):
        return datetime.date(2020, 1, 1)

    calls = []

    async def fake_ensure(_session, code, _start, _client=None):
        calls.append(code)
        return True

    monkeypatch.setattr("src.prices.earliest_snapshot_date", a_start)
    monkeypatch.setattr("src.fx.ensure_fx_history", fake_ensure)
    monkeypatch.setattr(sys, "argv", ["scryme", "backfill-fx-history", "--code", "GBP"])
    cli.main()
    assert "gbp: ok" in capsys.readouterr().out
    assert calls == ["gbp"]  # --code is normalized to lowercase, a single currency


def test_backfill_fx_history_all_codes(monkeypatch, capsys):
    async def a_start(_session):
        return datetime.date(2020, 1, 1)

    async def fake_ensure(_session, code, _start, _client=None):
        return code != "jpy"  # jpy simulates a failed/empty download

    monkeypatch.setattr("src.prices.earliest_snapshot_date", a_start)
    monkeypatch.setattr("src.fx.ensure_fx_history", fake_ensure)
    monkeypatch.setattr(sys, "argv", ["scryme", "backfill-fx-history"])
    cli.main()
    out = capsys.readouterr().out
    assert "eur: ok" in out
    assert "jpy: no data" in out
