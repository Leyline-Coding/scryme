"""Coverage for src/desktop_entry.py — path resolution, migrate wiring, and main().

The uvicorn.run server launch itself is not unit-testable (it blocks serving), so it is mocked;
the ``if __name__ == '__main__'`` guard (line 52) is unreachable on import."""

import src.desktop_entry as de


def test_base_dir_not_frozen():
    # Not frozen: base dir is the backend/ directory (parent of src/).
    assert de._base_dir().name == "backend"


def test_base_dir_frozen(monkeypatch):
    monkeypatch.setattr(de.sys, "frozen", True, raising=False)
    monkeypatch.setattr(de.sys, "_MEIPASS", "/tmp/meipass-bundle", raising=False)
    assert str(de._base_dir()) == "/tmp/meipass-bundle"


def test_migrate_wires_alembic_config(monkeypatch):
    recorded = {}

    class FakeConfig:
        def set_main_option(self, key, value):
            recorded[key] = value

    monkeypatch.setattr(de, "Config", FakeConfig)
    monkeypatch.setattr(de.command, "upgrade", lambda cfg, rev: recorded.__setitem__("rev", rev))

    de._migrate()
    assert recorded["rev"] == "head"
    assert recorded["script_location"].endswith("alembic")
    assert "sqlalchemy.url" in recorded


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
