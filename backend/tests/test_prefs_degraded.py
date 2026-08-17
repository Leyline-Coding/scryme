"""Pages keep rendering when the preferences singleton can't be read (#203).

``_install_prefs_loader`` promises it "never 500s a request over prefs: a DB hiccup (or the table
not yet migrated) falls back to ``None``, and the read helpers use their existing cookie/default
path." That degradation is the *only* time the cookie fallbacks in ``routes/card._hist_currency``
and ``routes/search._display_prefs`` run in production — and "the table not yet migrated" is a real
scenario, an instance upgraded before ``alembic upgrade head`` has caught up.

These tests make every ``Preferences`` lookup fail and assert the pages still render *and still
honor the visitor's cookies*, rather than 500ing or silently reverting to defaults.
"""

import datetime
import uuid

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncSession
from src.models import Card, CollectionCard, FxRateHistory, Preferences
from src.prices import snapshot_prices
from src.scryfall.mapping import card_to_columns


@pytest.fixture
def prefs_unavailable(monkeypatch):
    """Fail every Preferences read, as if the table were missing or the DB hiccuped."""
    real_get = AsyncSession.get

    async def failing_get(self, entity, ident, *args, **kwargs):
        if entity is Preferences:
            raise OperationalError(
                "SELECT preferences", {}, Exception('relation "preferences" does not exist')
            )
        return await real_get(self, entity, ident, *args, **kwargs)

    monkeypatch.setattr(AsyncSession, "get", failing_get)


async def _own(session, name, type_line="Instant", usd=None):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name,
           "set": "tst", "collector_number": str(abs(hash(name)) % 9999),
           "type_line": type_line}
    if usd:
        raw["prices"] = {"usd": usd}
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1, finish="normal"))
    await session.commit()
    return card


@pytest.mark.asyncio
async def test_search_still_renders_and_honors_cookies(client, session, prefs_unavailable):
    await _own(session, "Counterspell", "Instant")
    await _own(session, "Grizzly Bears", "Creature — Bear")

    resp = await client.get("/search", headers={"Cookie": "scryme_search_filter=t:instant"})
    assert resp.status_code == 200  # the promise: never 500 over prefs
    # …and the raw-cookie fallback still applies the universal filter.
    assert "Counterspell" in resp.text and "Grizzly Bears" not in resp.text


@pytest.mark.asyncio
async def test_card_page_still_renders_and_honors_cookies(client, session, prefs_unavailable):
    card = await _own(session, "Charted Card", usd="10.00")
    await snapshot_prices(session)
    await snapshot_prices(session)  # two points -> a trend line
    session.add(FxRateHistory(
        code="gbp", date=datetime.datetime.now(datetime.UTC).date(), rate=0.5
    ))
    await session.commit()

    resp = await client.get(
        f"/card/{card.scryfall_id}", headers={"Cookie": "scryme_hist_currency=gbp"}
    )
    assert resp.status_code == 200
    assert "£5.00" in resp.text  # 10 USD * 0.5, resolved from the cookie with no prefs available


@pytest.mark.asyncio
async def test_home_still_renders(client, session, prefs_unavailable):
    """A page with no prefs cookies at all falls through to plain defaults."""
    assert (await client.get("/")).status_code == 200
