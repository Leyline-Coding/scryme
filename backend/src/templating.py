"""Shared paths and the Jinja2 templates instance (avoids circular imports)."""

from pathlib import Path

from fastapi.templating import Jinja2Templates

from src.config import get_settings
from src.symbols import mana_symbols, set_symbol

BASE_DIR = Path(__file__).resolve().parent
TEMPLATES_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

# Render Scryfall {…} symbol tokens and set symbols via the vendored Mana/Keyrune fonts.
templates.env.filters["mana"] = mana_symbols
templates.env.globals["set_symbol"] = set_symbol
# True only in the desktop app (SCRYME_LAN_GUARD) — gates the "Share on LAN" affordance.
templates.env.globals["lan_available"] = lambda: get_settings().lan_guard
