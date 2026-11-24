"""Shared paths and the Jinja2 templates instance (avoids circular imports)."""

from pathlib import Path

from babel.numbers import format_currency
from fastapi.templating import Jinja2Templates

from src import __version__
from src.config import get_settings
from src.markdown import render_markdown
from src.sanitize import css_color, http_url
from src.symbols import mana_symbols, set_symbol

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
REUSABLES_DIR = TEMPLATES_DIR / "reusables"
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
            "theme_authoritative": authoritative, 'currency': prefs.currency.upper()}


def currency_filter(value:float, currency="USD", locale="en_US"):
    if value is None:
        return ""
    return format_currency(value, currency, locale=locale)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR), context_processors=[_inject_prefs])

if REUSABLES_DIR.exists() and REUSABLES_DIR.is_dir():
    jinja_env = templates.env
    for file in REUSABLES_DIR.iterdir():
        if file.is_file() and file.suffix in [".html", ".jinja", ".j2"]:
            jinja_env.globals[file.stem] = jinja_env.get_template(f"reusables/{file.name}").module

# International support for currency printing
templates.env.filters["currency_filter"] = currency_filter

# Render Scryfall {…} symbol tokens and set symbols via the vendored Mana/Keyrune fonts.
templates.env.filters["mana"] = mana_symbols
templates.env.globals["set_symbol"] = set_symbol
# True only in the desktop app (SCRYME_LAN_GUARD) — gates the "Share on LAN" affordance.
templates.env.globals["lan_available"] = lambda: get_settings().lan_guard
# Shown in the footer for quick reference.
templates.env.globals["app_version"] = __version__

templates.env.filters["markdown"] = render_markdown

# Allowlist filters for values that land in a CSS / URL context, where autoescape is not enough.
templates.env.filters["css_color"] = css_color
templates.env.filters["http_url"] = http_url
