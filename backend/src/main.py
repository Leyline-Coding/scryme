"""FastAPI application entrypoint.

Wires templates, static files, and routers. Feature routers (search, upload) are added in
later phases; this build ships the app skeleton, health/home/admin routes, and the scheduled
Scryfall bulk refresh.
"""

import asyncio
from contextlib import asynccontextmanager
from typing import cast

import structlog
from fastapi import FastAPI
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from slowapi.util import get_remote_address
from starlette.types import ExceptionHandler

from src import __version__
from src.card import card_name_text_search_matcher
from src.config import get_settings
from src.lan import access_decision, lan_state
from src.routes import (
    admin,
    ai,
    api,
    backup,
    binders,
    boxes,
    card,
    checklists,
    collection,
    decks,
    export,
    health,
    home,
    lan,
    mycollection,
    pair,
    preferences,
    prices,
    reusables,
    saved,
    search,
    sell,
    sets,
    share,
    sleeves,
    stats,
    trade,
    upload,
    watch,
    wishlist,
)
from src.routes import (
    settings as settings_routes,
)
from src.scheduler import shutdown_scheduler, start_scheduler
from src.templating import STATIC_DIR, templates

log = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.image_cache_dir.mkdir(parents=True, exist_ok=True)
    log.info("scryme.startup", version=__version__, environment=settings.environment)
    # The daily bulk refresh runs in-process. Skip it under tests and in read-only demos.
    if settings.environment != "test" and not settings.read_only:
        start_scheduler(refresh_hours=max(1, settings.bulk_refresh_min_hours))
    # Check for outstanding migrations. The test schema is built from ORM metadata, not Alembic,
    # so there is no revision to compare and every page would show the "migrations pending" gate.
    app.state.pending_migrations = (
        settings.environment != "test" and await _check_has_pending_migrations()
    )
    # A full text search engine for cards names is initialized if AI is setup
    async def initialize_matcher():
      app.state.card_name_text_search_matcher = await card_name_text_search_matcher()
    # Keep a reference: the event loop only holds tasks weakly, so an unreferenced one can be
    # garbage-collected before it finishes.
    app.state.card_name_matcher_task = asyncio.create_task(initialize_matcher())
    # Mirror cached FX rates into memory so converted display currencies (#232) work from the first
    # request (even in read-only demos). Non-critical: a failure just leaves conversion unavailable.
    try:
        from src import fx
        await fx.load_rates()
    except Exception as exc:  # noqa: BLE001 - display-currency conversion is non-critical
        log.warning("fx.load_failed", error=str(exc))
    yield
    shutdown_scheduler()
    log.info("scryme.shutdown")


def _install_lan_guard(app: FastAPI) -> None:
    """Block non-loopback clients unless LAN sharing is on (and any access code matches)."""
    from fastapi.responses import PlainTextResponse

    @app.middleware("http")
    async def lan_guard(request, call_next):
        decision = access_decision(
            host=request.client.host if request.client else None,
            path=request.url.path,
            state=lan_state(),
            cookie_code=request.cookies.get("scryme_lan"),
            query_code=request.query_params.get("code"),
        )
        if decision == "deny":
            return PlainTextResponse("scryme LAN sharing is turned off.", status_code=403)
        if decision == "unlock":
            return templates.TemplateResponse(request, "_lan_unlock.html", {}, status_code=401)
        response = await call_next(request)
        if decision == "set_cookie":
            response.set_cookie("scryme_lan", request.query_params["code"], max_age=2592000,
                                samesite="lax")
        return response


