"""Price-watchlist routes (#88): add/remove targets + a combined alerts summary.

The add form lives on the card page; targets are listed on /prices. `/alerts` unifies the
saved-search (#58) and price-watch counts for the desktop/web notification poll.
"""

from fastapi import APIRouter, Depends, Form, HTTPException
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.config import get_settings
from src.db import get_session
from src.price_watch import add_target, count_triggered, remove_target
from src.saved_alerts import total_new_matches

router = APIRouter(tags=["watch"])


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.post("/watch/add")
async def watch_add(
    scryfall_id: str = Form(...),
    direction: str = Form(...),
    threshold: float = Form(...),
    session: AsyncSession = Depends(get_session),
):
    _guard_writable()
    await add_target(session, scryfall_id, direction, threshold)
    return RedirectResponse(url=f"/card/{scryfall_id}", status_code=303)


@router.post("/watch/{target_id}/delete")
async def watch_delete(target_id: int, session: AsyncSession = Depends(get_session)):
    _guard_writable()
    await remove_target(session, target_id)
    return RedirectResponse(url="/prices", status_code=303)


@router.get("/alerts")
async def alerts_summary(session: AsyncSession = Depends(get_session)):
    """Combined unviewed-alert totals for the notification poll."""
    saved = await total_new_matches(session)
    price = await count_triggered(session)
    return {"saved": saved, "price": price, "total": saved + price}
