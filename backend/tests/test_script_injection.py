"""Regression tests for script/markup injection through values that reach a JS, CSS or URL context.

HTML-escaping protects element text and attribute *values*. It does nothing for a value the
browser then interprets again — code in an inline handler or Alpine expression, CSS in ``style``,
a URL in ``href``, JSON in ``hx-vals``, or an htmx response swapped in with ``innerHTML``. Each
test here plants a hostile value in one of those places and pins both halves of the fix: the
dangerous construct is gone, and the feature it belonged to still has what it needs to work.
"""

import html
import importlib.util
import json
import re
import uuid
from pathlib import Path

import pytest
from sqlalchemy import select, text
from src.config import get_settings
from src.deck_import import detect_host, normalize_deck_url
from src.decks import create_deck
from src.models import Binder, Box, Card, CollectionCard, Deck, Location, NLQCache, Sleeve
from src.routes import decks as decks_route
from src.sanitize import css_color, hex_color, http_url, local_path, target_selector
from src.scryfall.mapping import card_to_columns
from src.share import BINDER, DECK, create_share_link, just_minted_token, revoke_share_link

# Breaks out of a single-quoted JS string; harmless once it is only ever attribute *data*.
JS_BREAKOUT = "x'+alert(document.domain)+'"
# Markup that executes if it is ever parsed as HTML.
MARKUP = "<img src=x onerror=alert(1)>"

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32
HTML_DOC = b"<!doctype html><script>alert(document.domain)</script>"


def _attr(body: str, name: str, *, after: str = "") -> str:
    """The decoded value of the first *name* attribute (optionally the first one after *after*)."""
    start = body.index(after) if after else 0
    m = re.search(rf'{re.escape(name)}\s*=\s*"([^"]*)"', body[start:])
    assert m, f"no {name} attribute found"
    return html.unescape(m.group(1))


async def _card(session, name="Lightning Bolt", tags=None):
    raw = {"id": str(uuid.uuid4()), "oracle_id": str(uuid.uuid4()), "name": name, "set": "TST",
           "collector_number": "1", "type_line": "Instant", "rarity": "rare", "cmc": 1,
           "legalities": {}, "prices": {"usd": "1.00"}}
    card = Card(**card_to_columns(raw))
    session.add(card)
    await session.flush()
    session.add(CollectionCard(scryfall_id=card.scryfall_id, quantity=1, tags=tags))
    await session.commit()
    return card


async def _reload(session, model, pk):
    """The row as the database now has it (the app wrote through a different session)."""
    session.expire_all()
    return await session.get(model, pk)


async def _deck(session, name="Burn"):
    await _card(session)
    return await create_deck(session, name, "4 Lightning Bolt")


async def _binder(session, name="Trades"):
    binder = Binder(name=name)
    session.add(binder)
    await session.flush()
    session.add(Location(type="binder", foreign_id=binder.id))
    await session.commit()
    return binder


async def _box(session, name="Bulk"):
    box = Box(name=name)
    session.add(box)
    await session.flush()
    session.add(Location(type="box", foreign_id=box.id))
    await session.commit()
    return box


# --- 1. values inside Alpine x-data expressions ---------------------------------------------------

@pytest.mark.asyncio
async def test_search_dir_cannot_break_out_of_the_alpine_expression(client):
    payload = "'+alert(document.domain)+'"
    body = (await client.get("/search", params={"dir": payload})).text
    assert "alert(document.domain)" not in body
    # Normalised by the route, and emitted as a JSON string rather than between hand-written quotes.
    assert "dir: &#34;asc&#34;" in body
    assert "dir: '" not in body


@pytest.mark.asyncio
async def test_search_dir_and_scope_still_reach_the_page_state(client):
    body = (await client.get("/search", params={"dir": "desc", "scope": "all"})).text
    assert "dir: &#34;desc&#34;" in body
    assert "scope: &#34;all&#34;" in body
    # An unknown scope falls back rather than being echoed.
    body = (await client.get("/search", params={"scope": "'+alert(1)+'"})).text
    assert "scope: &#34;collection&#34;" in body and "alert(1)" not in body


@pytest.mark.asyncio
async def test_advanced_prefill_is_emitted_as_json_not_a_python_repr(client):
    body = (await client.get("/advanced", params={"q": "t:goblin"})).text
    arg = _attr(body, "x-data").removeprefix("advancedSearch(").removesuffix(")")
    assert json.loads(arg)["type"] == "goblin"


