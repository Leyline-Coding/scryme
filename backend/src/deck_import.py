"""Import a deck from a URL (#98): Moxfield / Archidekt / TappedOut.

Detects the host, fetches the deck via the site's public endpoint, and converts it to the plain
decklist text that ``decks.create_deck`` already understands. HTTP lives in ``fetch_deck_from_url``;
the per-host *parsing* is pure (``parse_moxfield`` / ``parse_archidekt``) so it's unit-testable
without the network. Public decks only.
"""

from __future__ import annotations

import re

import httpx

from src import __version__
from src.config import get_settings

SUPPORTED = "Moxfield, Archidekt, and TappedOut"
_UA = f"scryme/{__version__} (+https://github.com/Leyline-Coding/scryme)"
_TIMEOUT = 15.0
_DEFAULT_DECK_NAME = "Imported deck"


class DeckImportError(Exception):
    """A URL couldn't be fetched or parsed into a deck."""


_MOXFIELD = re.compile(r"moxfield\.com/decks/([A-Za-z0-9_-]+)")
_ARCHIDEKT = re.compile(r"archidekt\.com/decks/(\d+)")
_TAPPEDOUT = re.compile(r"tappedout\.net/mtg-decks/([A-Za-z0-9_-]+)")


def detect_host(url: str) -> str | None:
    if _MOXFIELD.search(url):
        return "moxfield"
    if _ARCHIDEKT.search(url):
        return "archidekt"
    if _TAPPEDOUT.search(url):
        return "tappedout"
    return None


def _lines(entries: list[tuple[int, str, str]]) -> str:
    """Build decklist text from (quantity, name, board) tuples."""
    main = [f"{q} {n}" for q, n, b in entries if b != "side" and n]
    side = [f"{q} {n}" for q, n, b in entries if b == "side" and n]
    text = "\n".join(main)
    if side:
        text += "\nSideboard\n" + "\n".join(side)
    return text


def parse_moxfield(payload: dict) -> tuple[str, str]:
    """Moxfield v2 deck JSON → (name, decklist text)."""
    name = (payload.get("name") or _DEFAULT_DECK_NAME).strip()
    entries: list[tuple[int, str, str]] = []
    # Commanders + mainboard are "main", sideboard is "side"; each board maps name -> {quantity}.
    for board_key, board in [("commanders", "main"), ("mainboard", "main"), ("sideboard", "side")]:
        cards = payload.get(board_key) or {}
        for card_name, info in cards.items():
            qty = int((info or {}).get("quantity", 1) or 1)
            entries.append((qty, card_name, board))
    if not entries:
        raise DeckImportError("That Moxfield deck looks empty or private.")
    return name, _lines(entries)


def parse_archidekt(payload: dict) -> tuple[str, str]:
    """Archidekt deck JSON → (name, decklist text)."""
    name = (payload.get("name") or _DEFAULT_DECK_NAME).strip()
    entries: list[tuple[int, str, str]] = []
    for item in payload.get("cards") or []:
        card = item.get("card") or {}
        oracle = card.get("oracleCard") or {}
        card_name = oracle.get("name") or card.get("name")
        if not card_name:
            continue
        cats = [c.lower() for c in (item.get("categories") or [])]
        board = "side" if ("sideboard" in cats or "maybeboard" in cats) else "main"
        entries.append((int(item.get("quantity", 1) or 1), card_name, board))
    if not entries:
        raise DeckImportError("That Archidekt deck looks empty or private.")
    return name, _lines(entries)


def _slug_name(slug: str) -> str:
    return slug.replace("-", " ").replace("_", " ").strip().title() or _DEFAULT_DECK_NAME


async def fetch_deck_from_url(
    url: str, *, client: httpx.AsyncClient | None = None
) -> tuple[str, str]:
    """Resolve a deck URL to (name, decklist text). Raises DeckImportError on any failure."""
    host = detect_host(url)
    if host is None:
        raise DeckImportError(f"Unsupported site. Supported: {SUPPORTED}.")

    settings = get_settings()
    headers = {"User-Agent": settings.scryfall_user_agent or _UA, "Accept": "application/json"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT, headers=headers, follow_redirects=True)
    try:
        if host == "moxfield":
            deck_id = _MOXFIELD.search(url).group(1)
            resp = await client.get(f"https://api.moxfield.com/v2/decks/all/{deck_id}")
            resp.raise_for_status()
            return parse_moxfield(resp.json())
        if host == "archidekt":
            deck_id = _ARCHIDEKT.search(url).group(1)
            resp = await client.get(f"https://archidekt.com/api/decks/{deck_id}/")
            resp.raise_for_status()
            return parse_archidekt(resp.json())
        # tappedout: the ?fmt=txt export is already a plain decklist.
        slug = _TAPPEDOUT.search(url).group(1)
        resp = await client.get(f"https://tappedout.net/mtg-decks/{slug}/", params={"fmt": "txt"})
        resp.raise_for_status()
        text = resp.text.strip()
        if not text:
            raise DeckImportError("That TappedOut deck looks empty or private.")
        return _slug_name(slug), text
    except httpx.HTTPStatusError as exc:
        raise DeckImportError(
            f"Couldn't fetch that deck (HTTP {exc.response.status_code}). Is it public?"
        ) from exc
    except httpx.HTTPError as exc:
        raise DeckImportError(f"Couldn't reach that site: {exc}") from exc
    finally:
        if own:
            await client.aclose()
