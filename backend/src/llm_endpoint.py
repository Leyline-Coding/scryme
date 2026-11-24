"""Validation of the owner-configured LLM / embeddings endpoint URL (#163 hardening).

The endpoint is whatever OpenAI-compatible server the owner points scryme at, and a local or
private address (Ollama / LM Studio on ``localhost`` or the LAN) is the common case — so private
ranges are deliberately **allowed**. What is refused:

* anything that is not plain ``http``/``https`` with a host name (``file:``, ``gopher:``, ...);
* credentials embedded in the URL (the key belongs in the encrypted API-key field);
* the link-local range (``169.254.0.0/16``, ``fe80::/10`` — where cloud metadata services live)
  and the unspecified address (``0.0.0.0``, ``::``). Nothing legitimate is served from there.

:func:`parse_endpoint` is the static check used when the setting is saved;
:func:`ensure_endpoint_allowed` additionally resolves the host name and checks *every* address it
resolves to, and runs before each outbound request. URLs are parsed with ``httpx.URL`` — the same
parser that makes the request — so the check and the connection agree on what the host is.

:func:`same_origin` backs the rule that a stored API key belongs to the origin it was saved for.
"""

from __future__ import annotations

import asyncio
import ipaddress
import socket

import httpx

_DEFAULT_PORTS = {"http": 80, "https": 443}
_BLOCKED_NETWORKS = (
    ipaddress.ip_network("169.254.0.0/16"),  # IPv4 link-local, incl. cloud metadata
    ipaddress.ip_network("fe80::/10"),       # IPv6 link-local
)

_MESSAGES = {
    "scheme": "The endpoint URL must start with http:// or https://.",
    "host": "The endpoint URL needs a valid host name.",
    "credentials": (
        "The endpoint URL must not contain a username or password — "
        "put the key in the API key field."
    ),
    "blocked": (
        "That endpoint address is not allowed (link-local / metadata addresses are blocked)."
    ),
    "unreachable": "Could not connect to the endpoint.",
}


class EndpointError(ValueError):
    """The configured endpoint URL is unusable. ``message`` is a fixed, user-safe string.

    Subclasses :class:`ValueError` so the existing ``except (httpx.HTTPError, ..., ValueError)``
    handlers around every AI feature treat it as "endpoint unreachable".
    """

    def __init__(self, code: str):
        self.code = code
        self.message = _MESSAGES[code]
        super().__init__(self.message)


def endpoint_error_message(code: str | None) -> str:
    """The fixed message for an error *code* ('' for an unknown code — never echoes input)."""
    return _MESSAGES.get(code or "", "")


def _is_blocked(address: str) -> bool:
    """True if *address* (a numeric IP, optionally with an IPv6 zone id) must never be contacted."""
    try:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
    except ValueError:
        return True  # a resolver handed back something that is not an IP — refuse it
    mapped = getattr(ip, "ipv4_mapped", None)
    if mapped is not None:  # ::ffff:169.254.169.254
        ip = mapped
    return ip.is_unspecified or any(ip in net for net in _BLOCKED_NETWORKS)


def _numeric_addresses(host: str) -> list[str]:
    """Addresses *host* denotes if it is a numeric literal, without any DNS lookup.

    Uses the platform's own numeric parser so that the odd spellings it accepts — ``0``,
    ``2852039166``, ``0xa9.0xfe.0xa9.0xfe`` — are seen as the addresses they connect to.
    """
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM, flags=socket.AI_NUMERICHOST)
    except (socket.gaierror, UnicodeError, ValueError):
        return []
    return [info[4][0] for info in infos]


def parse_endpoint(url: str) -> httpx.URL:
    """Statically validate an endpoint URL; return it parsed, or raise :class:`EndpointError`."""
    try:
        parsed = httpx.URL((url or "").strip())
    except (httpx.InvalidURL, TypeError, ValueError):
        raise EndpointError("host") from None
    if parsed.scheme not in _DEFAULT_PORTS:
        raise EndpointError("scheme")
    if parsed.userinfo:
        raise EndpointError("credentials")
    if not parsed.host:
        raise EndpointError("host")
    try:
        port = parsed.port
    except ValueError:
        raise EndpointError("host") from None
    if port is not None and not 0 < port < 65536:
        raise EndpointError("host")
    # A zone id ("fe80::1%eth0", percent-encoded in a URL) never changes which range it is in.
    if "%" in parsed.host and _is_blocked(parsed.host.split("%", 1)[0]):
        raise EndpointError("blocked")
    if any(_is_blocked(addr) for addr in _numeric_addresses(parsed.host)):
        raise EndpointError("blocked")
    return parsed


def endpoint_origin(url: str) -> tuple[str, str, int] | None:
    """Normalised ``(scheme, host, port)`` of *url*, or ``None`` if it is not a valid endpoint."""
    try:
        parsed = parse_endpoint(url)
    except EndpointError:
        return None
    host = parsed.host.lower().rstrip(".")
    return parsed.scheme, host, parsed.port or _DEFAULT_PORTS[parsed.scheme]


def same_origin(a: str, b: str) -> bool:
    """True if two endpoint URLs share scheme + host + port (a path-only difference is fine).

    URLs that cannot be parsed never share an origin with anything but an identical string.
    """
    a, b = (a or "").strip(), (b or "").strip()
    if a == b:
        return True
    origin_a = endpoint_origin(a)
    return origin_a is not None and origin_a == endpoint_origin(b)


async def _resolve(host: str, port: int) -> list[str]:
    """Every address *host* resolves to (system resolver, off the event loop)."""
    infos = await asyncio.get_running_loop().getaddrinfo(host, port, type=socket.SOCK_STREAM)
    return [info[4][0] for info in infos]


async def ensure_endpoint_allowed(url: str) -> httpx.URL:
    """Validate *url* and check every address its host resolves to. Run before each request.

    Raises :class:`EndpointError` — ``blocked`` if any resolved address is in a refused range,
    ``unreachable`` if the name does not resolve at all.
    """
    parsed = parse_endpoint(url)
    port = parsed.port or _DEFAULT_PORTS[parsed.scheme]
    try:
        addresses = await _resolve(parsed.host, port)
    except (OSError, UnicodeError):
        raise EndpointError("unreachable") from None
    if not addresses:
        raise EndpointError("unreachable")
    if any(_is_blocked(addr) for addr in addresses):
        raise EndpointError("blocked")
    return parsed