@pytest.mark.asyncio
async def test_card_page_history_currency_is_a_json_string(client, session):
    card = await _card(session)
    body = (await client.get(f"/card/{card.scryfall_id}")).text
    assert re.search(r'x-data="histCurrency\(&#34;[a-z]{3}&#34;\)"', body)
    assert "histCurrency('" not in body


@pytest.mark.asyncio
async def test_settings_initial_tab_is_a_json_string(client):
    body = (await client.get("/settings", params={"tab": "'+alert(1)+'"})).text
    assert "tab: &#34;collection&#34;" in body
    assert "alert(1)" not in body


# --- 2. sleeve image upload ----------------------------------------------------------------------

async def _upload(client, data: bytes, filename: str, content_type: str, **form):
    return await client.post(
        "/sleeves/new", data={"name": "S", **form},
        files={"image": (filename, data, content_type)}, follow_redirects=False,
    )


async def _only_sleeve(session) -> Sleeve | None:
    return (await session.execute(select(Sleeve))).scalars().first()


@pytest.mark.asyncio
@pytest.mark.parametrize(("data", "filename", "content_type"), [
    (HTML_DOC, "evil.html", "text/html"),              # honest about being HTML
    (HTML_DOC, "evil.png", "image/png"),               # lies in the name and the type
    (PNG, "evil.html", "text/html"),                   # image bytes, non-image type
    (b"<svg xmlns='http://www.w3.org/2000/svg' onload='alert(1)'/>", "x.svg", "image/svg+xml"),
])
async def test_sleeve_upload_rejects_anything_that_is_not_a_raster_image(
    client, session, data, filename, content_type
):
    resp = await _upload(client, data, filename, content_type)
    assert resp.status_code == 400
    assert await _only_sleeve(session) is None


@pytest.mark.asyncio
@pytest.mark.parametrize(("data", "ext", "media"), [
    (PNG, ".png", "image/png"), (JPEG, ".jpg", "image/jpeg"),
    (GIF, ".gif", "image/gif"), (WEBP, ".webp", "image/webp"),
])
async def test_sleeve_image_is_stored_and_served_by_its_detected_type(
    client, session, data, ext, media
):
    # The client-supplied name (and its .html extension) plays no part in what is stored.
    resp = await _upload(client, data, "sleeve.html", media)
    assert resp.status_code == 303
    sleeve = await _only_sleeve(session)
    assert sleeve.image.endswith(ext) and "sleeve" not in sleeve.image

    served = await client.get(f"/sleeves/{sleeve.id}/image")
    assert served.status_code == 200
    assert served.headers["content-type"] == media
    assert served.headers["x-content-type-options"] == "nosniff"
    assert served.content == data


@pytest.mark.asyncio
async def test_sleeve_extension_follows_the_bytes_not_the_declared_type(client, session):
    assert (await _upload(client, GIF, "a.png", "image/png")).status_code == 303
    assert (await _only_sleeve(session)).image.endswith(".gif")


@pytest.mark.asyncio
async def test_sleeve_upload_is_size_capped(client, session):
    from src.routes import sleeves
    resp = await _upload(client, PNG + b"\x00" * sleeves._MAX_IMAGE_BYTES, "big.png", "image/png")
    assert resp.status_code == 413
    assert await _only_sleeve(session) is None


@pytest.mark.asyncio
async def test_a_previously_stored_non_image_sleeve_file_is_not_served(client, session):
    """Files uploaded before validation existed may be HTML; they must 404, not render."""
    directory = Path(get_settings().data_dir) / "sleeves"
    directory.mkdir(parents=True, exist_ok=True)
    name = f"{uuid.uuid4().hex}.html"
    (directory / name).write_bytes(HTML_DOC)
    sleeve = Sleeve(name="old", image=name)
    session.add(sleeve)
    await session.commit()
    try:
        resp = await client.get(f"/sleeves/{sleeve.id}/image")
        assert resp.status_code == 404
        assert b"<script>" not in resp.content
    finally:
        (directory / name).unlink()


