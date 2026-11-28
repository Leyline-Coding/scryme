"""Collection preferences service (#203) — mirrors ``src.llm`` for the ``preferences`` singleton.

``get_preferences`` resolves the id==1 row, or synthesizes defaults from the operator env
(``SCRYME_DEFAULT_*``) and the built-in client defaults when no row exists — it never writes, so it
is safe on a read path (read-only demo). ``update_preferences`` upserts id==1 (like
``llm.save_config``); callers guard read-only.

The per-request cookie/localStorage *merge* (precedence between the singleton and a device's
cookies) lives with the request middleware (added in the wiring PR), not here.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.currency import normalize as normalize_currency
from src.models import Preferences as PreferencesRow
from src.pricing import normalize_source

# Built-in client defaults (mirror base.html pre-paint / _settings.html / search.py). currency and
# price_source default to the operator env values, resolved at call time.
_DEFAULTS: dict[str, Any] = {
    "search_filter": "", "movers": False, "view": "grid", "page_size": 60, "infinite": False,
    "hist_currency": None, "mode": "dark", "palette": "trop-orange", "accent": "",
    "foil_speed": 6, "spin": True, "spin_speed": 6, "card_size": 5, "facet_row_cap": 4000,
    "lang": "en",
}


@dataclass
class Prefs:
    """Resolved collection preferences (the value object stored on ``request.state.prefs`` and
    returned by the API). Field set mirrors the persisted columns minus the housekeeping ones."""
    currency: str = "usd"
    price_source: str = "tcgplayer"
    search_filter: str = ""
    movers: bool = False
    view: str = "grid"
    page_size: int = 60
    infinite: bool = False
    hist_currency: str | None = None
    show_sleeves: bool = True
    mode: str = "dark"
    palette: str = "trop-orange"
    accent: str = ""
    foil_speed: int = 6
    spin: bool = True
    spin_speed: int = 6
    card_size: int = 5
    facet_row_cap: int = 4000
    lang: str = "en"
    extra: dict = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_FIELD_NAMES = {f.name for f in fields(Prefs)}


def _defaults() -> Prefs:
    s = get_settings()
    return Prefs(
        currency=normalize_currency(s.default_currency) or "usd",
        price_source=normalize_source(s.default_price_source) or "tcgplayer",
        **_DEFAULTS,
    )


def _from_row(row: PreferencesRow) -> Prefs:
    return Prefs(**{name: getattr(row, name) for name in _FIELD_NAMES})


async def get_preferences(session: AsyncSession) -> Prefs:
    """The stored singleton, or env/built-in defaults when no row exists. Never writes."""
    row = await session.get(PreferencesRow, 1)
    return _from_row(row) if row is not None else _defaults()


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(value)))
    except (TypeError, ValueError):
        return default


def _normalize_field(name: str, value: Any) -> Any:
    """Coerce/validate a single field, falling back to its default on bad input."""
    if name == "currency":
        return normalize_currency(value) or _defaults().currency
    if name == "price_source":
        return normalize_source(value) or _defaults().price_source
    if name == "hist_currency":
        return normalize_currency(value)  # None when blank/invalid -> inherit currency
    if name == "view":
        return "list" if value == "list" else "grid"
    if name == "page_size":
        return _clamp(value, 1, 500, 60)
    if name in ("foil_speed", "spin_speed"):
        return _clamp(value, 1, 10, 6)
    if name == "card_size":
        return _clamp(value, 1, 10, 5)
    if name == "facet_row_cap":
        return _clamp(value, 1000, 20000, 4000)
    if name in ("movers", "infinite", "spin"):
        return bool(value)
    if name == "lang":
        # This is a language code; use the same validation as deck.py's normalize_language function
        from src.decks import _LANGUAGE_CODES
        code = (value or "").strip().lower()
        return code if code in _LANGUAGE_CODES else "en"
    if name == "extra":
        return value if isinstance(value, dict) else {}
    return value


async def update_preferences(session: AsyncSession, **fields_in: Any) -> Prefs:
    """Upsert the id==1 row, applying only the provided fields (validated). Caller guards
    read-only. Unknown keys are ignored. Returns the resolved preferences after the write."""
    row = await session.get(PreferencesRow, 1)
    if row is None:
        row = PreferencesRow(id=1)
        session.add(row)
    for name, value in fields_in.items():
        if name in _FIELD_NAMES and value is not None:
            setattr(row, name, _normalize_field(name, value))
    await session.commit()
    return _from_row(row)


# Cookie-backed prefs and the cookies they read (appearance prefs are localStorage-only — the client
# pre-paint applies those, so they never appear here).
_PAGE_SIZES = (30, 60, 120, 240)


def _apply_cookies(prefs: Prefs, cookies: dict) -> Prefs:
    """Overlay a device's cookie choices onto ``prefs`` (only where a cookie is present)."""
    if (c := normalize_currency(cookies.get("scryme_currency"))):
        prefs.currency = c
    if (c := normalize_source(cookies.get("scryme_price_source"))):
        prefs.price_source = c
    if "scryme_search_filter" in cookies:
        prefs.search_filter = cookies["scryme_search_filter"]
    if "scryme_movers" in cookies:
        prefs.movers = cookies["scryme_movers"] == "1"
    if "scryme_view" in cookies:
        prefs.view = "list" if cookies["scryme_view"] == "list" else "grid"
    if "scryme_infinite" in cookies:
        prefs.infinite = cookies["scryme_infinite"] == "1"
    if (hc := normalize_currency(cookies.get("scryme_hist_currency"))):
        prefs.hist_currency = hc
    try:
        n = int(cookies.get("scryme_page_size", ""))
        if n in _PAGE_SIZES:
            prefs.page_size = n
    except (TypeError, ValueError):
        pass
    return prefs


def resolve(row: PreferencesRow | None, cookies: dict, *, writable: bool) -> Prefs:
    """Effective per-request preferences.

    Precedence: when the instance is writable AND a saved singleton row exists, the singleton is
    authoritative (a synced device sees the account's choice). Otherwise — a fresh instance with no
    row yet, or the read-only demo — a device's own cookie wins so per-visitor choices still work.
    Appearance prefs are never cookie-backed here; the client pre-paint applies any localStorage
    device override on top of the injected singleton value.
    """
    base = _from_row(row) if row is not None else _defaults()
    if writable and row is not None:
        return base
    return _apply_cookies(base, cookies or {})


def sync_cookie_prefs_to_db(
    session: AsyncSession, row: PreferencesRow, cookies: dict
) -> dict[str, Any]:
    """Check cookie preferences against database values and return fields that need updating.

    This function is used by middleware to synchronize specific cookie preferences to the database
    when they differ from stored values.

    Returns a dict of field_name: new_value pairs for preferences that have changed.
    """
    # Define which cookie preferences should be synchronized to DB
    cookie_pref_mapping = {
        "page_size": ("scryme_page_size", int),
        "infinite": ("scryme_infinite", lambda x: x == "1"),
        "view": ("scryme_view", str),
    }

    update_fields = {}

    # Check each preference for changes
    for pref_name, (cookie_key, converter) in cookie_pref_mapping.items():
        try:
            cookie_value = cookies.get(cookie_key)
            if cookie_value is not None:
                converted_cookie_value = converter(cookie_value)
                db_value = getattr(row, pref_name)
                if converted_cookie_value != db_value:
                    update_fields[pref_name] = converted_cookie_value
        except (ValueError, TypeError):
            # If conversion fails, skip this preference
            continue

    return update_fields
