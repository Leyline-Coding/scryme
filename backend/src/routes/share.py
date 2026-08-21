"""Public read-only share views (#80).

The only routes in scryme that serve someone who is not the owner. Everything here is written on
the assumption that the caller is a stranger holding a URL:

* **Nothing is inferred from the request** — no cookies, no preferences, no price source. A share
  view renders from the link row alone, so two people opening the same link see the same page and a
  visitor's own browser state can't widen what they're shown.
* **Purpose-built templates**, not the owner-facing pages with the controls hidden. A template that
  never renders an edit control cannot leak one through a missed condition, and a page with no links
  into the rest of the collection cannot be walked outward from.
* **Views are not gated by ``SCRYME_READ_ONLY``** — they are reads. Creating and revoking links are
  writes and are gated.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.binder_service import binder_cards
from src.config import get_settings
from src.currency import info
from src.db import get_session
from src.decks import deck_coverage, deck_stats
from src.models import Binder, Deck
from src.routes._safe import local_redirect
from src.share import (
    BINDER,
    DECK,
    create_share_link,
    resolve_share_link,
    revoke_share_link,
)
from src.templating import templates

router = APIRouter(tags=["share"])

_GONE = "This share link isn't available."
# Shared views are always valued in USD: the link has no viewer to read a currency preference from,
# and the owner's own preference isn't the visitor's.
_SHARE_CURRENCY = "usd"


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.get("/share/{token}", response_class=HTMLResponse)
async def view_share(
    request: Request, token: str, session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    """Render a shared deck or binder. 404 for unknown, revoked, or deleted targets alike.

    Deliberately one status for all three: distinguishing "revoked" from "never existed" would
    confirm to a stranger that a token was once real, which is a small oracle worth not offering.
    """
    link = await resolve_share_link(session, token)
    if link is None:
        raise HTTPException(status_code=404, detail=_GONE)

    ctx: dict = {"link": link, "cur": info(_SHARE_CURRENCY), "show_prices": link.show_prices}
    if link.kind == DECK:
        deck = await session.get(Deck, link.target_id)
        if deck is None:
            raise HTTPException(status_code=404, detail=_GONE)
        ctx["cov"] = await deck_coverage(session, deck, currency=_SHARE_CURRENCY)
        ctx["deck"] = deck
        # Only priced when the owner opted in — no point computing a total we won't show.
        ctx["stats"] = await deck_stats(session, deck) if link.show_prices else None
        return templates.TemplateResponse(request, "share_deck.html", ctx)

    binder = await session.get(Binder, link.target_id)
    if binder is None:
        raise HTTPException(status_code=404, detail=_GONE)
    ctx["binder"] = binder
    ctx["cards"] = await binder_cards(session, binder.id)
    return templates.TemplateResponse(request, "share_binder.html", ctx)


@router.post("/share/{kind}/{target_id}")
async def new_share(
    kind: str,
    target_id: int,
    show_prices: str = Form(""),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    """Mint a link and send the owner back to the page they shared from."""
    _guard_writable()
    created = await create_share_link(session, kind, target_id,
                                      show_prices=bool(show_prices.strip()))
    if created is None:
        raise HTTPException(status_code=404, detail="Nothing to share.")
    _, token = created
    # The token rides back in the query string so the page can show the full URL once. Unlike a
    # device token this is not a secret the owner must keep — it is the thing they are about to
    # paste somewhere public, and they can re-open the link to read it again.
    return local_redirect(f"{_owner_url(kind, target_id)}?shared={token}")


@router.post("/share/link/{link_id}/revoke")
async def revoke_share(
    link_id: int,
    kind: str = Form(...),
    target_id: int = Form(...),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    _guard_writable()
    await revoke_share_link(session, link_id)
    return local_redirect(_owner_url(kind, target_id))


def _owner_url(kind: str, target_id: int) -> str:
    """Where the owner manages this target's links."""
    if kind == BINDER:
        return f"/binders/view/{target_id}"
    return f"/decks/{target_id}"
