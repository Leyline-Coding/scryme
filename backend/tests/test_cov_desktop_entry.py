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
    monkeypatch.setattr(de, "_bind_host", lambda: "127.0.0.1")
    monkeypatch.delenv("SCRYME_PORT", raising=False)
    # An inherited "off" must not survive: the desktop entry forces the guard on.
    monkeypatch.setenv("SCRYME_LAN_GUARD", "0")

    de.main()

    assert calls["migrated"] is True
    assert calls["app"] == "THE_APP"
    assert calls["port"] == 8765            # default port
    assert calls["host"] == "127.0.0.1"
    assert de.os.environ["SCRYME_LAN_GUARD"] == "1"


def test_force_lan_guard_overrides_every_spelling(monkeypatch):
    # The settings loader matches env names case-insensitively, so a lower-case variant set to
    # "0" would otherwise compete with the forced value.
    monkeypatch.setenv("SCRYME_LAN_GUARD", "0")
    monkeypatch.setenv("scryme_lan_guard", "false")

    de._force_lan_guard()

    guards = {k: v for k, v in de.os.environ.items() if k.upper() == "SCRYME_LAN_GUARD"}
    assert guards == {"SCRYME_LAN_GUARD": "1"}


def test_force_lan_guard_sets_it_when_absent(monkeypatch):
    monkeypatch.delenv("SCRYME_LAN_GUARD", raising=False)
    de._force_lan_guard()
    assert de.os.environ["SCRYME_LAN_GUARD"] == "1"
    monkeypatch.setenv("SCRYME_LAN_GUARD", "1")   # so teardown removes what the call added
    monkeypatch.delenv("SCRYME_LAN_GUARD")


def test_bind_host_is_loopback_unless_lan_sharing_is_on(monkeypatch):
    import src.lan

    monkeypatch.setattr(src.lan, "lan_state", lambda: {"enabled": False, "code": ""})
    assert de._bind_host() == "127.0.0.1"
    monkeypatch.setattr(src.lan, "lan_state", lambda: {"enabled": True, "code": "abc123"})
    assert de._bind_host() == "0.0.0.0"     # noqa: S104 - asserting the bind arg


def test_bind_host_reads_the_real_lan_file(monkeypatch, tmp_path):
    import src.lan
    from src.config import Settings

    settings = Settings(data_dir=tmp_path)
    monkeypatch.setattr(src.lan, "get_settings", lambda: settings)
    assert de._bind_host() == "127.0.0.1"   # no lan.json yet → off
    src.lan.save_state(True, "", settings)
    assert de._bind_host() == "0.0.0.0"     # noqa: S104 - asserting the bind arg
    (tmp_path / src.lan.LAN_FILE).write_text("{not json")
    assert de._bind_host() == "127.0.0.1"   # unreadable state counts as off
