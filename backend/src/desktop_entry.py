"""Single-binary entrypoint for the desktop app's backend sidecar.

The Electron main process boots an embedded PostgreSQL, then launches this (a PyInstaller-frozen
binary in production, or ``python -m src.desktop_entry`` in dev). It applies migrations and serves
the FastAPI app on ``127.0.0.1:$SCRYME_PORT`` — the same app the web/Docker build runs, just wired
to the bundled database via the env vars Electron sets (``SCRYME_DATABASE_URL``,
``SCRYME_DATA_DIR``, ``SCRYME_IMAGE_CACHE_DIR``).
"""

from __future__ import annotations

import os

import uvicorn
from alembic import command


def _migrate() -> None:
    from src.migrations import alembic_config

    command.upgrade(alembic_config(), "head")


def main() -> None:
    port = int(os.environ.get("SCRYME_PORT", "8765"))
    # Bind on all interfaces so opt-in LAN sharing can work, but turn on the LAN guard: non-loopback
    # clients are blocked until the user enables sharing (see src/lan.py). The window uses loopback.
    os.environ.setdefault("SCRYME_LAN_GUARD", "1")
    _migrate()
    # Import after migrate so the app/engine bind to the (now-ready) database.
    from src.main import create_app

    uvicorn.run(create_app(), host="0.0.0.0", port=port, log_level="info")  # noqa: S104


if __name__ == "__main__":  # pragma: no cover
    main()
