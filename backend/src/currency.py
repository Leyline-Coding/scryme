"""Display currency for *current-value* prices (USD / EUR).

Scryfall's ``prices`` JSON carries both ``usd``/``usd_foil`` and ``eur``/``eur_foil``, so switching
the displayed currency is a key-selection + formatting concern — no FX conversion. The per-visitor
choice rides in the ``scryme_currency`` cookie (set by the picker), defaulting to
``SCRYME_DEFAULT_CURRENCY``.

Note: the **price history** page (snapshots, P/L, movers) stays in USD — it's built on stored USD
snapshots and recorded purchase prices, which we can't convert without an exchange rate.
"""

from __future__ import annotations

from fastapi import Request

from src.config import get_settings

DEFAULT = "usd"

CURRENCIES: dict[str, dict] = {
    "usd": {"code": "usd", "symbol": "$", "label": "USD", "key": "usd", "foil": "usd_foil"},
    "eur": {"code": "eur", "symbol": "€", "label": "EUR", "key": "eur", "foil": "eur_foil"},
}


def normalize(value: str | None) -> str | None:
    v = (value or "").strip().lower()
    return v if v in CURRENCIES else None


def info(currency: str | None) -> dict:
    return CURRENCIES[normalize(currency) or DEFAULT]


def unit_price(prices: dict | None, finish: str, currency: str) -> float:
    """Current price of one card in ``currency``, preferring the foil price for foil/etched."""
    prices = prices or {}
    c = info(currency)
    key = c["foil"] if finish in ("foil", "etched") else c["key"]
    raw = prices.get(key) or prices.get(c["key"])
    try:
        return float(raw) if raw else 0.0
    except (TypeError, ValueError):
        return 0.0


def get_currency(request: Request) -> str:
    """Active display currency from the cookie, falling back to the configured default."""
    cookie = normalize(request.cookies.get("scryme_currency"))
    return cookie or normalize(get_settings().default_currency) or DEFAULT
