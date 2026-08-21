"""Read-only share links (#80).

These are the only routes that serve someone who isn't the owner, so most of what's pinned here is
about what a stranger holding a URL can and cannot reach.
"""

import uuid

import pytest
from sqlalchemy import select
from src.client_tokens import hash_token
from src.config import get_settings
from src.decks import create_deck
from src.models import Binder, BinderCard, Card, ShareLink
from src.scryfall.mapping import card_to_columns
from src.share import (
    BINDER,
    DECK,
    create_share_link,
    links_for,
    resolve_share_link,
    revoke_links_for,
    revoke_share_link,
)


async def _card(session, name="Bolt", cn="1", usd="3.50"):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "TST",
           "collector_number": cn, "type_line": "Instant", "rarity": "rare",
           "prices": {"usd": usd}}
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.commit()
    return card


async def _deck(session, name="Burn"):
    await _card(session, "Lightning Bolt")
    return await create_deck(session, name, "4 Lightning Bolt")


async def _binder(session, name="Trades"):
    card = await _card(session, "Shock", cn="2", usd="1.25")
    binder = Binder(name=name)
    session.add(binder)
    await session.flush()
    session.add(BinderCard(binder_id=binder.id, scryfall_id=card.scryfall_id))
    await session.commit()
    await session.refresh(binder)
    return binder


# --- minting -------------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_link_stores_only_a_hash_of_its_token(session):
    """The token travels in a pasted URL, so a database dump must not yield working links."""
    deck = await _deck(session)
    row, token = await create_share_link(session, DECK, deck.id)
    assert len(token) > 20
    assert row.token_hash == hash_token(token)
    stored = (await session.execute(select(ShareLink))).scalar_one()
    assert token not in stored.token_hash


@pytest.mark.asyncio
async def test_prices_are_off_unless_asked_for(session):
    """Sharing a list shouldn't also disclose what it's worth."""
    deck = await _deck(session)
    plain, _ = await create_share_link(session, DECK, deck.id)
    priced, _ = await create_share_link(session, DECK, deck.id, show_prices=True)
    assert plain.show_prices is False and priced.show_prices is True


@pytest.mark.asyncio
async def test_every_token_is_distinct(session):
    deck = await _deck(session)
    _, a = await create_share_link(session, DECK, deck.id)
    _, b = await create_share_link(session, DECK, deck.id)
    assert a != b


@pytest.mark.asyncio
async def test_unknown_targets_and_kinds_are_refused(session):
    deck = await _deck(session)
    assert await create_share_link(session, DECK, 999) is None
    assert await create_share_link(session, BINDER, 999) is None
    assert await create_share_link(session, "collection", deck.id) is None
    assert (await session.execute(select(ShareLink))).scalars().all() == []


# --- resolving -----------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_only_the_real_token_resolves(session):
    deck = await _deck(session)
    _, token = await create_share_link(session, DECK, deck.id)
    assert (await resolve_share_link(session, token)).target_id == deck.id
    assert await resolve_share_link(session, token + "x") is None
    assert await resolve_share_link(session, "") is None


@pytest.mark.asyncio
async def test_a_revoked_link_stops_resolving_but_the_record_remains(session):
    deck = await _deck(session)
    row, token = await create_share_link(session, DECK, deck.id)
    assert await revoke_share_link(session, row.id) is True
    assert await resolve_share_link(session, token) is None
    await session.refresh(row)
    assert row.revoked_at is not None and not row.active
    assert (await session.execute(select(ShareLink))).scalar_one() is not None
    # Idempotent, and the original timestamp is kept.
    first = row.revoked_at
    await revoke_share_link(session, row.id)
    await session.refresh(row)
    assert row.revoked_at == first
    assert await revoke_share_link(session, 999) is False


@pytest.mark.asyncio
async def test_viewing_records_when_it_was_last_opened(session):
    deck = await _deck(session)
    row, token = await create_share_link(session, DECK, deck.id)
    assert row.last_viewed_at is None
    await resolve_share_link(session, token)
    await session.refresh(row)
    assert row.last_viewed_at is not None


