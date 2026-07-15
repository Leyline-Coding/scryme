"""Preferred price source (#231): overlay a chosen marketplace's prices onto Scryfall's.

The per-visitor choice rides in the ``scryme_price_source`` cookie (set by the picker), defaulting
to ``SCRYME_DEFAULT_PRICE_SOURCE``. ``effective_prices`` returns a Scryfall-shaped ``prices`` dict
with the USD keys swapped for the chosen source's values (Card Kingdom / ManaPool, cached in
``cards.market_prices``), falling back to TCGplayer (Scryfall ``usd``) whenever the source has no
price for a card — so every downstream ``unit_price``/currency computation keeps working unchanged.
"""

from __future__ import annotations

from fastapi import Request
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings

DEFAULT = "tcgplayer"

# label = what the picker shows; note = short provenance line.
SOURCES: dict[str, dict] = {
    "tcgplayer": {"code": "tcgplayer", "label": "TCGplayer", "note": "Market price (Scryfall)"},
    "cardkingdom": {"code": "cardkingdom", "label": "Card Kingdom", "note": "Retail (MTGJSON)"},
    "manapool": {"code": "manapool", "label": "ManaPool", "note": "Market price"},
}
_USD_KEYS = ("usd", "usd_foil", "usd_etched")


def normalize_source(value: str | None) -> str | None:
    v = (value or "").strip().lower()
    return v if v in SOURCES else None


def get_price_source(request: Request) -> str:
    """Active price source from the cookie, falling back to the configured default."""
    cookie = normalize_source(request.cookies.get("scryme_price_source"))
    return cookie or normalize_source(get_settings().default_price_source) or DEFAULT


def resolve_prices(
    prices: dict | None, market_prices: dict | None, source: str | None = DEFAULT
) -> dict | None:
    """Scryfall ``prices`` with USD keys overridden by ``source`` (fallback: Scryfall/TCGplayer).

    The dict-level core used by callers that select ``prices``/``market_prices`` as columns.
    """
    base = prices or {}
    if not source or source == "tcgplayer":
        return prices
    override = (market_prices or {}).get(source) or {}
    if not override:
        return prices
    merged = dict(base)
    for key in _USD_KEYS:
        if override.get(key):
            merged[key] = override[key]
    return merged


def effective_prices(card, source: str | None = DEFAULT) -> dict | None:
    """``resolve_prices`` for a Card-like object with ``.prices`` and ``.market_prices``."""
    return resolve_prices(getattr(card, "prices", None), getattr(card, "market_prices", None),
                          source)


async def available_sources(session: AsyncSession) -> list[str]:
    """Sources with usable data: TCGplayer always; the others only once synced (data present)."""
    out = ["tcgplayer"]
    for src in ("cardkingdom", "manapool"):
        present = await session.scalar(
            text("SELECT 1 FROM cards WHERE market_prices ? :src LIMIT 1").bindparams(src=src)
        )
        if present:
            out.append(src)
    return out
