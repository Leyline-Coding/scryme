"""Alembic configuration that does not depend on the working directory.

The server is started from ``backend/`` in Docker and in development, but the desktop build runs a
PyInstaller-frozen binary from wherever Electron launches it, with the migration scripts unpacked
into the bundle. Building the config from this file's location (or the bundle root) instead of a
relative ``alembic.ini`` keeps migrations and the pending-migrations check working in both.
"""

from __future__ import annotations

import sys
from pathlib import Path

from alembic.config import Config


def base_dir() -> Path:
    """Directory holding ``alembic/``: the PyInstaller bundle root when frozen, else backend/."""
    if getattr(sys, "frozen", False):  # PyInstaller sets sys.frozen + sys._MEIPASS
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent.parent


def alembic_config() -> Config:
    """An Alembic config pointing at the bundled migration scripts and the configured database."""
    from src.config import get_settings

    cfg = Config()
    cfg.set_main_option("script_location", str(base_dir() / "alembic"))
    # env.py reads the URL from settings, but set it here too so a bare Config works when frozen.
    cfg.set_main_option("sqlalchemy.url", get_settings().database_url.replace("%", "%%"))
    return cfg