@pytest.mark.asyncio
@pytest.mark.parametrize("name,body,served", [
    ("old.jpeg", b"\xff\xd8\xff\xe0" + b"0" * 32, "image/jpeg"),   # real image, old extension
    ("old.JPG", b"\xff\xd8\xff\xe0" + b"0" * 32, "image/jpeg"),
    ("fake.png", b"<html><script>alert(1)</script></html>", None),     # HTML under an image name
    ("old.svg", b"<svg onload='alert(1)' xmlns='http://www.w3.org/2000/svg'/>", None),
])
async def test_stored_sleeve_files_are_served_by_content_not_by_name(
    client, session, name, body, served
):
    directory = Path(get_settings().data_dir) / "sleeves"
    directory.mkdir(parents=True, exist_ok=True)
    stored = f"{uuid.uuid4().hex}-{name}"
    (directory / stored).write_bytes(body)
    sleeve = Sleeve(name="old", image=stored)
    session.add(sleeve)
    await session.commit()
    try:
        resp = await client.get(f"/sleeves/{sleeve.id}/image")
        if served is None:
            assert resp.status_code == 404
        else:
            assert resp.status_code == 200
            assert resp.headers["content-type"] == served
            assert resp.headers["x-content-type-options"] == "nosniff"
    finally:
        (directory / stored).unlink()


# --- 3. sleeve colour ----------------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize("color", [
    "red;background:url(//evil.example/x)", "#12345", "#gggggg", "red", "#000000;", "rgb(0,0,0)",
])
async def test_sleeve_colour_must_be_a_hex_colour(client, session, color):
    resp = await client.post("/sleeves/new", data={"name": "S", "color": color},
                             follow_redirects=False)
    assert resp.status_code == 400
    assert await _only_sleeve(session) is None


@pytest.mark.asyncio
async def test_sleeve_colour_from_the_picker_is_stored(client, session):
    resp = await client.post("/sleeves/new", data={"name": "S", "color": "#A1b2C3"},
                             follow_redirects=False)
    assert resp.status_code == 303
    assert (await _only_sleeve(session)).color == "#A1b2C3"
    assert "background-color: #A1b2C3" in (await client.get("/collection/sleeves")).text


@pytest.mark.asyncio
async def test_a_stored_non_colour_is_not_rendered_into_style(client, session):
    deck = await _deck(session)
    session.add(Sleeve(name="planted", color="red;x:y", deck_id=deck.id))  # column is 7 chars
    await session.commit()
    for url in ("/collection/sleeves", "/collection/decks", f"/decks/{deck.id}"):
        body = (await client.get(url)).text
        assert "red;x:y" not in body, url
        assert "background-color: #000000" in body, url


def test_colour_helpers():
    assert hex_color("#fff") == "#fff" and hex_color(" #A1b2C3 ") == "#A1b2C3"
    for bad in ("", None, "red", "#ffff", "#fffffff", "#fff;color:red", "#fff\nx", 7):
        assert hex_color(bad) is None, bad
    assert css_color("url(x)") == "#000000" and css_color(None, "#111111") == "#111111"


# --- 4. NL status is never a document -------------------------------------------------------------

@pytest.mark.asyncio
async def test_nl_status_returns_model_output_as_plain_text(client, session):
    done, pending = f"prompt-{uuid.uuid4().hex}", f"prompt-{uuid.uuid4().hex}"
    session.add_all([NLQCache(nl_text=done, q_text=MARKUP), NLQCache(nl_text=pending, q_text=None)])
    await session.commit()
    try:
        cases = [({"nl": done}, 200), ({"nl": pending}, 202), ({"nl": uuid.uuid4().hex}, 404),
                 ({}, 400)]
        for params, status in cases:
            resp = await client.get("/search/nl/status", params=params)
            assert resp.status_code == status
            assert resp.headers["content-type"].startswith("text/plain"), status
        # The poller reads responseText and puts it in the q parameter, so the text is unchanged.
        assert (await client.get("/search/nl/status", params={"nl": done})).text == MARKUP
    finally:
        await session.execute(text("DELETE FROM nl_q_cache WHERE nl_text IN (:a, :b)"),
                              {"a": done, "b": pending})
        await session.commit()


# --- 5. share links and other copy buttons --------------------------------------------------------

@pytest.mark.asyncio
async def test_a_crafted_shared_value_is_ignored_on_the_deck_and_binder_pages(client, session):
    deck = await _deck(session)
    binder = await _binder(session)
    for url in (f"/decks/{deck.id}", f"/binders/view/{binder.id}"):
        for payload in ("');alert(document.domain);//", "A" * 32, "a b", ""):
            body = (await client.get(url, params={"shared": payload})).text
            assert "Link created" not in body, (url, payload)
            assert "alert(document.domain)" not in body
            assert "writeText('" not in body


