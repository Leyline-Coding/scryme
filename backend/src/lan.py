"""LAN sharing for the desktop app: expose the local instance to other devices on the home network.

Only active when ``lan_guard`` is set (the desktop app passes ``SCRYME_LAN_GUARD=1`` and binds the
backend to ``0.0.0.0``). The Docker/self-host deployment leaves it off, so its behaviour is
unchanged. State (on/off + optional access code) lives in a small JSON file in the data dir so it
survives restarts; the request guard lives in ``main.py``.

Security model: with the guard on, loopback (the desktop window itself) is always allowed and
``/static`` assets pass through, but every other client gets a 403 until LAN sharing is enabled —
and then, if an access code is set, must present it (``?code=`` once, then a cookie). Intended for
trusted home networks; there is no real auth model.
"""

from __future__ import annotations

import io
import json
import secrets
import socket
from pathlib import Path

from src.config import Settings, get_settings

LAN_FILE = "lan.json"
_LOOPBACK = {"127.0.0.1", "::1", "localhost", "::ffff:127.0.0.1"}


def _path(settings: Settings) -> Path:
    return settings.data_dir / LAN_FILE


def lan_state(settings: Settings | None = None) -> dict:
    """Current LAN sharing state: ``{"enabled": bool, "code": str}``."""
    settings = settings or get_settings()
    try:
        data = json.loads(_path(settings).read_text())
    except (OSError, ValueError):
        data = {}
    return {"enabled": bool(data.get("enabled")), "code": str(data.get("code") or "")}


def save_state(enabled: bool, code: str, settings: Settings | None = None) -> dict:
    settings = settings or get_settings()
    path = _path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    state = {"enabled": bool(enabled), "code": code or ""}
    path.write_text(json.dumps(state))
    return state


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    return host in _LOOPBACK or host.startswith("127.")


def access_decision(
    *, host: str | None, path: str, state: dict, cookie_code: str | None, query_code: str | None
) -> str:
    """Pure access decision for the LAN guard, so it's testable without a real network peer.

    Returns one of: ``allow`` (pass through), ``deny`` (403, sharing off), ``unlock`` (401, code
    required), ``set_cookie`` (valid ``?code=`` — pass through and remember it).
    """
    if is_loopback(host) or path.startswith("/static"):
        return "allow"
    if not state.get("enabled"):
        return "deny"
    code = state.get("code") or ""
    if not code:
        return "allow"
    if query_code == code:
        return "set_cookie"
    if cookie_code == code:
        return "allow"
    return "unlock"


def local_ip() -> str:
    """Best-effort primary LAN IP. Uses a UDP socket's routing decision (sends nothing)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("8.8.8.8", 80))
        return sock.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        sock.close()


def make_code() -> str:
    """A short, shareable access code."""
    return secrets.token_hex(3)


def share_url(port: int, code: str = "") -> str:
    url = f"http://{local_ip()}:{port}/"
    if code:
        url += f"?code={code}"
    return url


def qr_svg(data: str) -> str:
    """An inline SVG QR code for ``data`` (no Pillow/network needed)."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=9, border=2)
    buf = io.BytesIO()
    img.save(buf)
    return buf.getvalue().decode()
