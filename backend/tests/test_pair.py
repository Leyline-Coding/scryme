"""Device pairing (#165): the QR payload, the token it carries, and the loopback rewrite.

Two things here are worth pinning rather than trusting to review: that pairing issues a *revocable
per-device* token instead of exposing the shared ``SCRYME_API_TOKEN``, and that the URL in the code
is one the scanning phone can actually reach.
"""

import json

import pytest
from sqlalchemy import select
from src.models import ClientToken
from src.routes.pair import pairing_payload


async def _pair(client, label="Pixel scanner"):
    return await client.post("/pair", data={"label": label})


def _payload_from(html: str) -> dict:
    """The QR is an SVG, so read the credential out of the manual-entry block instead."""
    token = html.split('<dt class="text-xs text-muted">Token</dt>', 1)[1]
    token = token.split("select-all\">", 1)[1].split("</code>", 1)[0]
    return {"token": token.strip()}


# --- the page ------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_page_offers_pairing_before_any_token_exists(client):
    resp = await client.get("/pair")
    assert resp.status_code == 200
    assert "Show pairing code" in resp.text
    # Nothing is issued just by looking at the page.
    assert "Token" not in resp.text


@pytest.mark.asyncio
async def test_pairing_issues_a_revocable_per_device_token(client, session):
    resp = await _pair(client)
    assert resp.status_code == 200

    rows = list((await session.execute(select(ClientToken))).scalars().all())
    assert len(rows) == 1
    row = rows[0]
    assert row.label == "Pixel scanner" and row.scope == "write" and row.revoked_at is None

    # The secret is shown once, in the body, and only its hash was stored.
    secret = _payload_from(resp.text)["token"]
    assert secret.startswith("scryme_")
    assert secret not in row.token_hash


@pytest.mark.asyncio
async def test_the_shown_token_is_the_one_that_works_on_the_api(client, session):
    """End to end: the credential in the QR authenticates a scan against a now-closed API."""
    resp = await _pair(client)
    secret = _payload_from(resp.text)["token"]

    # Issuing the first token closes /api/v1 (#204), so an unauthenticated call is refused...
    assert (await client.get("/api/v1/stats")).status_code == 401
    # ...and the paired device's token is accepted.
    ok = await client.get("/api/v1/stats", headers={"Authorization": f"Bearer {secret}"})
    assert ok.status_code == 200


@pytest.mark.asyncio
async def test_pairing_again_issues_a_second_token_rather_than_reshowing_the_first(client, session):
    """The plaintext is unrecoverable by design, so "show it again" means "issue a new one"."""
    first = _payload_from((await _pair(client, "Phone A")).text)["token"]
    second = _payload_from((await _pair(client, "Phone B")).text)["token"]
    assert first != second

    labels = [r.label for r in (await session.execute(select(ClientToken))).scalars().all()]
    assert sorted(labels) == ["Phone A", "Phone B"]


@pytest.mark.asyncio
async def test_a_blank_device_name_falls_back_to_a_default(client, session):
    await _pair(client, "   ")
    row = (await session.execute(select(ClientToken))).scalars().first()
    assert row.label == "Scanner"


@pytest.mark.asyncio
async def test_pairing_is_refused_on_a_read_only_instance(client, session, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await _pair(client)).status_code == 403
    assert (await session.execute(select(ClientToken))).scalars().first() is None


@pytest.mark.asyncio
async def test_the_page_says_so_when_it_cannot_issue_tokens(client, monkeypatch):
    from src.config import get_settings
    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.get("/pair")
    assert resp.status_code == 200 and "read-only" in resp.text


# --- the payload ---------------------------------------------------------------------------------

def test_the_qr_payload_is_the_contract_scanme_expects():
    """``{base_url, token}`` and nothing else — a second field is a second thing to keep in sync."""
    payload = json.loads(pairing_payload("http://192.168.1.5:8080", "scryme_abc"))
    assert payload == {"base_url": "http://192.168.1.5:8080", "token": "scryme_abc"}


def test_the_payload_is_compact_so_the_code_stays_easy_to_scan():
    assert ", " not in pairing_payload("http://x", "y")


def _req(host, port=8080, scheme="http", base=None):
    """Just enough of a Request for ``pairing_base_url``: a base URL and a parsed one."""
    url = type("U", (), {"hostname": host, "port": port, "scheme": scheme})()
    return type("R", (), {"base_url": base or f"{scheme}://{host}:{port}/", "url": url})()


def test_a_reachable_host_is_left_alone():
    from src.routes import pair as pair_routes

    base, state = pair_routes.pairing_base_url(
        _req("cards.example.com", None, "https", "https://cards.example.com/"),
        can_resolve_host_ip=True,
    )
    assert state == pair_routes.REACHABLE and base == "https://cards.example.com"


