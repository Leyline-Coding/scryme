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
MOXFIELD = "moxfield"
ARCHIDEKT = "archidekt"
TAPPEDOUT = "tappedout"
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
        return MOXFIELD
    if _ARCHIDEKT.search(url):
        return ARCHIDEKT
    if _TAPPEDOUT.search(url):
        return TAPPEDOUT
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


# --- public-profile deck listing (#299) ---------------------------------------------------------

PROFILE_PROVIDERS = (MOXFIELD, ARCHIDEKT)
_MOX_PROFILE = re.compile(r"moxfield\.com/users/([A-Za-z0-9_.-]+)")
_ARCH_PROFILE = re.compile(r"archidekt\.com/u/([A-Za-z0-9_.-]+)")
# Archidekt's numeric deckFormat codes -> readable names (best-effort; blank when unknown).
_ARCH_FORMATS = {
    1: "Standard", 2: "Modern", 3: "Commander", 4: "Legacy", 5: "Vintage", 6: "Pauper",
    7: "Custom", 8: "Frontier", 10: "Penny Dreadful", 11: "1v1 Commander", 12: "Pioneer",
    13: "Brawl", 14: "Oathbreaker", 15: "Pauper EDH",
}


@dataclass
class ProfileDeck:
    name: str
    url: str      # a single-deck URL that fetch_deck_from_url can import
    format: str
    count: int


def detect_profile(text: str) -> tuple[str, str] | None:
    """A profile URL -> (provider, username); None if it's not a recognized profile link."""
    m = _MOX_PROFILE.search(text or "")
    if m:
        return MOXFIELD, m.group(1)
    m = _ARCH_PROFILE.search(text or "")
    if m:
        return ARCHIDEKT, m.group(1)
    return None


def _profile_moxfield(payload: dict) -> list[ProfileDeck]:
    out: list[ProfileDeck] = []
    for d in payload.get("data") or []:
        if (d.get("visibility") or "public") != "public":
            continue  # public decks only
        url = d.get("publicUrl") or (
            f"https://moxfield.com/decks/{d['publicId']}" if d.get("publicId") else None)
        if not url:
            continue
        out.append(ProfileDeck(name=(d.get("name") or "Untitled deck").strip(), url=url,
                               format=(d.get("format") or "").strip(),
                               count=int(d.get("mainboardCount") or 0)))
    return out


def _profile_archidekt(payload: dict) -> list[ProfileDeck]:
    out: list[ProfileDeck] = []
    for d in payload.get("results") or []:
        if d.get("private") or not d.get("id"):
            continue
        out.append(ProfileDeck(name=(d.get("name") or "Untitled deck").strip(),
                               url=f"https://archidekt.com/decks/{d['id']}",
                               format=_ARCH_FORMATS.get(d.get("deckFormat"), ""),
                               count=int(d.get("size") or 0)))
    return out


async def fetch_profile_decks(
    provider: str, username: str, *, limit: int = 60, client: httpx.AsyncClient | None = None
) -> list[ProfileDeck]:
    """A user's public decks on Moxfield/Archidekt (capped at ``limit``). Raises DeckImportError."""
    username = (username or "").strip()
    if provider not in PROFILE_PROVIDERS or not username:
        raise DeckImportError("Enter a Moxfield or Archidekt username or profile URL.")
    settings = get_settings()
    headers = {"User-Agent": settings.scryfall_user_agent or _UA, "Accept": "application/json"}
    own = client is None
    client = client or httpx.AsyncClient(timeout=_TIMEOUT, headers=headers, follow_redirects=True)
    try:
        if provider == MOXFIELD:
            resp = await client.get(
                "https://api2.moxfield.com/v2/decks/search",
                params={"authorUserNames": username, "pageSize": limit, "pageNumber": 1},
            )
            resp.raise_for_status()
            decks = _profile_moxfield(resp.json())
        else:
            resp = await client.get(
                "https://archidekt.com/api/decks/v3/",
                params={"ownerUsername": username, "pageSize": limit},
            )
            resp.raise_for_status()
            decks = _profile_archidekt(resp.json())
        if not decks:
            raise DeckImportError(f"No public decks found for “{username}”.")
        return decks
    except httpx.HTTPStatusError as exc:
        raise DeckImportError(
            f"Couldn't fetch that profile (HTTP {exc.response.status_code})."
        ) from exc
    except httpx.HTTPError as exc:
        raise DeckImportError(f"Couldn't reach {provider}: {exc}") from exc
    finally:
        if own:
            await client.aclose()


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
        if host == MOXFIELD:
            deck_id = _MOXFIELD.search(url).group(1)
            resp = await client.get(f"https://api.moxfield.com/v2/decks/all/{deck_id}")
            resp.raise_for_status()
            return parse_moxfield(resp.json())
        if host == ARCHIDEKT:
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
