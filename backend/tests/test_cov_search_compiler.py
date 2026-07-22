"""Coverage for src/search/compiler.py — field handlers, exercised through run_search
against a seeded card set (plus a couple direct-call edge cases the parser can't produce)."""

import uuid

import pytest
import pytest_asyncio
from src.models import Card, CollectionCard
from src.scryfall.mapping import card_to_columns
from src.search import SearchError, SearchScope
from src.search.ast import Term
from src.search.compiler import compile_term
from src.search.engine import run_search

# ids for cards we attach collection stacks to
BOLT = str(uuid.uuid4())
NIV = str(uuid.uuid4())

_RAW = [
    {"id": str(uuid.uuid4()), "name": "Black Lotus", "set": "LEA", "collector_number": "232",
     "rarity": "rare", "cmc": 0, "type_line": "Artifact", "colors": [], "color_identity": [],
     "mana_cost": "{0}", "oracle_text": "Add three mana.", "released_at": "1993-08-05",
     "lang": "en", "layout": "normal", "artist": "Christopher Rush", "watermark": "set",
     "border_color": "black", "frame": "1993", "set_type": "core", "security_stamp": "oval",
     "games": ["paper"], "prices": {"usd": "9999.99"}, "promo": True, "game_changer": True,
     "legalities": {"vintage": "restricted"}},
    {"id": BOLT, "name": "Lightning Bolt", "set": "MH2", "collector_number": "122",
     "rarity": "uncommon", "cmc": 1, "type_line": "Instant", "colors": ["R"],
     "color_identity": ["R"], "mana_cost": "{R}", "oracle_text": "Deal 3 damage.",
     "released_at": "2021-06-18", "lang": "en", "layout": "normal", "artist": "Christopher Moeller",
     "games": ["paper", "mtgo"], "prices": {"usd": "2.50"},
     "legalities": {"modern": "legal"}},
    {"id": NIV, "name": "Niv-Mizzet, Parun", "set": "GRN", "collector_number": "192",
     "rarity": "mythic", "cmc": 6, "type_line": "Legendary Creature — Dragon Wizard",
     "colors": ["U", "R"], "color_identity": ["U", "R"], "mana_cost": "{U}{U}{R}{R}",
     "power": "5", "toughness": "5", "oracle_text": "Whenever a player draws a card...",
     "released_at": "2018-10-05", "lang": "en", "layout": "normal", "prices": {"usd": "5.00"},
     "legalities": {"modern": "legal"}},
    {"id": str(uuid.uuid4()), "name": "Llanowar Elves", "set": "M19", "collector_number": "314",
     "rarity": "common", "cmc": 1, "type_line": "Creature — Elf Druid", "colors": ["G"],
     "color_identity": ["G"], "mana_cost": "{G}", "power": "1", "toughness": "1",
     "oracle_text": "{T}: Add {G}.", "released_at": "2018-07-13", "lang": "ja",
     "layout": "normal", "prices": {"usd": "0.20"}},
    {"id": str(uuid.uuid4()), "name": "Nissa, Who Shakes the World", "set": "WAR",
     "collector_number": "169", "rarity": "mythic", "cmc": 5,
     "type_line": "Legendary Planeswalker — Nissa", "colors": ["G"], "color_identity": ["G"],
     "mana_cost": "{3}{G}{G}", "loyalty": "5", "oracle_text": "Whenever a Forest...",
     "released_at": "2019-05-03", "lang": "en", "layout": "normal", "prices": {}},
    {"id": str(uuid.uuid4()), "name": "Fire // Ice", "set": "APC", "collector_number": "128",
     "rarity": "uncommon", "cmc": 2, "type_line": "Instant // Instant", "colors": ["R", "U"],
     "color_identity": ["R", "U"], "layout": "split", "oracle_text": "Fire ... // Ice ...",
     "released_at": "2001-06-04", "lang": "en", "prices": {"usd": "1.00"}},
]


async def _names(session, query, scope=SearchScope.ALL):
    result = await run_search(session, query, scope=scope, page_size=100)
    return {c.name for c in result.cards}


