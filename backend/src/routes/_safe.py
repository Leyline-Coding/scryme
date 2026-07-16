"""Response helpers that can't become open redirects."""

from __future__ import annotations

from urllib.parse import urlsplit

from fastapi.responses import RedirectResponse


def local_redirect(path: str, status_code: int = 303) -> RedirectResponse:
    """A ``RedirectResponse`` constrained to a same-origin, absolute path.

    Callers build ``path`` from a fixed prefix plus values such as ids, so an open
    redirect shouldn't be possible — but routing every such redirect through this guard
    makes that explicit: if the target carries a scheme or host, or is protocol-relative
    (``//host``), it's replaced with the app root before redirecting.
    """
    parts = urlsplit(path)
    if parts.scheme or parts.netloc or not path.startswith("/") or path.startswith("//"):
        path = "/"
    return RedirectResponse(url=path, status_code=status_code)
