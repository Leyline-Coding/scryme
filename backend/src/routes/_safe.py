"""Response helpers that can't become open redirects."""

from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit

from fastapi.responses import RedirectResponse


def local_redirect(path: str, status_code: int = 303) -> RedirectResponse:
    """A ``RedirectResponse`` constrained to a same-origin, absolute path.

    The target is rebuilt from only the parsed *path* and *query* components with an empty
    scheme and host, so any scheme/host a caller-supplied value might carry is dropped and the
    redirect can never leave this origin. Anything that still isn't a plain ``/…`` path (e.g. a
    protocol-relative ``//host``) falls back to the app root.
    """
    parts = urlsplit(path)
    safe = urlunsplit(("", "", parts.path or "/", parts.query, ""))
    if not safe.startswith("/") or safe.startswith("//"):
        safe = "/"
    return RedirectResponse(url=safe, status_code=status_code)
