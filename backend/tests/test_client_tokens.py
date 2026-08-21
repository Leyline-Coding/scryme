"""Per-device API tokens: issue, scope, revoke, and the two rules #204 left open (#204).

The rules being pinned here, because both are security-relevant and neither is obvious:

* **write implies read, read does not imply write** — enforced by HTTP method, so a new endpoint
  can't be added without inheriting protection.
* **revoking every token does not reopen the API.** Falling open on revocation would make the one
  action you take *because* something went wrong the action that removes the lock.
"""

import datetime

import pytest
from sqlalchemy import select
from src.client_tokens import (
    TOKEN_MARKER,
    auth_required,
    hash_token,
    issue_token,
    list_tokens,
    revoke_token,
    scope_for_method,
    verify_token,
)
from src.config import get_settings
from src.models import ClientToken

# --- issuing -------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_issuing_returns_a_secret_that_is_never_stored(session):
    row, secret = await issue_token(session, "Pixel scanner")
    assert secret.startswith(TOKEN_MARKER) and len(secret) > 40
    # Only the hash is persisted — a dump of this table yields no working credential.
    assert row.token_hash == hash_token(secret)
    assert secret not in row.token_hash
    assert row.prefix and row.prefix in secret
    assert row.label == "Pixel scanner" and row.scope == "write" and row.active


def test_stored_hashes_are_keyed_to_this_instance(tmp_path, monkeypatch):
    """A dump of client_token is not enough to check a guess — the key lives outside the DB.

    Pinning this because it is the whole reason a fast hash is acceptable here: the protection a
    password KDF would provide comes from the pepper instead.
    """
    from src import client_tokens
    from src.config import get_settings

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "a")
    here = client_tokens.hash_token("scryme_sample")
    assert here == client_tokens.hash_token("scryme_sample")   # stable within an instance

    monkeypatch.setattr(get_settings(), "data_dir", tmp_path / "b")
    assert client_tokens.hash_token("scryme_sample") != here   # different instance, different hash

    key = tmp_path / "a" / "tokens.key"
    assert key.exists() and len(key.read_bytes()) == 32
    assert key.stat().st_mode & 0o077 == 0        # not readable by anyone else
    assert b"scryme_sample" not in key.read_bytes()


@pytest.mark.asyncio
async def test_every_token_is_distinct(session):
    _, a = await issue_token(session, "One")
    _, b = await issue_token(session, "Two")
    assert a != b


@pytest.mark.asyncio
async def test_label_and_scope_are_normalized(session):
    row, _ = await issue_token(session, "   ", scope="superuser")
    assert row.label == "Unnamed device" and row.scope == "write"
    readonly, _ = await issue_token(session, "Kiosk", scope="read")
    assert readonly.scope == "read"


# --- verifying -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_verify_accepts_the_real_secret_only(session):
    _, secret = await issue_token(session, "Scanner")
    assert await verify_token(session, secret, "write") is not None
    assert await verify_token(session, secret + "x", "write") is None
    assert await verify_token(session, "", "write") is None


@pytest.mark.asyncio
async def test_a_read_token_cannot_write_but_a_write_token_can_read(session):
    _, ro = await issue_token(session, "Kiosk", scope="read")
    _, rw = await issue_token(session, "Scanner", scope="write")
    assert await verify_token(session, ro, "read") is not None
    assert await verify_token(session, ro, "write") is None
    assert await verify_token(session, rw, "read") is not None
    assert await verify_token(session, rw, "write") is not None


def test_only_safe_methods_count_as_reads():
    assert scope_for_method("GET") == "read" and scope_for_method("head") == "read"
    for method in ("POST", "PATCH", "PUT", "DELETE", ""):
        assert scope_for_method(method) == "write", method


@pytest.mark.asyncio
async def test_a_revoked_token_stops_working_but_the_record_remains(session):
    row, secret = await issue_token(session, "Lost phone")
    assert await revoke_token(session, row.id) is True
    assert await verify_token(session, secret, "read") is None
    await session.refresh(row)
    assert row.revoked_at is not None and not row.active
    assert (await session.execute(select(ClientToken))).scalar_one() is not None
    # Revoking twice is fine, and the original timestamp is kept.
    first = row.revoked_at
    assert await revoke_token(session, row.id) is True
    await session.refresh(row)
    assert row.revoked_at == first
    assert await revoke_token(session, 999) is False


@pytest.mark.asyncio
async def test_last_used_is_recorded_then_throttled(session):
    row, secret = await issue_token(session, "Scanner")
    assert row.last_used_at is None
    await verify_token(session, secret, "read")
    await session.refresh(row)
    first = row.last_used_at
    assert first is not None

    await verify_token(session, secret, "read")   # immediately again: not rewritten
    await session.refresh(row)
    assert row.last_used_at == first

    row.last_used_at = first - datetime.timedelta(minutes=5)
    await session.commit()
    await verify_token(session, secret, "read")
    await session.refresh(row)
    assert row.last_used_at > first - datetime.timedelta(minutes=5)


@pytest.mark.asyncio
async def test_tokens_are_listed_active_first(session):
    old, _ = await issue_token(session, "Old")
    await issue_token(session, "New")
    await revoke_token(session, old.id)
    assert [t.label for t in await list_tokens(session)] == ["New", "Old"]