@pytest.mark.asyncio
async def test_links_are_listed_live_first(session):
    deck = await _deck(session)
    old, _ = await create_share_link(session, DECK, deck.id)
    await create_share_link(session, DECK, deck.id)
    await revoke_share_link(session, old.id)
    listed = await links_for(session, DECK, deck.id)
    assert listed[0].active and not listed[-1].active


@pytest.mark.asyncio
async def test_revoking_all_links_for_a_target(session):
    deck = await _deck(session)
    await create_share_link(session, DECK, deck.id)
    await create_share_link(session, DECK, deck.id)
    assert await revoke_links_for(session, DECK, deck.id) == 2
    assert await revoke_links_for(session, DECK, deck.id) == 0   # already withdrawn


# --- the public view -----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_shared_deck_renders_its_list(client, session):
    deck = await _deck(session)
    _, token = await create_share_link(session, DECK, deck.id)
    page = await client.get(f"/share/{token}")
    assert page.status_code == 200
    assert "Burn" in page.text and "Lightning Bolt" in page.text
    assert "shared · read-only" in page.text


@pytest.mark.asyncio
async def test_a_shared_view_offers_no_way_into_the_rest_of_the_collection(client, session):
    """A stranger with this URL must not be handed navigation into everything else."""
    deck = await _deck(session)
    _, token = await create_share_link(session, DECK, deck.id)
    page = (await client.get(f"/share/{token}")).text
    for path in ('href="/"', 'href="/search', 'href="/collection', 'href="/decks',
                 'href="/settings'):
        assert path not in page, path
    # …and no edit affordances at all.
    assert "hx-post" not in page and "<form" not in page

    # Named explicitly because these are write vectors, not just navigation: the settings panel
    # PATCHes /prefs, and the drop zone posts to /upload. An earlier revision of this leaked both
    # by extending the owner shell.
    for owner_control in ("/prefs", "/upload", "/admin", "/backup"):
        assert owner_control not in page, owner_control


@pytest.mark.asyncio
async def test_prices_appear_only_when_the_link_says_so(client, session):
    deck = await _deck(session)
    _, plain = await create_share_link(session, DECK, deck.id)
    _, priced = await create_share_link(session, DECK, deck.id, show_prices=True)
    assert "$14.00" not in (await client.get(f"/share/{plain}")).text
    assert "$14.00" in (await client.get(f"/share/{priced}")).text   # 4 × $3.50


@pytest.mark.asyncio
async def test_a_shared_binder_renders_its_cards(client, session):
    binder = await _binder(session)
    _, token = await create_share_link(session, BINDER, binder.id)
    page = (await client.get(f"/share/{token}")).text
    assert "Trades" in page and "Shock" in page
    assert "$1.25" not in page                       # prices off by default


@pytest.mark.asyncio
async def test_an_empty_binder_shares_cleanly(client, session):
    binder = Binder(name="Empty")
    session.add(binder)
    await session.commit()
    await session.refresh(binder)
    _, token = await create_share_link(session, BINDER, binder.id)
    assert "This binder is empty" in (await client.get(f"/share/{token}")).text


@pytest.mark.asyncio
async def test_unknown_revoked_and_deleted_all_look_the_same(client, session):
    """One status for all three: telling a stranger a token was once real is a needless oracle."""
    deck = await _deck(session)
    row, token = await create_share_link(session, DECK, deck.id)
    assert (await client.get("/share/never-existed")).status_code == 404

    await revoke_share_link(session, row.id)
    assert (await client.get(f"/share/{token}")).status_code == 404


@pytest.mark.asyncio
async def test_a_link_to_a_deleted_deck_404s_rather_than_erroring(client, session):
    deck = await _deck(session)
    row, token = await create_share_link(session, DECK, deck.id)
    row.revoked_at = None
    await session.commit()
    await session.delete(await session.get(type(deck), deck.id))
    await session.commit()
    assert (await client.get(f"/share/{token}")).status_code == 404


