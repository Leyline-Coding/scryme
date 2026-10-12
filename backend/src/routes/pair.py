"""Device pairing (#165): hand a scanner a base URL and its own token, as one QR code.

The alternative is typing a 43-character secret into a phone, so the QR is the feature. What it
encodes is deliberately small — ``{"base_url": ..., "token": ...}`` — because that is the whole
contract the scanner needs and anything more would be a second thing to keep in sync across two
repos.

Two decisions worth stating:

**The token is a per-device token (#204), never ``SCRYME_API_TOKEN``.** A paired phone is the most
losable credential an instance issues, so it has to be revocable on its own, and revoking it must
not log out every other client. Pairing therefore mints a fresh ``client_token`` each time rather
than showing an existing one — the plaintext is unrecoverable after issue (by design), so "show me
the QR again" is necessarily "issue a new one and revoke the old".

**The base URL is chosen for the phone, not for the browser showing the page.** An operator pairs
from the machine running scryme, so ``request.base_url`` is very often ``localhost`` — a URL that
is correct for this browser and useless to every other device on the network.

Substituting the machine's own LAN address is only *sometimes* the right repair, and getting that
wrong produces a QR code that scans perfectly and then cannot connect. :func:`local_ip` asks the
routing table what address this process would send from, which answers the operator's question only
when the process shares the host's network — the desktop app, or a bare-metal run. Under Docker it
answers truthfully about the container (``172.x``) and uselessly about the host, and the container
has no way to learn the host's LAN address. So the substitution is gated on ``lan_guard``, the flag
the desktop shell sets, and every other deployment is told plainly to reopen scryme at the address
it wants the device to use rather than being handed a confident wrong answer.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from src.client_tokens import issue_token
from src.config import get_settings
from src.db import get_session
from src.lan import is_loopback, lan_state, local_ip, qr_svg
from src.templating import templates

router = APIRouter(tags=["pair"])

DEFAULT_LABEL = "Scanner"
# Pairing is for a device that adds cards, so it needs write. A read-only device does not need to
# be paired — it can be given a read token from Settings → Devices.
PAIR_SCOPE = "write"


# What had to be done to the browser's URL to make it usable by another device.
REACHABLE = "reachable"      # the address in the browser bar is already fine
REWRITTEN = "rewritten"      # loopback, and we know this host's LAN address
UNREACHABLE = "unreachable"  # loopback, and we can't know what would work instead


def pairing_base_url(request: Request, *, can_resolve_host_ip: bool = False) -> tuple[str, str]:
    """The URL to put in the QR, and what had to happen to get it. Returns ``(url, state)``.

    ``can_resolve_host_ip`` says whether :func:`local_ip` speaks for the machine the operator means
    — true on the desktop app, false inside a container, where it would confidently return an
    address only the container can reach.
    """
    base = str(request.base_url).rstrip("/")
    if not is_loopback(request.url.hostname):
        return base, REACHABLE
    if not can_resolve_host_ip:
        return base, UNREACHABLE
    # Only the host is substituted. Hardcoding the scheme would rewrite an https origin to
    # "http://<ip>:443" — a URL that contradicts itself and connects to nothing.
    scheme = request.url.scheme
    port = request.url.port or (443 if scheme == "https" else 80)
    return f"{scheme}://{local_ip()}:{port}", REWRITTEN


def pairing_payload(base_url: str, token: str) -> str:
    """The QR's contents. Compact JSON so the code stays low-density enough to scan quickly."""
    return json.dumps({"base_url": base_url, "token": token}, separators=(",", ":"))


def _render(
    request: Request,
    *,
    token: str = "",
    label: str = "",
) -> HTMLResponse:
    settings = get_settings()
    # The desktop shell binds to the host's network, so its own address is the one a phone wants.
    base_url, url_state = pairing_base_url(request, can_resolve_host_ip=bool(settings.lan_guard))
    return templates.TemplateResponse(
        request,
        "pair.html",
        {
            "read_only": settings.read_only,
            "issued_token": token,
            "device_label": label,
            "base_url": base_url,
            "url_state": url_state,
            "qr": qr_svg(pairing_payload(base_url, token)) if token else "",
            # A desktop instance only answers other devices while LAN sharing is on, so a QR minted
            # with it off would scan cleanly and then fail to connect.
            "lan_required": settings.lan_guard,
            "lan_enabled": lan_state(settings)["enabled"],
        },
    )


def _guard_writable() -> None:
    if get_settings().read_only:
        raise HTTPException(status_code=403, detail="This instance is read-only.")


@router.get("/pair", response_class=HTMLResponse)
async def pair_page(request: Request) -> HTMLResponse:
    return _render(request)


@router.post("/pair", response_class=HTMLResponse)
async def pair_device(
    request: Request,
    label: str = Form(DEFAULT_LABEL),
    session: AsyncSession = Depends(get_session),
) -> HTMLResponse:
    """Mint a device token and render its QR.

    Renders rather than redirects, for the same reason ``/settings/devices`` does: a redirect would
    put a live credential in the URL, and from there into browser history, the ``Referer`` header
    and any access log that keeps query strings.
    """
    _guard_writable()
    clean = (label or "").strip() or DEFAULT_LABEL
    _, secret = await issue_token(session, clean, PAIR_SCOPE)
    return _render(request, token=secret, label=clean)
