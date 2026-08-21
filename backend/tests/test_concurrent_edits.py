"""Concurrent-edit safety on a shared collection (#207).

ADR 0001 keeps scryme single-collection on purpose, which means one collection is deliberately
edited by more than one person — so the app owes them a guarantee that two simultaneous edits don't
silently destroy each other.

The distinction these tests exist to pin: **relative** edits (± a copy) are commutative and must
stay unguarded, while **absolute** edits (set the quantity, set the condition, delete) replace what
the editor was looking at and must be refused when it has moved.
"""

import uuid

import pytest
from sqlalchemy import select
from src.collection_edit import (
    StaleStackError,
    add_or_increment,
    adjust_quantity,
    delete_stack,
    edit_stack,
    update_stack,
)
from src.config import get_settings
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns


async def _owned(session, name="Bolt", qty=2, cn="1"):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "TST",
           "collector_number": cn, "type_line": "Instant", "rarity": "rare",
           "prices": {"usd": "1.00"}}
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.flush()
    stack = CollectionCard(scryfall_id=card.scryfall_id, quantity=qty, finish="normal",
                           language="en")
    session.add(stack)
    await session.commit()
    await session.refresh(stack)
    return card, stack


# --- the version moves with every change ---------------------------------------------------------

@pytest.mark.asyncio
async def test_a_new_stack_starts_at_version_one(session):
    _card, stack = await _owned(session)
    assert stack.version == 1


@pytest.mark.asyncio
async def test_every_kind_of_edit_bumps_the_version(session):
    """An absolute edit has to notice a relative one that happened underneath it."""
    card, stack = await _owned(session, qty=3)
    seen = [stack.version]

    await adjust_quantity(session, stack.id, 1)
    await session.refresh(stack)
    seen.append(stack.version)

    await update_stack(session, stack.id, condition="NM")
    await session.refresh(stack)
    seen.append(stack.version)

    await add_or_increment(session, card.scryfall_id, 1, condition="NM")
    await session.refresh(stack)
    seen.append(stack.version)

    assert seen == sorted(set(seen)), seen   # strictly increasing, no repeats


# --- relative edits stay unguarded ---------------------------------------------------------------

@pytest.mark.asyncio
async def test_two_people_each_adding_a_copy_get_two_copies(session):
    """± is commutative. Rejecting the second would reject a correct answer."""
    card, stack = await _owned(session, qty=1)
    await adjust_quantity(session, stack.id, 1)
    await adjust_quantity(session, stack.id, 1)
    await session.refresh(stack)
    assert stack.quantity == 3


@pytest.mark.asyncio
async def test_adding_to_the_collection_is_never_refused(session):
    card, stack = await _owned(session, qty=1)
    stack.version = 99                       # someone else has been busy
    await session.commit()
    assert await add_or_increment(session, card.scryfall_id, 2) is not None
    await session.refresh(stack)
    assert stack.quantity == 3


# --- absolute edits are refused when the row moved -----------------------------------------------

@pytest.mark.asyncio
async def test_update_with_a_stale_version_is_refused_and_changes_nothing(session):
    card, stack = await _owned(session, qty=2)
    stale = stack.version
    await adjust_quantity(session, stack.id, 1)     # someone else edits

    with pytest.raises(StaleStackError) as exc:
        await update_stack(session, stack.id, quantity=10, expected_version=stale)
    await session.refresh(stack)
    assert stack.quantity == 3                       # their change survived, ours didn't apply
    assert exc.value.stack.version == stack.version  # the error carries the current row


@pytest.mark.asyncio
async def test_update_with_the_current_version_succeeds(session):
    _card, stack = await _owned(session)
    updated = await update_stack(session, stack.id, condition="LP",
                                 expected_version=stack.version)
    assert updated.condition == "LP"


@pytest.mark.asyncio
async def test_delete_with_a_stale_version_is_refused(session):
    """Deleting a stack someone just changed is the most destructive way to lose their edit."""
    _card, stack = await _owned(session, qty=2)
    stale = stack.version
    await adjust_quantity(session, stack.id, 1)

    with pytest.raises(StaleStackError):
        await delete_stack(session, stack.id, expected_version=stale)
    assert await session.get(CollectionCard, stack.id) is not None


@pytest.mark.asyncio
async def test_edit_stack_with_a_stale_version_is_refused(session):
    card, stack = await _owned(session, qty=2)
    stale = stack.version
    await adjust_quantity(session, stack.id, 1)

    with pytest.raises(StaleStackError):
        await edit_stack(session, stack.id, quantity=9, expected_version=stale)
    await session.refresh(stack)
    assert stack.quantity == 3


@pytest.mark.asyncio
async def test_omitting_the_version_opts_out(session):
    """Internal callers aren't replaying a human's stale view, so they don't have one to send."""
    _card, stack = await _owned(session, qty=2)
    await adjust_quantity(session, stack.id, 1)
    assert await update_stack(session, stack.id, quantity=7) is not None
    await session.refresh(stack)
    assert stack.quantity == 7


# --- over HTTP: the card page ---------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_edit_form_carries_the_current_version(client, session):
    _card, stack = await _owned(session)
    page = (await client.get(f"/card/{stack.scryfall_id}")).text
    assert f'name="version" value="{stack.version}"' in page


