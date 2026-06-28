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
from src.routes import (
    admin,
    binders,
    card,
    collection,
    decks,
    export,
    health,
    home,
    prices,
    saved,
    search,
    sets,
    stats,
    upload,
    wishlist,
)
from src.scheduler import shutdown_scheduler, start_scheduler
from src.templating import STATIC_DIR

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


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="scryme", version=__version__, lifespan=lifespan)

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

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

    # Cached card images are served from the data volume.
    settings.image_cache_dir.mkdir(parents=True, exist_ok=True)
    app.mount("/images", StaticFiles(directory=settings.image_cache_dir), name="images")

    return app


app = create_app()
