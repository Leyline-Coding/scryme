"""Coverage for src/lan.py local_ip (success + OSError fallback)."""

from src import lan


def test_local_ip_returns_string():
    ip = lan.local_ip()
    assert isinstance(ip, str) and ip


def test_local_ip_falls_back_on_oserror(monkeypatch):
    class FakeSock:
        def __init__(self, *a, **k):
            pass

        def connect(self, addr):
            raise OSError("no route")

        def getsockname(self):  # pragma: no cover - not reached on the error path
            return ("1.2.3.4", 0)

        def close(self):
            pass

    monkeypatch.setattr(lan.socket, "socket", lambda *a, **k: FakeSock())
    assert lan.local_ip() == "127.0.0.1"
