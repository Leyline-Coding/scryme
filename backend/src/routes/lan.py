"""LAN sharing page + toggles (desktop). Reachable in-app from the settings panel.

The toggle/code endpoints are loopback-only — only the desktop window itself can change sharing,
not a phone that already has the link.
"""

from fastapi import APIRouter, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from src import lan
from src.config import get_settings
from src.templating import templates

router = APIRouter(tags=["lan"])


def _require_loopback(request: Request) -> None:
    host = request.client.host if request.client else None
    if not lan.is_loopback(host):
        raise HTTPException(status_code=403, detail="Only the desktop app can change LAN sharing.")


@router.get("/lan", response_class=HTMLResponse)
async def lan_page(request: Request) -> HTMLResponse:
    settings = get_settings()
    state = lan.lan_state(settings)
    port = request.url.port or 80
    url = lan.share_url(port, state["code"]) if state["enabled"] else lan.share_url(port)
    return templates.TemplateResponse(
        request,
        "lan.html",
        {
            "state": state,
            "url": url,
            "qr": lan.qr_svg(url) if state["enabled"] else "",
            "available": settings.lan_guard,
            "read_only": settings.read_only,
        },
    )


@router.post("/lan/toggle")
async def lan_toggle(request: Request):
    _require_loopback(request)
    state = lan.lan_state()
    lan.save_state(not state["enabled"], state["code"])
    return RedirectResponse(url="/lan", status_code=303)


@router.post("/lan/code")
async def lan_code(request: Request, action: str = Form(...)):
    _require_loopback(request)
    state = lan.lan_state()
    code = lan.make_code() if action == "set" else ""
    lan.save_state(state["enabled"], code)
    return RedirectResponse(url="/lan", status_code=303)
