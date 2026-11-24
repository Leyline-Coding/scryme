"""Request boundary: the checks every request passes before it reaches a route.

scryme has no accounts, so a route cannot ask "who is this?". What it can rely on is decided here,
once, instead of in each route:

* **Host** — a browser tricked by DNS rebinding sends the attacker's hostname. IP literals and
  ``localhost`` are always fine; other names must be listed in ``SCRYME_ALLOWED_HOSTS`` when that
  is set, and always on the desktop build (where nothing but loopback or a LAN address is right).
* **Origin** — a state-changing request must come from the app's own pages, not from another
  site's form or script (CSRF).
* **Login** — when ``SCRYME_API_TOKEN`` is set it protects the whole app, not only ``/api/v1``:
  the HTML UI needs a session cookie obtained by presenting that token at ``/login``.
* **Read-only** — a read-only instance refuses every state-changing method centrally. Individual
  routes still guard themselves, but a new route that forgets no longer opens a hole.
* **Rate limit** — per client address, with tighter limits on login and on expensive routes.
* **Headers** — ``nosniff``, frame denial, a referrer policy and a Content-Security-Policy.
"""

from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import time
from urllib.parse import quote, urlsplit

from fastapi import FastAPI
from fastapi.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.requests import Request

from src.config import Settings, get_settings
from src.lan import is_loopback

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
SESSION_COOKIE = "scryme_session"
READ_ONLY_DETAIL = "This instance is read-only."

# Paths reachable without a login even when a token is set: liveness, assets, public share links,
# the login form itself, and the JSON API (which checks the token itself and also accepts scoped
# per-device tokens that must not unlock the UI).
_OPEN_EXACT = frozenset({"/health", "/login", "/logout", "/favicon.ico"})
_OPEN_PREFIXES = ("/static/", "/images/", "/share/", "/api/v1/")

# State-changing methods a read-only instance still serves, because they change nothing the
# visitor could not already see. ``fx-history`` fills a shared cache of reference FX rates (#233)
# for a fixed set of currencies; the restore routes only *preview* in read-only mode (the route
# refuses ``mode=apply`` itself) and are bounded in size and key-derivation cost.
_READ_ONLY_ALLOWED = re.compile(
    r"^(?:/card/fx-history|/decks/\d+/suggest-owned|/backup/restore|/backup/disk/restore"
    r"|/login|/logout)$"
)

# Routes that are expensive (LLM calls, whole-database reads, uploads, key derivation).
_HEAVY = re.compile(
    r"^(?:/backup(?:/|$)|/export(?:/|$)|/upload(?:/|$)|/ai(?:/|$)|/card/[^/]+/ask$"
    r"|/decks/\d+/(?:ai/|upgrade|analyze|suggest|chat)|/api/v1/(?:import|scan)(?:/|$))"
)

