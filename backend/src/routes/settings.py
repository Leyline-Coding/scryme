"""Unified settings page (#202): one place for Collection preferences and Instance/Operator config.

Collection tab reuses the shared preference controls (``_settings_prefs.html``, same as the gear
panel). Instance tab shows the read-only operator config (env-bound, process-lifetime — change via
``SCRYME_*`` and restart) plus entry points to the operator surfaces (/ai, /backup, /lan, /admin).
"""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from src.config import get_settings
from src.templating import templates

router = APIRouter(tags=["settings"])


def _instance_groups(s) -> list[dict]:
    """Grouped, read-only operator config for display. Values are process-lifetime."""
    return [
        {"title": "General", "rows": [
            ("Environment", s.environment), ("Read-only mode", s.read_only), ("Debug", s.debug)]},
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


@router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request) -> HTMLResponse:
    s = get_settings()
    return templates.TemplateResponse(
        request,
        "settings.html",
        {
            "read_only": s.read_only,
            "instance_groups": _instance_groups(s),
            # Operator surfaces linked from the Instance tab (lan gated by the desktop LAN feature).
            "lan_on": s.lan_guard,
        },
    )
