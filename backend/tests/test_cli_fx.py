"""CLI `refresh-fx` command (#232). refresh_fx_rates is mocked — no network."""

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
