"""Shared paths and the Jinja2 templates instance (avoids circular imports)."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from src import __version__
from src.config import get_settings
from src.symbols import mana_symbols, set_symbol

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"


def _inject_prefs(request) -> dict:
    """Make the resolved appearance preferences + a writable flag available to every template (for
    base.html's pre-paint theme hydration, #203). Empty/writable=False when the prefs middleware
    didn't populate ``request.state.prefs`` — base.html then falls back to localStorage only."""
    state = getattr(request, "state", None)
    prefs = getattr(state, "prefs", None)
    if prefs is None:
        return {"theme_prefs": None, "prefs_writable": False, "theme_authoritative": False}
    appearance = {
        "mode": prefs.mode, "palette": prefs.palette, "accent": prefs.accent,
        "foil_speed": prefs.foil_speed, "spin": prefs.spin, "spin_speed": prefs.spin_speed,
        "card_size": prefs.card_size,
    }
    writable = not get_settings().read_only
    # Server appearance wins over localStorage only when writable AND actually saved (a row exists),
    # so a fresh instance's defaults never overwrite an upgrading user's existing device theme.
    authoritative = writable and getattr(state, "prefs_saved", False)
    return {"theme_prefs": appearance, "prefs_writable": writable,
            "theme_authoritative": authoritative}


templates = Jinja2Templates(directory=str(TEMPLATES_DIR), context_processors=[_inject_prefs])

# Render Scryfall {…} symbol tokens and set symbols via the vendored Mana/Keyrune fonts.
templates.env.filters["mana"] = mana_symbols
templates.env.globals["set_symbol"] = set_symbol
# True only in the desktop app (SCRYME_LAN_GUARD) — gates the "Share on LAN" affordance.
templates.env.globals["lan_available"] = lambda: get_settings().lan_guard
# Shown in the footer for quick reference.
templates.env.globals["app_version"] = __version__
