"""Small validators for values that end up in a browser-interpreted context.

HTML-escaping (what Jinja's autoescape does) is only sufficient for element text and quoted
attribute *values*. It is not enough for a value that the browser goes on to interpret as
something else — CSS inside ``style=""``, a URL inside ``href=""``, or a selector/path handed to
htmx. Those need an allowlist, and this module is where the allowlists live so that the write
path and the render path agree on them.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

# What ``<input type="color">`` submits is always ``#rrggbb``; the short form is accepted too
# because it is unambiguous and harmless. Named colours and ``rgb()`` are deliberately not: no UI
# produces them and every extra grammar is another way to smuggle ``;``/``url(`` into a style.
_HEX_COLOR = re.compile(r"#(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{6})")

# ``secrets.token_urlsafe`` output (share links, device tokens): url-safe base64, no padding.
_URLSAFE_TOKEN = re.compile(r"[A-Za-z0-9_-]{16,128}")

# An ``#id`` selector — all the inline editors ever target. The ``#`` is required: a bare word is
# a tag selector to htmx (``body``), and its extended forms (``closest …``, ``find …``) need spaces.
_TARGET = re.compile(r"#[A-Za-z][A-Za-z0-9_-]{0,63}")

# A same-site path: one leading slash, then only unreserved path characters. No ``//`` (which a
# browser resolves as a scheme-relative URL to another host), no backslash (which browsers treat
# as a slash), no query or fragment.
_LOCAL_PATH = re.compile(r"/(?:[A-Za-z0-9._~-]+/?)*")


def hex_color(value: object) -> str | None:
    """*value* as a ``#rgb``/``#rrggbb`` colour, or None if it is anything else."""
    if isinstance(value, str) and _HEX_COLOR.fullmatch(value.strip()):
        return value.strip()
    return None


def css_color(value: object, default: str = "#000000") -> str:
    """Jinja filter: a colour that is safe to drop into ``style=""`` — *default* if not valid."""
    return hex_color(value) or default


def urlsafe_token(value: object) -> str | None:
    """*value* if it has the shape of a ``secrets.token_urlsafe`` token, else None."""
    if isinstance(value, str) and _URLSAFE_TOKEN.fullmatch(value):
        return value
    return None


def http_url(value: object) -> str | None:
    """*value* if it is an absolute ``http(s)`` URL with a host, else None (Jinja filter too)."""
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    try:
        parts = urlsplit(candidate)
    except ValueError:
        return None
    if parts.scheme.lower() in ("http", "https") and parts.netloc:
        return candidate
    return None


def local_path(value: object) -> str | None:
    """*value* if it is a plain same-site path (``/decks/3/rename``), else None."""
    if isinstance(value, str) and _LOCAL_PATH.fullmatch(value):
        return value
    return None


def target_selector(value: object) -> str | None:
    """*value* if it is an ``#id`` selector, else None."""
    if isinstance(value, str) and _TARGET.fullmatch(value):
        return value
    return None