@pytest.mark.asyncio
async def test_a_stale_edit_returns_409_and_shows_the_current_state(client, session):
    card, stack = await _owned(session, qty=2)
    stale = stack.version
    await adjust_quantity(session, stack.id, 1)      # the other person's edit

    resp = await client.post(f"/collection/stack/{stack.id}/edit", data={
        "card_id": str(card.scryfall_id), "quantity": "10", "version": str(stale),
    })
    assert resp.status_code == 409
    assert "changed somewhere else" in resp.text
    # The response body is the panel as it is NOW, so the editor can re-apply deliberately.
    assert 'id="card-collection"' in resp.text
    await session.refresh(stack)
    assert stack.quantity == 3


@pytest.mark.asyncio
async def test_a_stale_delete_returns_409_and_keeps_the_stack(client, session):
    _card, stack = await _owned(session, qty=2)
    stale = stack.version
    await adjust_quantity(session, stack.id, 1)

    resp = await client.post(f"/collection/stack/{stack.id}/delete",
                             data={"version": str(stale)})
    assert resp.status_code == 409
    assert await session.get(CollectionCard, stack.id) is not None


@pytest.mark.asyncio
async def test_a_current_edit_over_http_applies(client, session):
    card, stack = await _owned(session, qty=2)
    resp = await client.post(f"/collection/stack/{stack.id}/edit", data={
        "card_id": str(card.scryfall_id), "quantity": "5", "version": str(stack.version),
    })
    assert resp.status_code == 200
    await session.refresh(stack)
    assert stack.quantity == 5


@pytest.mark.asyncio
async def test_a_page_without_a_version_still_works(client, session):
    """An old tab that predates the guard degrades to the previous behaviour, not to broken."""
    card, stack = await _owned(session, qty=2)
    resp = await client.post(f"/collection/stack/{stack.id}/edit", data={
        "card_id": str(card.scryfall_id), "quantity": "6",
    })
    assert resp.status_code == 200
    await session.refresh(stack)
    assert stack.quantity == 6
    # A non-numeric version is treated the same way rather than 500ing.
    assert (await client.post(f"/collection/stack/{stack.id}/delete",
                              data={"version": "garbage"})).status_code == 200


@pytest.mark.asyncio
async def test_deleting_a_missing_stack_is_still_404(client, session):
    assert (await client.post("/collection/stack/9999/delete")).status_code == 404


# --- over HTTP: the JSON API ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_api_exposes_the_version(client, session):
    _card, stack = await _owned(session)
    rows = (await client.get("/api/v1/collection")).json()["items"]
    assert rows[0]["version"] == stack.version


@pytest.mark.asyncio
async def test_the_api_refuses_a_stale_patch_with_409(client, session):
    _card, stack = await _owned(session, qty=2)
    stale = stack.version
    await adjust_quantity(session, stack.id, 1)

    resp = await client.patch(f"/api/v1/collection/{stack.id}",
                              json={"quantity": 10, "version": stale})
    assert resp.status_code == 409
    detail = resp.json()["detail"]
    assert detail["error"] == "stale"
    # The current version comes back so a client can retry deliberately, not blindly.
    await session.refresh(stack)
    assert detail["current_version"] == stack.version
    assert stack.quantity == 3


@pytest.mark.asyncio
async def test_the_api_applies_a_patch_at_the_current_version(client, session):
    _card, stack = await _owned(session, qty=2)
    resp = await client.patch(f"/api/v1/collection/{stack.id}",
                              json={"quantity": 4, "version": stack.version})
    assert resp.status_code == 200
    body = resp.json()
    assert body["quantity"] == 4 and body["version"] > stack.version


@pytest.mark.asyncio
async def test_the_api_patch_without_a_version_still_applies(client, session):
    """Existing clients keep working — the guard is opt-in per request."""
    _card, stack = await _owned(session, qty=2)
    resp = await client.patch(f"/api/v1/collection/{stack.id}", json={"quantity": 8})
    assert resp.status_code == 200 and resp.json()["quantity"] == 8


@pytest.mark.asyncio
async def test_the_api_refuses_a_stale_delete_with_409(client, session):
    _card, stack = await _owned(session, qty=2)
    stale = stack.version
    await adjust_quantity(session, stack.id, 1)

    assert (await client.delete(
        f"/api/v1/collection/{stack.id}?version={stale}")).status_code == 409
    assert await session.get(CollectionCard, stack.id) is not None
    # At the current version it goes.
    await session.refresh(stack)
    assert (await client.delete(
        f"/api/v1/collection/{stack.id}?version={stack.version}")).status_code == 200
    assert (await session.execute(select(CollectionCard))).scalars().all() == []


@pytest.mark.asyncio
async def test_conflicts_cannot_arise_on_a_read_only_instance(client, session, monkeypatch):
    """No writes, no conflicts — the guard must not turn into a 409 where a 403 belongs."""
    _card, stack = await _owned(session)
    monkeypatch.setattr(get_settings(), "read_only", True)
    resp = await client.patch(f"/api/v1/collection/{stack.id}",
                              json={"quantity": 3, "version": 999})
    assert resp.status_code == 403
