"""Operator endpoints for triggering and monitoring Scryfall ingestion.

Single-user deployment: the "admin" is simply the person running the instance, so there is no
auth. Mutating endpoints are disabled when the instance is in read-only (demo) mode.
"""

from fastapi import APIRouter, BackgroundTasks, HTTPException

from src.config import get_settings
from src.db import SessionLocal
from src.models import IngestState
from src.scryfall.ingest import BULK_TYPE, current_card_count, ingest_default_cards

router = APIRouter(prefix="/admin", tags=["admin"])


@router.get("/status")
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


@router.post("/ingest", status_code=202)
async def trigger_ingest(background: BackgroundTasks, force: bool = False) -> dict:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="Instance is read-only")
    background.add_task(_run_ingest, force)
    return {"accepted": True, "force": force}


async def _run_ingest(force: bool) -> None:
    await ingest_default_cards(force=force)