def test_a_loopback_url_is_rewritten_when_this_process_owns_the_host_network(monkeypatch):
    """The desktop shell binds to the host, so its own routing address is the one a phone wants."""
    from src.routes import pair as pair_routes

    monkeypatch.setattr(pair_routes, "local_ip", lambda: "192.168.1.5")
    base, state = pair_routes.pairing_base_url(_req("127.0.0.1"), can_resolve_host_ip=True)
    assert state == pair_routes.REWRITTEN and base == "http://192.168.1.5:8080"


def test_a_loopback_url_is_left_alone_and_flagged_when_the_host_ip_is_unknowable(monkeypatch):
    """Inside a container ``local_ip`` answers about the container (172.x), which no phone can
    reach — so say we don't know rather than encoding a confidently wrong address."""
    from src.routes import pair as pair_routes

    monkeypatch.setattr(pair_routes, "local_ip", lambda: "172.20.0.3")
    base, state = pair_routes.pairing_base_url(_req("localhost"), can_resolve_host_ip=False)
    assert state == pair_routes.UNREACHABLE
    assert base == "http://localhost:8080" and "172.20" not in base


def test_only_the_host_is_substituted_never_the_scheme(monkeypatch):
    """Hardcoding http would turn an https origin into "http://<ip>:443" — a self-contradiction."""
    from src.routes import pair as pair_routes

    monkeypatch.setattr(pair_routes, "local_ip", lambda: "10.0.0.2")
    base, _ = pair_routes.pairing_base_url(
        _req("localhost", None, "https", "https://localhost/"), can_resolve_host_ip=True
    )
    assert base == "https://10.0.0.2:443"


def test_a_plain_http_origin_keeps_its_scheme_and_default_port(monkeypatch):
    from src.routes import pair as pair_routes

    monkeypatch.setattr(pair_routes, "local_ip", lambda: "10.0.0.2")
    base, _ = pair_routes.pairing_base_url(
        _req("localhost", None, "http", "http://localhost/"), can_resolve_host_ip=True
    )
    assert base == "http://10.0.0.2:80"


@pytest.mark.asyncio
async def test_a_self_hosted_instance_warns_instead_of_guessing_an_address(client, monkeypatch):
    """The common Docker case: the operator opens localhost:8080 and pairs from there."""
    from src.routes import pair as pair_routes

    monkeypatch.setattr(pair_routes, "pairing_base_url",
                        lambda r, **kw: ("http://localhost:8080", pair_routes.UNREACHABLE))
    resp = await _pair(client)
    assert "scryme can't" in resp.text and "only works on this machine" in resp.text


@pytest.mark.asyncio
async def test_the_desktop_rewrite_note_is_shown_when_the_address_was_substituted(
    client, monkeypatch
):
    from src.routes import pair as pair_routes

    monkeypatch.setattr(pair_routes, "pairing_base_url",
                        lambda r, **kw: ("http://192.168.1.5:8080", pair_routes.REWRITTEN))
    resp = await _pair(client)
    assert "uses this machine's network address instead" in resp.text
    assert "192.168.1.5" in resp.text


@pytest.mark.asyncio
async def test_only_the_desktop_shell_is_trusted_to_resolve_the_host_address(client, monkeypatch):
    """``lan_guard`` is what tells pairing that ``local_ip`` speaks for the machine in question."""
    from src.config import get_settings
    from src.routes import pair as pair_routes

    seen = {}

    def spy(request, *, can_resolve_host_ip=False):
        seen["allowed"] = can_resolve_host_ip
        return "http://x", pair_routes.REACHABLE

    monkeypatch.setattr(pair_routes, "pairing_base_url", spy)

    monkeypatch.setattr(get_settings(), "lan_guard", False)
    await client.get("/pair")
    assert seen["allowed"] is False

    monkeypatch.setattr(get_settings(), "lan_guard", True)
    await client.get("/pair")
    assert seen["allowed"] is True


# --- LAN interaction -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_page_warns_when_lan_sharing_would_block_the_paired_device(client, monkeypatch):
    """A desktop instance only answers other devices with LAN sharing on, so a code minted with it
    off would scan cleanly and then fail to connect — worth saying before, not after."""
    from src.config import get_settings
    from src.routes import pair as pair_routes

    monkeypatch.setattr(get_settings(), "lan_guard", True)
    monkeypatch.setattr(pair_routes, "lan_state", lambda _s: {"enabled": False, "code": ""})
    resp = await client.get("/pair")
    assert "LAN sharing is off" in resp.text

    monkeypatch.setattr(pair_routes, "lan_state", lambda _s: {"enabled": True, "code": ""})
    assert "LAN sharing is off" not in (await client.get("/pair")).text


@pytest.mark.asyncio
async def test_no_lan_warning_on_a_self_hosted_instance(client):
    """``lan_guard`` is a desktop-only concern; a Docker deployment is reachable already."""
    assert "LAN sharing is off" not in (await client.get("/pair")).text


@pytest.mark.asyncio
async def test_settings_links_to_the_pairing_page(client):
    resp = await client.get("/settings?tab=devices")
    assert 'href="/pair"' in resp.text