CONTENT_SECURITY_POLICY = "; ".join([
    "default-src 'self'",
    # Alpine and the inline handlers need inline + eval; what the policy does buy is that script
    # can only be *loaded* from the app itself, and nothing can frame or re-target the page.
    "script-src 'self' 'unsafe-inline' 'unsafe-eval'",
    "style-src 'self' 'unsafe-inline'",
    # Card art falls back to Scryfall's image host until it is cached locally.
    "img-src 'self' data: blob: https:",
    "font-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])

_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Cross-Origin-Opener-Policy": "same-origin",
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
}


# --- host -----------------------------------------------------------------------------------------

def hostname(netloc: str | None) -> str:
    """The lower-cased host of a ``Host`` header or URL authority, without port or brackets."""
    if not netloc:
        return ""
    value = netloc.strip().lower()
    if "@" in value:
        value = value.rsplit("@", 1)[1]
    if value.startswith("["):
        return value[1:].split("]", 1)[0]
    return value.split(":", 1)[0]


def _is_ip(name: str) -> bool:
    try:
        ipaddress.ip_address(name)
    except ValueError:
        return False
    return True


def allowed_hosts(settings: Settings) -> list[str]:
    return [h.strip().lower() for h in settings.allowed_hosts.split(",") if h.strip()]


def _matches(name: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        if pattern == "*" or name == pattern:
            return True
        if pattern.startswith("*.") and name.endswith(pattern[1:]):
            return True
    return False


def host_allowed(host_header: str | None, settings: Settings) -> bool:
    """Whether a request's ``Host`` names this instance rather than someone else's domain."""
    name = hostname(host_header)
    patterns = allowed_hosts(settings)
    strict = bool(patterns) or settings.lan_guard
    if not name:
        return not strict
    # A rebinding attack needs a public DNS name the attacker controls. Literals, and names only
    # the local network can resolve (``localhost``, ``nas``, ``nas.local``), are always safe.
    if _is_ip(name) or "." not in name or name.endswith((".localhost", ".local")):
        return True
    if patterns:
        return _matches(name, patterns)
    return not strict


# --- origin ---------------------------------------------------------------------------------------

def is_cross_site(request: Request, settings: Settings) -> bool:
    """True for a state-changing request that another site's page caused."""
    if request.method in SAFE_METHODS:
        return False
    fetch_site = request.headers.get("sec-fetch-site", "").lower()
    if fetch_site in ("same-origin", "none"):
        return False
    origin = request.headers.get("origin")
    if origin is None:
        # No Origin: not a cross-origin browser request (curl, the scanner app, a native client),
        # unless the browser said outright that it was.
        return fetch_site == "cross-site"
    if origin == "null":
        return True
    source = hostname(urlsplit(origin).netloc)
    if fetch_site == "same-site":
        # A sibling subdomain or another port on this host: only an exact match will do.
        return urlsplit(origin).netloc.lower() != (request.headers.get("host") or "").lower()
    own = {hostname(request.headers.get("host")),
           hostname(request.headers.get("x-forwarded-host"))}
    own.discard("")
    return source not in own and not _matches(source, allowed_hosts(settings))


# --- login ----------------------------------------------------------------------------------------

def session_value(token: str) -> str:
    """The cookie that proves the token was presented. Changing the token invalidates it."""
    return hmac.new(token.encode(), b"scryme-ui-session-v1", hashlib.sha256).hexdigest()


def _presented(request: Request) -> str:
    auth = request.headers.get("authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return request.headers.get("x-api-key", "")


def is_authenticated(request: Request, token: str) -> bool:
    cookie = request.cookies.get(SESSION_COOKIE, "")
    if cookie and hmac.compare_digest(cookie, session_value(token)):
        return True
    provided = _presented(request)
    return bool(provided) and hmac.compare_digest(provided, token)


def needs_login(path: str) -> bool:
    return path not in _OPEN_EXACT and not path.startswith(_OPEN_PREFIXES)


def _login_required(request: Request) -> Response:
    target = request.url.path + (f"?{request.url.query}" if request.url.query else "")
    login = f"/login?next={quote(target, safe='')}"
    if request.headers.get("hx-request"):
        return PlainTextResponse("Sign in required.", status_code=401,
                                 headers={"HX-Redirect": "/login"})
    if request.method == "GET" and "text/html" in request.headers.get("accept", ""):
        return RedirectResponse(login, status_code=303)
    return JSONResponse({"detail": "Sign in required."}, status_code=401,
                        headers={"WWW-Authenticate": "Bearer"})


# --- rate limit -----------------------------------------------------------------------------------

class RateLimiter:
    """Fixed-window counters per (client, bucket). In-process, which is all one instance needs."""

    def __init__(self) -> None:
        self._hits: dict[tuple[str, str], tuple[int, int]] = {}

    def check(self, client: str, bucket: str, limit: int, *, window: int = 60,
              now: float | None = None) -> int:
        """Count one hit. Returns 0 when allowed, else the seconds until the window resets."""
        if limit <= 0:
            return 0
        moment = time.monotonic() if now is None else now
        slot = int(moment // window)
        key = (client, bucket)
        seen_slot, count = self._hits.get(key, (slot, 0))
        if seen_slot != slot:
            count = 0
        count += 1
        self._hits[key] = (slot, count)
        if len(self._hits) > 20_000:
            self._hits = {k: v for k, v in self._hits.items() if v[0] == slot}
        if count > limit:
            return max(1, int(window - (moment % window)))
        return 0

    def reset(self) -> None:
        self._hits.clear()


limiter = RateLimiter()


def client_address(request: Request, settings: Settings) -> str:
    """The client to bill a request to: a trusted proxy header if configured, else the peer."""
    header = settings.client_ip_header.strip()
    if header:
        value = request.headers.get(header, "").split(",")[0].strip()
        if value:
            return value
    return request.client.host if request.client else ""


def bucket_for(request: Request) -> tuple[str, str] | None:
    """Which limit applies, as (bucket, settings attribute). ``None`` for static assets."""
    path = request.url.path
    if path.startswith(("/static/", "/images/")):
        return None
    if path == "/login" and request.method == "POST":
        return "login", "rate_limit_login"
    if _HEAVY.match(path):
        return "heavy", "rate_limit_heavy"
    return "default", "rate_limit_default"


def _limits_active(settings: Settings) -> bool:
    # The test client fires thousands of requests from one address; tests that exercise the
    # limiter switch it on explicitly.
    return settings.rate_limit_enabled and settings.environment != "test"


def rate_limited(request: Request, settings: Settings) -> int:
    """Seconds to wait, or 0 when the request may proceed."""
    if not _limits_active(settings):
        return 0
    chosen = bucket_for(request)
    if chosen is None:
        return 0
    client = client_address(request, settings)
    # The desktop window and a local browser are the owner; throttling them only gets in the way.
    if is_loopback(client) and not settings.client_ip_header.strip():
        return 0
    bucket, attribute = chosen
    return limiter.check(client or "unknown", bucket, getattr(settings, attribute))


# --- wiring ---------------------------------------------------------------------------------------

def _reject(request: Request, settings: Settings) -> Response | None:
    """The response that stops this request at the boundary, or ``None`` to let it through."""
    if not host_allowed(request.headers.get("host"), settings):
        return PlainTextResponse(
            "This hostname is not allowed. Add it to SCRYME_ALLOWED_HOSTS.", status_code=400
        )
    wait = rate_limited(request, settings)
    if wait:
        return JSONResponse({"detail": "Too many requests. Slow down and try again."},
                            status_code=429, headers={"Retry-After": str(wait)})
    if is_cross_site(request, settings):
        return JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
    path = request.url.path
    if settings.api_token and needs_login(path) and not is_authenticated(
        request, settings.api_token
    ):
        return _login_required(request)
    if (settings.read_only and request.method not in SAFE_METHODS
            and not _READ_ONLY_ALLOWED.match(path)):
        return JSONResponse({"detail": READ_ONLY_DETAIL}, status_code=403)
    return None


def install(app: FastAPI) -> None:
    @app.middleware("http")
    async def request_boundary(request: Request, call_next):
        settings = get_settings()
        response = _reject(request, settings) or await call_next(request)
        for name, value in _HEADERS.items():
            response.headers.setdefault(name, value)
        return response
