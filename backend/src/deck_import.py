"""Import a deck from a URL (#98): Moxfield / Archidekt / TappedOut.

Detects the host, fetches the deck via the site's public endpoint, and converts it to the plain
decklist text that ``decks.create_deck`` already understands. HTTP lives in ``fetch_deck_from_url``;
the per-host *parsing* is pure (``parse_moxfield`` / ``parse_archidekt``) so it's unit-testable
without the network. Public decks only.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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


@dataclass
class _Entry:
    quantity: int
    name: str
    board: str
    set_code: str = ""
    collector_number: str = ""
    finish: str = "normal"  # normal | foil | etched


# Provider finish labels (Moxfield "nonFoil"/"foil"/"etched", Archidekt "Normal"/"Foil"/"Etched").
_FINISH_TOKENS = {"foil": "foil", "etched": "etched", "etchedfoil": "etched"}


def _finish_of(value: str | None) -> str:
    return _FINISH_TOKENS.get((value or "").replace(" ", "").lower(), "normal")


def _render(e: _Entry) -> str:
    """One decklist line, carrying the exact printing and finish the source specified."""
    s = f"{e.quantity} {e.name}"
    if e.set_code and e.collector_number:
        s += f" ({e.set_code.upper()}) {e.collector_number}"
    if e.finish == "foil":
        s += " *F*"
    elif e.finish == "etched":
        s += " *E*"
    return s


def _lines(entries: list[_Entry]) -> str:
    """Build decklist text, preserving each line's printing + finish so imports stay faithful."""
    main = [_render(e) for e in entries if e.board != "side" and e.name]
    side = [_render(e) for e in entries if e.board == "side" and e.name]
    text = "\n".join(main)
    if side:
        text += "\nSideboard\n" + "\n".join(side)
    return text


def parse_moxfield(payload: dict) -> tuple[str, str]:
    """Moxfield v2 deck JSON → (name, decklist text)."""
    name = (payload.get("name") or _DEFAULT_DECK_NAME).strip()
    entries: list[_Entry] = []
    # Commanders + mainboard are "main", sideboard is "side"; each board maps name -> entry, where
    # the entry carries the chosen printing (card.set / card.cn) and finish.
    for board_key, board in [("commanders", "main"), ("mainboard", "main"), ("sideboard", "side")]:
        cards = payload.get(board_key) or {}
        for card_name, info in cards.items():
            info = info or {}
            card = info.get("card") or {}
            entries.append(_Entry(
                quantity=int(info.get("quantity", 1) or 1), name=card_name, board=board,
                set_code=(card.get("set") or ""),
                collector_number=str(card.get("cn") or ""),
                finish=_finish_of(info.get("finish")),
            ))
    if not entries:
        raise DeckImportError("That Moxfield deck looks empty or private.")
    return name, _lines(entries)


def _archidekt_entry(item: dict) -> _Entry | None:
    """One Archidekt card row → an entry, or None when the row carries no card name."""
    card = item.get("card") or {}
    oracle = card.get("oracleCard") or {}
    card_name = oracle.get("name") or card.get("name")
    if not card_name:
        return None
    cats = [c.lower() for c in (item.get("categories") or [])]
    board = "side" if ("sideboard" in cats or "maybeboard" in cats) else "main"
    edition = card.get("edition") or {}
    return _Entry(
        quantity=int(item.get("quantity", 1) or 1), name=card_name, board=board,
        set_code=(edition.get("editioncode") or ""),
        collector_number=str(card.get("collectorNumber") or ""),
        finish=_finish_of(item.get("modifier")),
    )


def parse_archidekt(payload: dict) -> tuple[str, str]:
    """Archidekt deck JSON → (name, decklist text)."""
    name = (payload.get("name") or _DEFAULT_DECK_NAME).strip()
    entries = [e for e in map(_archidekt_entry, payload.get("cards") or []) if e is not None]
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
