"""Render Scryfall symbol tokens as Mana/Keyrune icon-font markup.

Scryfall mana costs and oracle text embed symbols as ``{...}`` tokens (e.g. ``{2}{W}{U}``,
``{T}``, ``{W/U}``, ``{G/P}``). The vendored Mana font (``static/vendor/mana``) renders these via
``<i class="ms ms-...">`` classes. Set symbols use the Keyrune font (``ss ss-<set> ss-<rarity>``).

These produce HTML, so the non-symbol text is escaped and the result is marked safe; the Jinja
filters are registered in ``templating``.
"""

from __future__ import annotations

import re

from markupsafe import Markup, escape

_TOKEN = re.compile(r"\{([^}]+)\}")

# Tokens whose Mana class name isn't just the lowercased token with slashes removed.
_SPECIAL = {"T": "tap", "Q": "untap", "½": "half", "∞": "infinity"}

_RARITIES = {"common", "uncommon", "rare", "mythic", "special", "timeshifted"}


def _mana_class(inner: str) -> str:
    key = inner.strip()
    if key in _SPECIAL:
        return _SPECIAL[key]
    # {W}->w, {2}->2, {W/U}->wu, {2/W}->2w, {G/P}->gp
    return key.lower().replace("/", "")


def mana_symbols(text: str | None) -> Markup:
    """Replace ``{...}`` symbol tokens in *text* with Mana-font markup; escape the rest."""
    if not text:
        return Markup("")
    parts: list[str] = []
    last = 0
    for m in _TOKEN.finditer(text):
        parts.append(str(escape(text[last : m.start()])))
        cls = _mana_class(m.group(1))
        label = escape(m.group(0))
        parts.append(
            f'<i class="ms ms-{cls} ms-cost" role="img" aria-label="{label}" title="{label}"></i>'
        )
        last = m.end()
    parts.append(str(escape(text[last:])))
    return Markup("".join(parts))


def set_symbol(set_code: str | None, rarity: str | None = None) -> Markup:
    """Keyrune set symbol for *set_code*, tinted by *rarity* when known."""
    if not set_code:
        return Markup("")
    code = escape(set_code.lower())
    rar = (rarity or "").lower()
    rar_cls = f" ss-{rar}" if rar in _RARITIES else ""
    label = escape(set_code.upper())
    return Markup(
        f'<i class="ss ss-{code}{rar_cls}" role="img" '
        f'aria-label="{label} set symbol" title="{label}"></i>'
    )
