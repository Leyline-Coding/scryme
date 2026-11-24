"""Coverage for src/desktop_entry.py — path resolution, migrate wiring, and main().

The uvicorn.run server launch itself is not unit-testable (it blocks serving), so it is mocked;
the ``if __name__ == '__main__'`` guard (line 52) is unreachable on import."""

from pathlib import Path

import src.desktop_entry as de
from src import migrations


def test_base_dir_not_frozen():
    # Not frozen: base dir is the backend/ directory (parent of src/).
    assert migrations.base_dir().name == "backend"


def test_base_dir_frozen(monkeypatch):
    monkeypatch.setattr(migrations.sys, "frozen", True, raising=False)
    monkeypatch.setattr(migrations.sys, "_MEIPASS", "/tmp/meipass-bundle", raising=False)
    assert Path(migrations.base_dir()).as_posix().endswith("/tmp/meipass-bundle")


def test_alembic_config_ignores_working_directory(monkeypatch, tmp_path):
    # The frozen desktop backend starts in a directory with no alembic.ini; the config must still
    # find the migration scripts (a relative lookup crashed startup there).
    from alembic.script import ScriptDirectory

    monkeypatch.chdir(tmp_path)
    cfg = migrations.alembic_config()
    assert Path(cfg.get_main_option("script_location")).is_dir()
    assert cfg.get_main_option("sqlalchemy.url")
    assert ScriptDirectory.from_config(cfg).get_heads()


def test_migrate_upgrades_to_head(monkeypatch):
    recorded = {}
    monkeypatch.setattr(
        de.command, "upgrade", lambda cfg, rev: recorded.update(cfg=cfg, rev=rev)
    )

    de._migrate()
    assert recorded["rev"] == "head"
    assert recorded["cfg"].get_main_option("script_location").endswith("alembic")


def test_main_migrates_then_launches_server(monkeypatch):
    import src.main

    calls = {}
    monkeypatch.setattr(de, "_migrate", lambda: calls.__setitem__("migrated", True))
    monkeypatch.setattr(src.main, "create_app", lambda: "THE_APP")
    monkeypatch.setattr(de.uvicorn, "run", lambda app, **kw: calls.update(app=app, **kw))
    monkeypatch.delenv("SCRYME_PORT", raising=False)

    de.main()

    assert calls["migrated"] is True
    assert calls["app"] == "THE_APP"
    assert calls["port"] == 8765            # default port
    assert calls["host"] == "0.0.0.0"       # noqa: S104 - asserting the bind arg