@pytest_asyncio.fixture
async def seeded(session):
    for raw in _RAW:
        session.add(Card(**card_to_columns(raw)))
    # Owned stacks: a foil Bolt with a tag + location; a graded Niv.
    session.add(CollectionCard(scryfall_id=BOLT, quantity=1, finish="foil",
                               tags=["for-trade"], location="Box A"))
    session.add(CollectionCard(scryfall_id=NIV, quantity=1, finish="normal",
                               grade_company="PSA", grade="10"))
    await session.commit()
    return _RAW


# --- raw-JSON string handlers ---------------------------------------------

@pytest.mark.asyncio
async def test_artist_and_watermark(seeded, session):
    assert await _names(session, "a:rush") == {"Black Lotus"}
    assert await _names(session, "wm:set") == {"Black Lotus"}


@pytest.mark.asyncio
async def test_border_frame_settype_stamp(seeded, session):
    assert await _names(session, "border:black") == {"Black Lotus"}
    assert await _names(session, "frame:1993") == {"Black Lotus"}
    assert await _names(session, "st:core") == {"Black Lotus"}
    assert await _names(session, "stamp:oval") == {"Black Lotus"}


@pytest.mark.asyncio
async def test_game_and_boolean_flag(seeded, session):
    assert await _names(session, "game:mtgo") == {"Lightning Bolt"}
    assert await _names(session, "is:promo") == {"Black Lotus"}
    # is:gamechanger is aliased to Scryfall's raw ``game_changer`` flag (#159).
    assert await _names(session, "is:gamechanger") == {"Black Lotus"}
    assert await _names(session, "is:game-changer") == {"Black Lotus"}


# --- mana / colors ---------------------------------------------------------

@pytest.mark.asyncio
async def test_mana_symbols(seeded, session):
    assert await _names(session, "m:{U}{R}") == {"Niv-Mizzet, Parun"}
    assert await _names(session, "m:R") == {"Lightning Bolt", "Niv-Mizzet, Parun"}


@pytest.mark.asyncio
async def test_color_operator_branches(seeded, session):
    assert await _names(session, "c=r") == {"Lightning Bolt"}
    assert "Lightning Bolt" not in await _names(session, "c!=r")
    # <= : subset of {R} -> colorless + mono-red
    assert await _names(session, "c<=r") == {"Black Lotus", "Lightning Bolt"}
    # > : contains R and more than 1 color
    assert await _names(session, "c>r") == {"Niv-Mizzet, Parun", "Fire // Ice"}
    # < : proper subset of {W,U,R}
    assert "Lightning Bolt" in await _names(session, "c<wur")
    assert await _names(session, "c:c") == {"Black Lotus"}  # colorless special
    assert "Niv-Mizzet, Parun" in await _names(session, "c:m")  # multicolor special


@pytest.mark.asyncio
async def test_bad_color_raises(seeded, session):
    with pytest.raises(SearchError):
        await run_search(session, "c:xyz", scope=SearchScope.ALL)


# --- numeric / rarity / set fields ----------------------------------------

@pytest.mark.asyncio
async def test_loyalty(seeded, session):
    assert await _names(session, "loy>=5") == {"Nissa, Who Shakes the World"}


@pytest.mark.asyncio
async def test_rarity_variants(seeded, session):
    with pytest.raises(SearchError):
        await run_search(session, "r:foobar", scope=SearchScope.ALL)
    assert "Llanowar Elves" not in await _names(session, "r!=common")


@pytest.mark.asyncio
async def test_cn_lang_layout(seeded, session):
    assert "Black Lotus" not in await _names(session, "cn!=232")
    assert await _names(session, "lang:ja") == {"Llanowar Elves"}
    assert await _names(session, "layout:split") == {"Fire // Ice"}


# --- collection-backed handlers -------------------------------------------