@pytest.mark.asyncio
async def test_a_link_to_a_deleted_binder_404s_rather_than_erroring(client, session):
    binder = await _binder(session)
    row, token = await create_share_link(session, BINDER, binder.id)
    row.revoked_at = None
    await session.commit()
    await session.delete(await session.get(Binder, binder.id))
    await session.commit()
    assert (await client.get(f"/share/{token}")).status_code == 404


@pytest.mark.asyncio
async def test_a_shared_view_ignores_the_visitors_own_cookies(client, session):
    """Two people opening one link must see the same page — the view has no viewer preferences."""
    deck = await _deck(session)
    _, token = await create_share_link(session, DECK, deck.id, show_prices=True)
    plain = (await client.get(f"/share/{token}")).text
    with_cookie = (await client.get(f"/share/{token}",
                                    headers={"Cookie": "scryme_currency=eur"})).text
    assert "$14.00" in plain and "$14.00" in with_cookie


# --- owner-side management ------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_creating_a_link_from_the_deck_page_shows_it_once(client, session):
    deck = await _deck(session)
    resp = await client.post(f"/share/deck/{deck.id}", data={"show_prices": "1"})
    assert resp.status_code == 303
    token = resp.headers["location"].split("shared=")[1]

    page = (await client.get(f"/decks/{deck.id}?shared={token}")).text
    assert "Link created" in page and f"/share/{token}" in page
    assert "prices shown" in page
    # Reloading without the token no longer shows it — only the hash is stored.
    later = (await client.get(f"/decks/{deck.id}")).text
    assert "Link created" not in later and token not in later


@pytest.mark.asyncio
async def test_the_binder_page_lists_and_revokes(client, session):
    binder = await _binder(session)
    row, _ = await create_share_link(session, BINDER, binder.id)
    page = (await client.get(f"/binders/view/{binder.id}")).text
    assert "Share a read-only link" in page and "Revoke" in page

    resp = await client.post(f"/share/link/{row.id}/revoke",
                             data={"kind": BINDER, "target_id": binder.id})
    assert resp.status_code == 303
    await session.refresh(row)
    assert row.revoked_at is not None
    assert "revoked" in (await client.get(f"/binders/view/{binder.id}")).text


@pytest.mark.asyncio
async def test_sharing_something_that_does_not_exist_is_404(client):
    assert (await client.post("/share/deck/999")).status_code == 404


@pytest.mark.asyncio
async def test_deleting_the_target_withdraws_its_links(client, session):
    deck = await _deck(session)
    row, token = await create_share_link(session, DECK, deck.id)
    await client.post(f"/decks/{deck.id}/delete")
    await session.refresh(row)
    assert row.revoked_at is not None
    assert (await client.get(f"/share/{token}")).status_code == 404


@pytest.mark.asyncio
async def test_deleting_a_binder_withdraws_its_links(client, session):
    binder = await _binder(session)
    row, _ = await create_share_link(session, BINDER, binder.id)
    await client.post(f"/binders/{binder.id}/delete")
    await session.refresh(row)
    assert row.revoked_at is not None


@pytest.mark.asyncio
async def test_read_only_cannot_mint_or_revoke_but_can_still_serve(client, session, monkeypatch):
    """A shared demo can show its links; it just can't hand out new ones."""
    deck = await _deck(session)
    row, token = await create_share_link(session, DECK, deck.id)
    monkeypatch.setattr(get_settings(), "read_only", True)

    assert (await client.post(f"/share/deck/{deck.id}")).status_code == 403
    assert (await client.post(f"/share/link/{row.id}/revoke",
                              data={"kind": DECK, "target_id": deck.id})).status_code == 403
    # Viewing is a read, so it keeps working.
    assert (await client.get(f"/share/{token}")).status_code == 200
    assert "Create link" not in (await client.get(f"/decks/{deck.id}")).text