@pytest.mark.asyncio
async def test_a_real_minted_token_is_shown_with_a_working_copy_button(client, session):
    deck = await _deck(session)
    binder = await _binder(session)
    for kind, target, url in ((DECK, deck.id, f"/decks/{deck.id}"),
                              (BINDER, binder.id, f"/binders/view/{binder.id}")):
        _row, token = await create_share_link(session, kind, target)
        body = (await client.get(url, params={"shared": token})).text
        assert "Link created" in body
        assert _attr(body, "data-copy") == f"http://test/share/{token}"
        assert "navigator.clipboard.writeText(this.dataset.copy)" in body
        assert "writeText('" not in body


@pytest.mark.asyncio
async def test_just_minted_token_only_accepts_a_live_link_to_that_target(session):
    deck = await _deck(session)
    other = await create_deck(session, "Other", "4 Lightning Bolt")
    row, token = await create_share_link(session, DECK, deck.id)
    assert await just_minted_token(session, DECK, deck.id, token) == token
    assert await just_minted_token(session, DECK, other.id, token) is None
    assert await just_minted_token(session, BINDER, deck.id, token) is None
    assert await just_minted_token(session, DECK, deck.id, token + "'") is None
    await revoke_share_link(session, row.id)
    assert await just_minted_token(session, DECK, deck.id, token) is None


@pytest.mark.asyncio
async def test_pair_and_settings_copy_buttons_read_from_data_attributes(client):
    body = (await client.post("/pair", data={"label": "Phone"})).text
    assert "writeText('" not in body
    assert body.count("navigator.clipboard.writeText(this.dataset.copy)") == 2
    assert body.count('data-copy="') == 2

    body = (await client.post("/settings/devices", data={"label": "CLI", "scope": "read"})).text
    assert "writeText('" not in body
    token = _attr(body, "data-copy")
    assert len(token) >= 16 and "navigator.clipboard.writeText(this.dataset.copy)" in body


# --- 6. names inside confirm() -------------------------------------------------------------------

@pytest.mark.asyncio
async def test_box_name_is_data_for_the_delete_confirm_not_code(client, session):
    await _box(session, JS_BREAKOUT)
    body = (await client.get("/collection/locations")).text
    assert "confirm('Delete box" not in body
    assert 'onsubmit="return confirm(this.dataset.confirm)"' in body
    assert _attr(body, "data-confirm") == f"Delete box {JS_BREAKOUT}? Its cards become unfiled."
    # The row's click-through is the same pattern: the URL is data, the handler is constant.
    assert 'onclick="window.location.href = this.dataset.href"' in body
    assert _attr(body, "data-href").startswith("/search?q=loc%3A%22box%3A")


def test_no_template_interpolates_into_a_confirm_call():
    """Every ``confirm('…')`` in the templates must be a constant string."""
    offenders = []
    for path in Path("src/templates").rglob("*.html"):
        for m in re.finditer(r"confirm\((['\"])(.*?)\1\)", path.read_text(), re.S):
            if "{{" in m.group(2) or "{%" in m.group(2):
                offenders.append(f"{path}: {m.group(0)[:80]}")
    assert not offenders, offenders


# --- 7. sleeve deck picker -----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_sleeve_deck_picker_sets_its_label_as_text_from_a_data_attribute(client, session):
    await _deck(session, JS_BREAKOUT)
    session.add(Sleeve(name="S", color="#112233"))
    await session.commit()
    body = (await client.get("/collection/sleeves")).text
    assert "innerHTML" not in body
    assert "alert(document.domain)+&#39; ▾'" not in body
    assert _attr(body, "data-label") == f"{JS_BREAKOUT} ▾"
    assert "document.getElementById(this.dataset.btn).textContent = this.dataset.label" in body
    assert _attr(body, "data-btn", after="data-label").startswith("sleeve_selection_btn_")


# --- 8. rename responses and the inline-edit fragments -------------------------------------------