@pytest.mark.asyncio
async def test_tag_location_and_owned_finish(seeded, session):
    # An etched copy of Niv, to check the foil/etched relationship.
    session.add(CollectionCard(scryfall_id=NIV, quantity=1, finish="etched"))
    await session.commit()
    assert await _names(session, "tag:for-trade") == {"Lightning Bolt"}
    assert await _names(session, "loc:box") == {"Lightning Bolt"}
    # Etched is a kind of foil: is:foil includes etched copies, is:etched stays strict.
    assert await _names(session, "is:foil") == {"Lightning Bolt", "Niv-Mizzet, Parun"}
    assert await _names(session, "is:etched") == {"Niv-Mizzet, Parun"}


@pytest.mark.asyncio
async def test_is_layout_and_graded(seeded, session):
    assert await _names(session, "is:split") == {"Fire // Ice"}
    assert await _names(session, "is:graded") == {"Niv-Mizzet, Parun"}


# --- year / date -----------------------------------------------------------

@pytest.mark.asyncio
async def test_date_filter_and_errors(seeded, session):
    assert await _names(session, "date>=2020-01-01") == {"Lightning Bolt"}
    with pytest.raises(SearchError):
        await run_search(session, "date:not-a-date", scope=SearchScope.ALL)
    with pytest.raises(SearchError):
        await run_search(session, "year:abcd", scope=SearchScope.ALL)


# --- text fields, numeric fields, boolean combination ---------------------

@pytest.mark.asyncio
async def test_name_oracle_type_and_regex(seeded, session):
    assert await _names(session, "goblin OR lotus") == {"Black Lotus"}  # bare name
    assert "Niv-Mizzet, Parun" in await _names(session, "o:draws")
    assert await _names(session, "t:planeswalker") == {"Nissa, Who Shakes the World"}
    assert "Black Lotus" in await _names(session, "o:/add three/")  # oracle regex


@pytest.mark.asyncio
async def test_identity_power_toughness(seeded, session):
    assert "Lightning Bolt" in await _names(session, "id:r")
    assert await _names(session, "pow>=5") == {"Niv-Mizzet, Parun"}
    assert await _names(session, "tou<=1") == {"Llanowar Elves"}


@pytest.mark.asyncio
async def test_color_ge_and_exact_colon(seeded, session):
    assert await _names(session, "c>=r") == {
        "Lightning Bolt", "Niv-Mizzet, Parun", "Fire // Ice"}


@pytest.mark.asyncio
async def test_rarity_set_keyword_format(seeded, session):
    assert await _names(session, "r:mythic") == {
        "Niv-Mizzet, Parun", "Nissa, Who Shakes the World"}
    assert "Black Lotus" in await _names(session, "r>=rare")
    assert await _names(session, "s:lea") == {"Black Lotus"}
    assert await _names(session, "kw:flying") == set()  # handler runs, no matches
    assert "Lightning Bolt" in await _names(session, "f:modern")


@pytest.mark.asyncio
async def test_price_year_and_bad_number(seeded, session):
    assert await _names(session, "usd>=9000") == {"Black Lotus"}
    assert await _names(session, "year>=2020") == {"Lightning Bolt"}
    with pytest.raises(SearchError):
        await run_search(session, "mv>=abc", scope=SearchScope.ALL)


@pytest.mark.asyncio
async def test_boolean_and_or_not(seeded, session):
    assert await _names(session, "t:instant OR t:artifact") == {
        "Black Lotus", "Lightning Bolt", "Fire // Ice"}
    assert await _names(session, "c:r t:creature") == {"Niv-Mizzet, Parun"}
    assert "Lightning Bolt" not in await _names(session, "-t:instant")


# --- compile_term guards (not reachable via the parser) --------------------

def test_regex_on_non_text_field_rejected():
    # "c:/r/" parses to a regex Term on the color field -> compile_term rejects it.
    with pytest.raises(SearchError):
        compile_term(Term(field="color", op="~", value="r", regex=True))


def test_comparator_rejects_unknown_operator():
    # A numeric field with a non-comparison operator hits _comparator's guard.
    with pytest.raises(SearchError):
        compile_term(Term(field="mv", op="~", value="3", regex=False))