# --- when the API is gated -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_is_open_until_the_first_token_is_issued(session):
    assert await auth_required(session) is False
    await issue_token(session, "Scanner")
    assert await auth_required(session) is True


@pytest.mark.asyncio
async def test_revoking_every_token_does_not_reopen_the_api(session):
    """The whole point of revocation is to lock something out; it must not unlock everything."""
    row, _ = await issue_token(session, "Only device")
    await revoke_token(session, row.id)
    assert await auth_required(session) is True


@pytest.mark.asyncio
async def test_the_env_token_alone_still_gates_the_api(session, monkeypatch):
    monkeypatch.setattr(get_settings(), "api_token", "secret")
    assert await auth_required(session) is True


# --- over HTTP -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_api_is_open_with_no_tokens_configured(client):
    assert (await client.get("/api/v1/preferences")).status_code == 200


@pytest.mark.asyncio
async def test_a_device_token_authenticates_both_header_styles(client, session):
    _, secret = await issue_token(session, "Scanner")
    assert (await client.get("/api/v1/preferences")).status_code == 401
    for headers in ({"Authorization": f"Bearer {secret}"}, {"X-API-Key": secret}):
        assert (await client.get("/api/v1/preferences", headers=headers)).status_code == 200


@pytest.mark.asyncio
async def test_a_read_token_is_refused_on_a_mutation(client, session):
    _, ro = await issue_token(session, "Kiosk", scope="read")
    h = {"X-API-Key": ro}
    assert (await client.get("/api/v1/preferences", headers=h)).status_code == 200
    assert (await client.patch("/api/v1/preferences", json={"mode": "light"},
                               headers=h)).status_code == 401


@pytest.mark.asyncio
async def test_a_write_token_may_mutate(client, session):
    _, rw = await issue_token(session, "Scanner")
    r = await client.patch("/api/v1/preferences", json={"mode": "light"},
                           headers={"X-API-Key": rw})
    assert r.status_code == 200 and r.json()["mode"] == "light"


@pytest.mark.asyncio
async def test_a_revoked_token_is_refused_over_http(client, session):
    row, secret = await issue_token(session, "Lost phone")
    await revoke_token(session, row.id)
    assert (await client.get("/api/v1/preferences",
                             headers={"X-API-Key": secret})).status_code == 401


@pytest.mark.asyncio
async def test_the_legacy_env_token_still_works_alongside_device_tokens(client, session,
                                                                       monkeypatch):
    """An operator who set SCRYME_API_TOKEN meant "this whole API" — that keeps working."""
    monkeypatch.setattr(get_settings(), "api_token", "legacy")
    _, device = await issue_token(session, "Scanner", scope="read")
    assert (await client.get("/api/v1/preferences",
                             headers={"X-API-Key": "legacy"})).status_code == 200
    # …and unlike a read-scoped device token, it may mutate.
    assert (await client.patch("/api/v1/preferences", json={"mode": "light"},
                               headers={"X-API-Key": "legacy"})).status_code == 200
    assert (await client.patch("/api/v1/preferences", json={"mode": "light"},
                               headers={"X-API-Key": device})).status_code == 401
    assert (await client.get("/api/v1/preferences",
                             headers={"X-API-Key": "wrong"})).status_code == 401


# --- the settings UI -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_creating_a_token_shows_it_once_and_only_once(client, session):
    resp = await client.post("/settings/devices", data={"label": "Pixel scanner",
                                                        "scope": "read"})
    assert resp.status_code == 200
    row = (await session.execute(select(ClientToken))).scalar_one()
    # The secret is in this response body and nowhere else — not in a URL, not in the DB.
    assert TOKEN_MARKER in resp.text and "Copy it now" in resp.text
    assert row.token_hash not in resp.text
    assert "Pixel scanner" in resp.text

    later = await client.get("/settings?tab=devices")
    assert "Copy it now" not in later.text
    assert f"scryme_{row.prefix}" in later.text   # identifiable, but not usable


@pytest.mark.asyncio
async def test_the_devices_tab_lists_and_revokes(client, session):
    row, _ = await issue_token(session, "Old tablet")
    page = (await client.get("/settings")).text
    assert "Old tablet" in page and "Revoke" in page and "New device token" in page

    assert (await client.post(f"/settings/devices/{row.id}/revoke")).status_code == 303
    await session.refresh(row)
    assert row.revoked_at is not None
    assert "revoked" in (await client.get("/settings")).text


@pytest.mark.asyncio
async def test_the_settings_page_opens_on_the_requested_tab(client):
    assert "tab: 'devices'" in (await client.get("/settings?tab=devices")).text
    assert "tab: 'collection'" in (await client.get("/settings?tab=nonsense")).text


@pytest.mark.asyncio
async def test_device_management_is_blocked_read_only(client, session, monkeypatch):
    row, _ = await issue_token(session, "Scanner")
    monkeypatch.setattr(get_settings(), "read_only", True)
    assert (await client.post("/settings/devices", data={"label": "x"})).status_code == 403
    assert (await client.post(f"/settings/devices/{row.id}/revoke")).status_code == 403
    # Reading the tab is fine; it just offers no controls.
    page = (await client.get("/settings?tab=devices")).text
    assert page.count("New device token") == 0 and "Scanner" in page