@pytest.mark.asyncio
async def test_rename_responses_are_inert_when_swapped_as_html(client, session):
    deck = await _deck(session)
    binder = await _binder(session)
    box = await _box(session)
    for url, model, pk in ((f"/decks/{deck.id}/rename", Deck, deck.id),
                           (f"/binders/{binder.id}/rename", Binder, binder.id),
                           (f"/box/{box.id}/rename", Box, box.id)):
        resp = await client.post(url, data={"name": MARKUP})
        assert resp.status_code == 200
        assert "<img" not in resp.text, url
        # Escaped exactly once: innerHTML turns it back into the name the user typed.
        assert html.unescape(resp.text) == MARKUP
        assert "&amp;lt;" not in resp.text
        assert (await _reload(session, model, pk)).name == MARKUP  # stored raw


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/edit-name", "/edit-description"])
@pytest.mark.parametrize("action", [
    "//evil.example/steal", "https://evil.example/steal", "javascript:alert(1)",
    "/\\evil.example", "decks/1/rename", "/decks/1/rename?x=1", "/a b", "",
])
async def test_edit_fragments_refuse_an_off_site_action(client, endpoint, action):
    resp = await client.post(
        endpoint, data={"placeholder": "n", "action": action, "target": "#deck-name"})
    assert resp.status_code in (400, 422)
    assert "evil.example" not in resp.text or resp.headers["content-type"].startswith(
        "application/json")
    assert "hx-post" not in resp.text


@pytest.mark.asyncio
@pytest.mark.parametrize("endpoint", ["/edit-name", "/edit-description"])
@pytest.mark.parametrize("target", ["body", "#a, body", "closest form", "#x\" onfocus=\"y", "#"])
async def test_edit_fragments_refuse_an_arbitrary_target(client, endpoint, target):
    resp = await client.post(
        endpoint, data={"placeholder": "n", "action": "/decks/1/rename", "target": target})
    assert resp.status_code == 400
    assert "hx-target" not in resp.text


@pytest.mark.asyncio
async def test_edit_fragments_still_work_for_the_real_callers(client):
    for endpoint, action, target in (
        ("/edit-name", "/decks/12/rename", "#deck-name"),
        ("/edit-name", "/binders/3/rename", "#binder-name"),
        ("/edit-name", "/box/7/rename", "#box-name-7"),
        ("/edit-description", "/decks/12/update-description", "#deck-description"),
    ):
        resp = await client.post(
            endpoint, data={"placeholder": 'a "b" <c>', "action": action, "target": target})
        assert resp.status_code == 200
        assert f'hx-post="{action}"' in resp.text and f'hx-target="{target}"' in resp.text
        assert "<c>" not in resp.text


def test_path_and_target_helpers():
    assert local_path("/decks/1/rename") == "/decks/1/rename"
    for bad in ("//x", "/a//b", "/a/../b?c", "http://x/", "/a#b", "/a\\b", None, "/a\n"):
        assert local_path(bad) is None, bad
    assert target_selector("#box-name-7") == "#box-name-7"
    for bad in ("box-name-7", "#7", "#a b", "#a,b", None, "#a\n"):
        assert target_selector(bad) is None, bad


# --- 9. hx-vals is valid JSON whatever the name contains ------------------------------------------

AWKWARD = 'He said "hi" \\ it\'s <b>'


@pytest.mark.asyncio
async def test_rename_buttons_carry_awkward_names_as_valid_json(client, session):
    deck = await _deck(session, AWKWARD)
    binder = await _binder(session, AWKWARD)
    box = await _box(session, AWKWARD)
    cases = (
        (f"/decks/{deck.id}", f"/decks/{deck.id}/rename", "#deck-name"),
        (f"/binders/view/{binder.id}", f"/binders/{binder.id}/rename", "#binder-name"),
        ("/collection/locations", f"/box/{box.id}/rename", f"#box-name-{box.id}"),
    )
    for url, action, target in cases:
        body = (await client.get(url)).text
        vals = json.loads(_attr(body, "hx-vals", after='hx-post="/edit-name"'))
        assert vals == {"placeholder": AWKWARD, "action": action, "target": target}, url
        assert "hx-vals='" not in body.split('hx-post="/edit-name"', 1)[1][:400]


@pytest.mark.asyncio
async def test_tag_remove_button_carries_an_awkward_tag_as_valid_json(client, session):
    card = await _card(session, "Tagged", tags=[AWKWARD])
    body = (await client.get(f"/card/{card.scryfall_id}")).text
    vals = json.loads(_attr(body, "hx-vals", after='title="Remove tag"'))
    assert vals["tag"] == AWKWARD
    assert vals["bulk_action"] == "tag-remove" and vals["refresh"] is True
    assert isinstance(vals["stack_ids"], int)


