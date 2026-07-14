"""FastAPI application entrypoint.

Wires templates, static files, and routers. Feature routers (search, upload) are added in
later phases; this build ships the app skeleton, health/home/admin routes, and the scheduled
Scryfall bulk refresh.
"""

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src import __version__
from src.config import get_settings
from src.lan import access_decision, lan_state
from src.routes import (
    admin,
    ai,
    api,
    backup,
    binders,
    card,
    checklists,
    collection,
    decks,
    export,
    health,
    home,
    lan,
    mycollection,
    prices,
    saved,
    search,
    sell,
    sets,
    stats,
    trade,
    upload,
    watch,
    wishlist,
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


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="scryme", version=__version__, lifespan=lifespan)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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
    app.include_router(sell.router)
    app.include_router(checklists.router)
    app.include_router(mycollection.router)
    app.include_router(api.router)
    app.include_router(lan.router)
    app.include_router(watch.router)
    app.include_router(ai.router)

    # Cached card images are served from the data volume.
    settings.image_cache_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=settings.image_cache_dir), name="images")

    return app


app = create_app()
