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
    "foil_speed": 6, "spin": True, "spin_speed": 6,
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
    mode: str = "dark"
    palette: str = "trop-orange"
    accent: str = ""
    foil_speed: int = 6
    spin: bool = True
    spin_speed: int = 6
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
    if name in ("movers", "infinite", "spin"):
        return bool(value)
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