# --- 10. deck source URL -------------------------------------------------------------------------

HOSTILE_URLS = [
    "javascript:alert(document.domain)//moxfield.com/decks/a",
    "file:///x/moxfield.com/decks/a",
    "data:text/html,<script>alert(1)</script>//archidekt.com/decks/1",
    "https://evil.example/moxfield.com/decks/a",
    "https://moxfield.com.evil.example/decks/a",
    "https://evil.example/?u=tappedout.net/mtg-decks/a",
    " javascript:alert(1)//moxfield.com/decks/a",
    "ftp://moxfield.com/decks/a",
]


@pytest.mark.parametrize("url", HOSTILE_URLS)
def test_detect_host_is_anchored(url):
    assert detect_host(url) is None
    assert detect_host(url.strip()) is None
    assert normalize_deck_url(url) is None


def test_normalize_deck_url_yields_https_for_supported_hosts():
    assert normalize_deck_url(" https://moxfield.com/decks/abc ") == "https://moxfield.com/decks/abc"
    assert normalize_deck_url("http://www.moxfield.com/decks/abc") == \
        "https://www.moxfield.com/decks/abc"
    assert normalize_deck_url("archidekt.com/decks/42/elf") == "https://archidekt.com/decks/42/elf"
    assert normalize_deck_url("HTTPS://TappedOut.net/mtg-decks/x/") == \
        "https://TappedOut.net/mtg-decks/x/"
    assert normalize_deck_url("https://moxfield.com/decks/a b") is None
    assert normalize_deck_url("") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("url", HOSTILE_URLS[:3])
async def test_resync_never_stores_an_invalid_source_url(client, session, monkeypatch, url):
    async def must_not_fetch(_url):  # pragma: no cover - reaching it is the failure
        raise AssertionError("fetched an unvalidated URL")
    monkeypatch.setattr(decks_route, "fetch_deck_from_url", must_not_fetch)
    deck_id = (await _deck(session)).id
    resp = await client.post(f"/decks/{deck_id}/resync", data={"url": url},
                             follow_redirects=False)
    assert resp.status_code == 400
    assert (await _reload(session, Deck, deck_id)).source_url is None


@pytest.mark.asyncio
async def test_resync_keeps_a_valid_url_whose_fetch_fails(client, session, monkeypatch):
    """Unchanged behaviour: a *valid* source survives a provider outage; a retry needs no input."""
    async def fail(_url):
        raise decks_route.DeckImportError("nope")
    monkeypatch.setattr(decks_route, "fetch_deck_from_url", fail)
    deck_id = (await _deck(session)).id
    resp = await client.post(f"/decks/{deck_id}/resync",
                             data={"url": "http://moxfield.com/decks/gone"},
                             follow_redirects=False)
    assert resp.status_code == 502
    assert (await _reload(session, Deck, deck_id)).source_url == "https://moxfield.com/decks/gone"


@pytest.mark.asyncio
async def test_resync_stores_the_normalised_https_url(client, session, monkeypatch):
    seen = []

    async def fake(url):
        seen.append(url)
        return "Burn", "4 Lightning Bolt"
    monkeypatch.setattr(decks_route, "fetch_deck_from_url", fake)
    deck_id = (await _deck(session)).id
    resp = await client.post(f"/decks/{deck_id}/resync",
                             data={"url": "http://moxfield.com/decks/y"}, follow_redirects=False)
    assert resp.status_code == 303
    assert seen == ["https://moxfield.com/decks/y"]
    assert (await _reload(session, Deck, deck_id)).source_url == "https://moxfield.com/decks/y"


@pytest.mark.asyncio
async def test_import_url_never_records_an_invalid_source(client, session, monkeypatch):
    async def fake(_url):  # stands in for a fetcher that was (wrongly) persuaded to accept it
        return "Imported", "4 Lightning Bolt"
    monkeypatch.setattr(decks_route, "fetch_deck_from_url", fake)
    await _card(session)
    resp = await client.post(
        "/decks/import-url", data={"url": HOSTILE_URLS[0], "ownership": "unowned"},
        follow_redirects=False)
    assert resp.status_code == 303
    deck = (await session.execute(select(Deck))).scalars().one()
    assert deck.source_url is None


