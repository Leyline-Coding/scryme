"""Operator endpoints for triggering and monitoring Scryfall ingestion.

Single-user deployment: the "admin" is simply the person running the instance, so there is no
auth. Mutating endpoints are disabled when the instance is in read-only (demo) mode.
"""

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.admin_stats import collect_admin_stats, image_cache_disk, render_metrics
from src.config import get_settings
from src.db import SessionLocal, get_session
from src.models import IngestState
from src.scryfall.ingest import (
    BULK_TYPE,
    current_card_count,
    get_ingest_progress,
    ingest_default_cards,
)
from src.templating import templates

router = APIRouter(tags=["admin"])


@router.get("/admin", response_class=HTMLResponse)
async def dashboard(
    request: Request, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    stats = await collect_admin_stats(session)
    img_count, img_bytes = image_cache_disk(get_settings().image_cache_dir)
    return templates.TemplateResponse(
        request, "admin.html",
        {"stats": stats, "image_files": img_count, "image_bytes": img_bytes},
    )


@router.get("/metrics", response_class=PlainTextResponse)
async def metrics(session: AsyncSession = Depends(get_session)) -> PlainTextResponse:
    stats = await collect_admin_stats(session)
    return PlainTextResponse(render_metrics(stats), media_type="text/plain; version=0.0.4")


@router.get("/admin/status")
async def status() -> dict:
    async with SessionLocal() as s:
        state = await s.get(IngestState, BULK_TYPE)
    return {
        "card_count": await current_card_count(),
        "ingest": {
            "status": state.status if state else "never",
            "source_updated_at": state.source_updated_at.isoformat()
            if state and state.source_updated_at
            else None,
            "last_downloaded_at": state.last_downloaded_at.isoformat()
            if state and state.last_downloaded_at
            else None,
            "card_count": state.card_count if state else 0,
        },
    }


@router.get("/admin/ingest/progress")
async def ingest_progress() -> dict:
    """Live progress for the first-run / refresh ingest, polled by the setup screen."""
    return {**get_ingest_progress(), "card_count": await current_card_count()}


@router.post("/admin/ingest", status_code=202)
async def trigger_ingest(background: BackgroundTasks, force: bool = False) -> dict:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="Instance is read-only")
    # Don't stack a second ingest if one is already in flight (e.g. the setup screen reloaded).
    if get_ingest_progress().get("phase") in ("downloading", "ingesting"):
        return {"accepted": False, "already_running": True}
    background.add_task(_run_ingest, force)
    return {"accepted": True, "force": force}


async def _run_ingest(force: bool) -> None:
    await ingest_default_cards(force=force)
