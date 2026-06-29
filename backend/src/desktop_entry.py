"""Single-binary entrypoint for the desktop app's backend sidecar.

The Electron main process boots an embedded PostgreSQL, then launches this (a PyInstaller-frozen
binary in production, or ``python -m src.desktop_entry`` in dev). It applies migrations and serves
the FastAPI app on ``127.0.0.1:$SCRYME_PORT`` — the same app the web/Docker build runs, just wired
to the bundled database via the env vars Electron sets (``SCRYME_DATABASE_URL``,
``SCRYME_DATA_DIR``, ``SCRYME_IMAGE_CACHE_DIR``).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import uvicorn
from alembic import command
from alembic.config import Config


def _base_dir() -> Path:
    """Dir holding ``alembic/`` — the PyInstaller bundle root when frozen, else backend/."""
    if getattr(sys, "frozen", False):  # PyInstaller sets sys.frozen + sys._MEIPASS
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def _migrate() -> None:
    from src.config import get_settings

    base = _base_dir()
    cfg = Config()
    cfg.set_main_option("script_location", str(base / "alembic"))
    # env.py reads the URL from settings, but set it here too so a bare Config works when frozen.
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    command.upgrade(cfg, "head")


def main() -> None:
    port = int(os.environ.get("SCRYME_PORT", "8765"))
    _migrate()
    # Import after migrate so the app/engine bind to the (now-ready) database.
    from src.main import create_app

    uvicorn.run(create_app(), host="127.0.0.1", port=port, log_level="info")


if __name__ == "__main__":
    main()
