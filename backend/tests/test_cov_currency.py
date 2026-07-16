"""Coverage tests for src/currency.py: the bad-value branch and cookie resolution."""

from types import SimpleNamespace

from src.currency import get_currency, unit_price


def test_unit_price_bad_value_returns_zero():
    # Non-numeric price -> ValueError/TypeError caught -> 0.0 (covers the except branch).
    assert unit_price({"usd": "not-a-number"}, "normal", "usd") == 0.0
    assert unit_price({"usd_foil": object()}, "foil", "usd") == 0.0


def _req(cookie=None):
    return SimpleNamespace(cookies={"scryme_currency": cookie} if cookie else {})


def test_get_currency_cookie_and_default():
    # No cookie -> configured default (usd).
    assert get_currency(_req()) == "usd"
    # Valid cookie honored.
    assert get_currency(_req("eur")) == "eur"
    # Unknown cookie value falls back to the default.
    assert get_currency(_req("gbp")) == "usd"
