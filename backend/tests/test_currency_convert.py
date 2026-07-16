"""unit_price FX conversion for converted display currencies (#232)."""

import pytest
from src import currency, fx


@pytest.fixture(autouse=True)
def _rates():
    fx.FX_RATES.clear()
    fx.FX_RATES.update({"gbp": 0.80, "cad": 1.40, "aud": 1.50, "jpy": 150.0})
    yield
    fx.FX_RATES.clear()


def test_native_currencies_are_not_converted():
    prices = {"usd": "10.00", "usd_foil": "25.00", "eur": "9.00", "eur_foil": "22.00"}
    assert currency.unit_price(prices, "normal", "usd") == 10.0
    assert currency.unit_price(prices, "foil", "usd") == 25.0
    assert currency.unit_price(prices, "normal", "eur") == 9.0
    assert currency.unit_price(prices, "etched", "eur") == 22.0


def test_converted_currency_multiplies_the_usd_price():
    prices = {"usd": "10.00", "usd_foil": "25.00"}
    assert currency.unit_price(prices, "normal", "gbp") == pytest.approx(8.0)   # 10 * 0.80
    assert currency.unit_price(prices, "foil", "cad") == pytest.approx(35.0)    # 25 * 1.40
    assert currency.unit_price(prices, "normal", "jpy") == pytest.approx(1500.0)


def test_missing_rate_degrades_to_the_usd_number():
    fx.FX_RATES.clear()  # rates not loaded yet
    assert currency.unit_price({"usd": "10.00"}, "normal", "gbp") == 10.0


def test_metadata_for_new_currencies():
    assert currency.normalize("gbp") == "gbp"
    assert currency.info("cad")["symbol"] == "CA$"
    assert currency.info("jpy")["convert"] is True
    assert "convert" not in currency.info("usd")
