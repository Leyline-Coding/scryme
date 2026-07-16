"""Display currency for *current-value* prices.

Scryfall's ``prices`` JSON has ``usd``/``usd_foil`` and ``eur``/``eur_foil`` natively, so USD and
EUR are a pure key-selection. Other currencies (GBP/CAD/AUD/JPY) have no Scryfall price, so they
are shown by converting the USD price with a periodically-refreshed FX rate (see ``src/fx.py``).
The per-visitor choice rides in the ``scryme_currency`` cookie (set by the picker), defaulting to
``SCRYME_DEFAULT_CURRENCY``.

Note: the **price history** page (snapshots, P/L, movers) stays in USD — it's built on stored USD
snapshots and recorded purchase prices, kept honest in their source currency rather than converted.
"""

from __future__ import annotations

from fastapi import Request

from src import fx
from src.config import get_settings

DEFAULT = "usd"

# Native currencies use their own Scryfall keys; converted ones (``convert``) read the USD price and
# multiply by the USD->code FX rate from ``src/fx.py``.
CURRENCIES: dict[str, dict] = {
    "usd": {"code": "usd", "symbol": "$", "label": "USD", "key": "usd", "foil": "usd_foil"},
    "eur": {"code": "eur", "symbol": "€", "label": "EUR", "key": "eur", "foil": "eur_foil"},
    "gbp": {"code": "gbp", "symbol": "£", "label": "GBP",
            "key": "usd", "foil": "usd_foil", "convert": True},
    "cad": {"code": "cad", "symbol": "CA$", "label": "CAD",
            "key": "usd", "foil": "usd_foil", "convert": True},
    "aud": {"code": "aud", "symbol": "A$", "label": "AUD",
            "key": "usd", "foil": "usd_foil", "convert": True},
    "jpy": {"code": "jpy", "symbol": "¥", "label": "JPY",
            "key": "usd", "foil": "usd_foil", "convert": True},
}


def normalize(value: str | None) -> str | None:
    v = (value or "").strip().lower()
    return v if v in CURRENCIES else None


def info(currency: str | None) -> dict:
    return CURRENCIES[normalize(currency) or DEFAULT]


def unit_price(prices: dict | None, finish: str, currency: str) -> float:
    """Current price of one card in ``currency``, preferring the foil price for foil/etched.

    Converted currencies read the USD price and multiply by the current FX rate; if that rate
    hasn't loaded yet (a brand-new install before the first refresh), it degrades to the raw USD
    number rather than showing zero.
    """
    prices = prices or {}
    c = info(currency)
    key = c["foil"] if finish in ("foil", "etched") else c["key"]
    raw = prices.get(key) or prices.get(c["key"])
    try:
        value = float(raw) if raw else 0.0
    except (TypeError, ValueError):
        return 0.0
    if c.get("convert"):
        r = fx.rate(c["code"])
        if r:
            value *= r
    return value


def get_currency(request: Request) -> str:
    """Active display currency from the cookie, falling back to the configured default."""
    cookie = normalize(request.cookies.get("scryme_currency"))
    return cookie or normalize(get_settings().default_currency) or DEFAULT
