"""Legacy /stats path — now the Stats tab of the consolidated /collection page."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter(tags=["stats"])


@router.get("/stats")
async def stats() -> RedirectResponse:
    return RedirectResponse(url="/collection/stats", status_code=307)
