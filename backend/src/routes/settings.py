"""Unified settings page (#202): one place for Collection preferences and Instance/Operator config.

Collection tab reuses the shared preference controls (``_settings_prefs.html``, same as the gear
panel). Instance tab shows the read-only operator config (env-bound, process-lifetime — change via
``SCRYME_*`` and restart) plus entry points to the operator surfaces (/ai, /backup, /lan, /admin).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.client_tokens import issue_token, list_tokens, revoke_token
from src.config import get_settings
from src.db import get_session
from src.routes._safe import local_redirect
from src.templating import templates

router = APIRouter(tags=["settings"])

_TABS = ("collection", "instance", "devices")


def _onoff(value: bool) -> str:
    return "on" if value else "off"


def _instance_groups(s) -> list[dict]:
    """Grouped, read-only operator config for display. Values are process-lifetime."""
    return [
        {"title": "General", "rows": [
            ("Environment", s.environment),
            ("Read-only mode", _onoff(s.read_only)), ("Debug", _onoff(s.debug))]},
        {"title": "Storage", "rows": [
            ("Data directory", str(s.data_dir)), ("Image cache", str(s.image_cache_dir))]},
        {"title": "Database", "rows": [
            ("Host", s.db_host), ("Port", s.db_port), ("Name", s.db_name), ("User", s.db_user)]},
        {"title": "Scryfall", "rows": [
            ("API base", s.scryfall_api_base),
            ("Min request interval", f"{s.scryfall_min_request_interval}s"),
            ("Bulk refresh", f"every {s.bulk_refresh_min_hours}h"),
            ("FX refresh", f"every {s.fx_refresh_min_hours}h")]},
        {"title": "Defaults", "rows": [
            ("Currency", s.default_currency), ("Price source", s.default_price_source)]},
        {"title": "Backups", "rows": [
            ("Directory", str(s.backup_dir) if s.backup_dir else "— (disabled)"),
            ("Schedule", f"every {s.backup_interval_hours}h" if s.backup_interval_hours else "off"),
            ("Keep", s.backup_keep)]},
        {"title": "Access", "rows": [
            ("API token", "set" if s.api_token else "open (no token)"),
            ("LAN sharing", "enabled" if s.lan_guard else "off")]},
        {"title": "AI (env fallback)", "rows": [
            ("Endpoint", s.llm_base_url or "— (set in AI settings)"),
            ("Chat model", s.llm_chat_model), ("Embed model", s.llm_embed_model)]},
    ]


async def _render_settings(
    request: Request, session: AsyncSession, *, issued: str = "", tab: str = "collection"
) -> HTMLResponse:
    s = get_settings()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "read_only": s.read_only,
            "instance_groups": _instance_groups(s),
            # Operator surfaces linked from the Instance tab (lan gated by the desktop LAN feature).
            "lan_on": s.lan_guard,
            "tokens": await list_tokens(session),
            "issued_token": issued,
            "env_token_set": bool(s.api_token),
            "initial_tab": tab if tab in _TABS else "collection",
        },
    )


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request, tab: str = "collection", session: AsyncSession = Depends(get_session)
) -> HTMLResponse:
    return await _render_settings(request, session, tab=tab)


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.post("/settings/devices", response_class=HTMLResponse)
async def new_device_token(
    request: Request,
    label: str = Form(""),
    scope: str = Form("write"),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Issue a device token and hand it back exactly once.

    This renders the page rather than redirecting with the secret in the query string: a URL would
    put a live credential into browser history, the Referer header and any access log that records
    query strings. The one place the token exists in readable form is this response body.
    """
    _guard_writable()
    _, secret = await issue_token(session, label, scope)
    return await _render_settings(request, session, issued=secret, tab="devices")


@router.post("/settings/devices/{token_id}/revoke")
async def revoke_device_token(
    token_id: int, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    _guard_writable()
    await revoke_token(session, token_id)
    return local_redirect("/settings?tab=devices")