@pytest.mark.asyncio
@pytest.mark.parametrize("stored", ["javascript:alert(document.domain)", "file:///etc/passwd",
                                    "//evil.example/x", "data:text/html,x"])
async def test_a_stored_non_http_source_is_never_rendered_as_a_link(client, session, stored):
    deck = await _deck(session)
    deck.source_url = stored  # e.g. written before validation existed, or restored from a backup
    await session.commit()
    body = (await client.get(f"/decks/{deck.id}")).text
    assert f'href="{html.escape(stored)}"' not in body
    assert "Source:</span>" not in body
    # The deck falls back to the "link a source" form, so it can be repaired from the UI.
    assert 'id="resync-url"' in body


@pytest.mark.asyncio
async def test_a_stored_https_source_is_still_linked(client, session):
    deck = await _deck(session)
    deck.source_url = "https://moxfield.com/decks/abc"
    await session.commit()
    body = (await client.get(f"/decks/{deck.id}")).text
    assert 'href="https://moxfield.com/decks/abc"' in body and "Source:</span>" in body


def test_http_url_helper():
    assert http_url("https://a.example/x") == "https://a.example/x"
    assert http_url("HTTP://a.example") == "HTTP://a.example"
    for bad in ("javascript:alert(1)", "//a.example", "https:///x", "/x", "", None, "http://[x"):
        assert http_url(bad) is None, bad


# --- 12. deck description is escaped once, at render ---------------------------------------------

DESCRIPTION = "Tom & Jerry's <b>\"best\"</b> deck"


@pytest.mark.asyncio
async def test_description_is_stored_raw_and_escaped_once(client, session):
    deck_id = (await _deck(session)).id
    resp = await client.post(f"/decks/{deck_id}/update-description",
                             data={"description": DESCRIPTION})
    assert resp.status_code == 200
    # The fragment htmx swaps in is escaped (once), so it displays exactly what was typed…
    assert "<b>" not in resp.text and html.unescape(resp.text) == DESCRIPTION
    assert "&amp;amp;" not in resp.text and "&amp;lt;" not in resp.text
    # …the row holds the text itself…
    assert (await _reload(session, Deck, deck_id)).description == DESCRIPTION
    # …and a full page load shows the same thing, not a double-escaped version.
    body = (await client.get(f"/decks/{deck_id}")).text
    shown = body.split('id="deck-description">', 1)[1].split("</p>", 1)[0]
    assert html.unescape(shown) == DESCRIPTION
    assert "&amp;amp;" not in shown and "<b>" not in shown


@pytest.mark.asyncio
async def test_clearing_the_description_returns_an_empty_fragment(client, session):
    deck = await _deck(session)
    deck.description = "x"
    deck_id = deck.id
    await session.commit()
    resp = await client.post(f"/decks/{deck_id}/update-description", data={"description": ""})
    assert resp.status_code == 200 and resp.text == ""
    assert (await _reload(session, Deck, deck_id)).description is None


def _migration():
    path = Path("alembic/versions/0048_unescape_deck_descriptions.py")
    spec = importlib.util.spec_from_file_location("mig_0048", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_migration_unescapes_previously_escaped_descriptions(session):
    mig = _migration()
    assert mig.revision == "0048_unescape_deck_descriptions"
    assert mig.down_revision == "0047_repair_fork_databases"
    originals = [DESCRIPTION, "plain text", "a &lt; b written literally, then escaped", "1 < 2 > 0"]
    ids = []
    for i, original in enumerate(originals):
        deck = Deck(name=f"d{i}", description=html.escape(original))
        session.add(deck)
        await session.flush()
        ids.append(deck.id)
    untouched = Deck(name="none", description=None)
    session.add(untouched)
    await session.flush()
    untouched_id = untouched.id
    await session.commit()

    await session.execute(text(mig.UNESCAPE_SQL))
    await session.commit()
    for deck_id, original in zip(ids, originals, strict=True):
        assert (await _reload(session, Deck, deck_id)).description == original
    assert (await _reload(session, Deck, untouched_id)).description is None
    # Idempotent on rows that are already plain.
    await session.execute(text(mig.UNESCAPE_SQL))
    await session.commit()
    assert (await _reload(session, Deck, ids[1])).description == "plain text"
    assert (await _reload(session, Deck, ids[3])).description == "1 < 2 > 0"
