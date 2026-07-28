"""Browser-facing preferences endpoints (#203).

Separate from the versioned ``/api/v1`` router because that one is token-gated at the router level
(``Depends(require_api_token)``) — a browser ``PATCH`` there would 401 whenever ``SCRYME_API_TOKEN``
is set. The gear panel and the /settings page write here; ``/api/v1/preferences`` (in ``api.py``)
exposes the same data for external/programmatic clients and delegates to the same service.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.preferences import get_preferences, update_preferences

router = APIRouter(tags=["preferences"])


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


class PreferencesOut(BaseModel):
    currency: str
    price_source: str
    search_filter: str
    movers: bool
    view: str
    page_size: int
    infinite: bool
    hist_currency: str | None
    mode: str
    palette: str
    accent: str
    foil_speed: int
    spin: bool
    spin_speed: int
    extra: dict


class PreferencesUpdateIn(BaseModel):
    """All optional — a PATCH applies only the fields that are present (exclude_unset)."""
    currency: str | None = None
    price_source: str | None = None
    search_filter: str | None = None
    movers: bool | None = None
    view: str | None = None
    page_size: int | None = None
    infinite: bool | None = None
    hist_currency: str | None = None
    mode: str | None = None
    palette: str | None = None
    accent: str | None = None
    foil_speed: int | None = None
    spin: bool | None = None
    spin_speed: int | None = None
    extra: dict | None = None


@router.get("/prefs", response_model=PreferencesOut)
async def read_prefs(session: AsyncSession = Depends(get_session)) -> PreferencesOut:
    """The stored collection preferences (env/built-in defaults when nothing is saved)."""
    return PreferencesOut(**(await get_preferences(session)).to_dict())


@router.patch("/prefs", response_model=PreferencesOut)
async def patch_prefs(
    body: PreferencesUpdateIn, session: AsyncSession = Depends(get_session)
) -> PreferencesOut:
    """Update only the provided preferences; rejected on a read-only instance."""
    _guard_writable()
    prefs = await update_preferences(session, **body.model_dump(exclude_unset=True))
    return PreferencesOut(**prefs.to_dict())
