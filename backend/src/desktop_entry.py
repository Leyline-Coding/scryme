"""Single-binary entrypoint for the desktop app's backend sidecar.

The Electron main process boots an embedded PostgreSQL, then launches this (a PyInstaller-frozen
binary in production, or ``python -m src.desktop_entry`` in dev). It applies migrations and serves
the FastAPI app on ``$SCRYME_PORT`` — the same app the web/Docker build runs, just wired to the
bundled database via the env vars Electron sets (``SCRYME_DATABASE_URL``, ``SCRYME_DATA_DIR``,
``SCRYME_IMAGE_CACHE_DIR``).

Bind address: ``127.0.0.1`` unless LAN sharing (``src/lan.py``) is switched on, in which case
``0.0.0.0``. A listening socket cannot be rebound, so the choice is made once at startup; the
Electron shell watches ``lan.json`` and restarts this process when the user toggles sharing.
"""

from __future__ import annotations

import os

import uvicorn
from alembic import command

LOOPBACK_HOST = "127.0.0.1"
LAN_HOST = "0.0.0.0"  # noqa: S104 - only used while the user has LAN sharing switched on


def _migrate() -> None:
    from src.migrations import alembic_config

    command.upgrade(alembic_config(), "head")


def _force_lan_guard() -> None:
    """Turn the LAN guard on regardless of the inherited environment.

    The desktop backend may listen on every interface, so the guard is not optional here: an
    inherited ``SCRYME_LAN_GUARD=0`` (in any letter case — the settings loader is case-insensitive)
    must not be able to switch it off.
    """
    for key in [k for k in os.environ if k.upper() == "SCRYME_LAN_GUARD"]:
        del os.environ[key]
    os.environ["SCRYME_LAN_GUARD"] = "1"


def _bind_host() -> str:
    """Loopback unless the user has LAN sharing on (unreadable state counts as off)."""
    from src.lan import lan_state

    return LAN_HOST if lan_state()["enabled"] else LOOPBACK_HOST


def main() -> None:
    port = int(os.environ.get("SCRYME_PORT", "8765"))
    _force_lan_guard()
    _migrate()
    # Import after migrate so the app/engine bind to the (now-ready) database.
    from src.main import create_app

    uvicorn.run(create_app(), host=_bind_host(), port=port, log_level="info")


if __name__ == "__main__":  # pragma: no cover
    main()