def _install_cache_headers(app: FastAPI) -> None:
    """Let browsers (and any CDN in front, e.g. Cloudflare) cache static assets.

    Card images are content-addressed (the scryfall id + size is in the filename), so they're
    safe to cache forever — this is what makes card grids feel instant on repeat views and lets a
    CDN serve them without touching the origin. Bundled static assets (CSS/JS) can change between
    releases and aren't fingerprinted, so they get a modest TTL and still revalidate via ETag.
    """

    @app.middleware("http")
    async def cache_headers(request, call_next):
        response = await call_next(request)
        path = request.url.path
        if path.startswith("/images/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        elif path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "public, max-age=86400")
        return response


def _install_prefs_loader(app: FastAPI) -> None:
    """Resolve the collection preferences singleton once per HTML request onto
    ``request.state.prefs`` (#203), so the sync currency/price-source/search read helpers and the
    template context processor can consult it without threading a session everywhere.

    Skips static/image/health/metrics and the token-gated ``/api`` surface. Never 500s a request
    over prefs: a DB hiccup (or the table not yet migrated) falls back to ``None``, and the read
    helpers use their existing cookie/default path.

    Also updates specific cookie preferences from cookies to the database when they differ from
    stored values, but only for non-read-only deployments.
    """
    from sqlalchemy.exc import SQLAlchemyError

    from src.db import SessionLocal
    from src.models import Preferences
    from src.preferences import resolve, sync_cookie_prefs_to_db, update_preferences

    _skip = ("/static/", "/images/", "/api/")

    @app.middleware("http")
    async def load_prefs(request, call_next):
        path = request.url.path
        if not (path.startswith(_skip) or path in ("/health", "/metrics")):
            try:
                async with SessionLocal() as s:
                    row = await s.get(Preferences, 1)
                    # Save cookie values to database if they differ from stored values and we're not
                    # read-only
                    if row is not None and not get_settings().read_only:
                        # Check for changes in cookie preferences and update DB if needed
                        update_fields = sync_cookie_prefs_to_db(s, row, request.cookies)

                        # If there are changes, save them to the database
                        if update_fields:
                            await update_preferences(s, **update_fields)
                            # Reload the row to get updated values
                            row = await s.get(Preferences, 1)

                    request.state.prefs = resolve(
                        row, request.cookies, writable=not get_settings().read_only
                    )
                    request.state.prefs_saved = row is not None  # a value has actually been saved
            except SQLAlchemyError:
                request.state.prefs = None
        return await call_next(request)


async def _check_has_pending_migrations(alembic_ini_path: str = "alembic.ini") -> bool:
    from alembic.config import Config
    from alembic.runtime.migration import MigrationContext
    from alembic.script import ScriptDirectory

    alembic_cfg = Config(alembic_ini_path)
    script = ScriptDirectory.from_config(alembic_cfg)
    head_revs = set(script.get_heads())

    from src.db import SessionLocal
    def _check_revs(sync_conn) -> set[str]:
        context = MigrationContext.configure(sync_conn)
        return set(context.get_current_heads())

    async with SessionLocal() as session:
        conn = await session.connection()
        current_revs = await conn.run_sync(_check_revs)

    if(current_revs != head_revs):
        log.warn("scryme.migrations_pending")
        return True
    else:
        log.info("scryme.no_pending_migrations")
        return False


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="scryme", version=__version__, lifespan=lifespan)

    # Compress text responses (HTML, the value-chart SVG, JSON API). Skips small bodies.
    app.add_middleware(GZipMiddleware, minimum_size=512)

    # Add rate limiting middleware - this should be applied before other middlewares that might be
    # affected
    # Initialize rate limiter
    app.state.limiter = Limiter(key_func=get_remote_address, default_limits=["100/minute"])
    app.add_exception_handler(
        RateLimitExceeded, cast(ExceptionHandler, _rate_limit_exceeded_handler)
    )
    app.add_middleware(SlowAPIMiddleware)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    _install_cache_headers(app)
    _install_prefs_loader(app)
    if settings.lan_guard:
        _install_lan_guard(app)

    app.include_router(health.router)
    app.include_router(home.router)
    app.include_router(admin.router)
    app.include_router(search.router)
    app.include_router(upload.router)
    app.include_router(card.router)
    app.include_router(export.router)
    app.include_router(saved.router)
    app.include_router(stats.router)
    app.include_router(decks.router)
    app.include_router(binders.router)
    app.include_router(prices.router)
    app.include_router(sets.router)
    app.include_router(wishlist.router)
    app.include_router(collection.router)
    app.include_router(backup.router)
    app.include_router(trade.router)
    app.include_router(share.router)
    app.include_router(sell.router)
    app.include_router(checklists.router)
    app.include_router(mycollection.router)
    app.include_router(api.router)
    app.include_router(preferences.router)
    app.include_router(settings_routes.router)
    app.include_router(lan.router)
    app.include_router(pair.router)
    app.include_router(watch.router)
    app.include_router(ai.router)
    app.include_router(sleeves.router)
    app.include_router(reusables.router)
    app.include_router(boxes.router)

    # Cached card images are served from the data volume.
    settings.image_cache_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=settings.image_cache_dir), name="images")

    return app


app = create_app()
