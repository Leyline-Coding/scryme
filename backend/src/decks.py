"""Deck parsing, card resolution, and ownership coverage.

`parse_decklist` reads a plain decklist (``4 Lightning Bolt``, optional ``(SET) NUM`` suffix,
``Sideboard`` marker / ``SB:`` prefix). `create_deck` resolves each line to a representative
printing + oracle id. `deck_coverage` compares the deck against the owned collection by oracle id
(any printing you own counts) to answer "what am I missing".
"""

from __future__ import annotations

import datetime
import re
import uuid
import copy
from dataclasses import dataclass, field

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from collections import OrderedDict

from src.collection_edit import move_stack_location, move_qty_from_existing_stack
from src.currency import unit_price
from src.models import Card, CollectionCard, Deck, DeckCard, Location
from src.pricing import resolve_prices
from src.stats import Bar, _bars, _color_bucket, _primary_type

# These patterns are written so each has exactly one way to match, which is what keeps them
# linear: no `$` anchor (in Python `$` also matches *before* a trailing newline, so a `\s*$` tail
# has two valid end positions and backtracks over trailing whitespace — `\Z` is unambiguous), and
# no two adjacent constructs that accept the same character. Trailing whitespace is removed with
# rstrip() in code rather than absorbed by the pattern.
#
# A decklist line: an optional count, an optional "x" marker, then the name. `\s+` is followed by
# `\S` so the whitespace run has a single end point; "2x Bolt" and "2 Bolt" are both accepted,
# "2 x Bolt" is not (no exporter emits it).
_LINE = re.compile(r"^\s*(\d+)[xX]?\s+(\S.*)")
# A trailing "(SET) 123" / "(SET)" printing hint — captured so the exact printing is honoured.
# `name` is already rstripped, so the pattern needs no trailing whitespace of its own.
_SET_SUFFIX = re.compile(r"\(([A-Za-z0-9]{2,6})\)\s*([A-Za-z0-9-]*)\Z")
# Export finish markers -> the finish they mean.
_FINISH_MARKERS = {"f": "foil", "foil": "foil", "e": "etched", "etched": "etched"}


@dataclass
class ParsedLine:
    quantity: int
    name: str
    board: str  # main | side
    # Printing hints from the line, when the export carried them (#import fidelity):
    set_code: str = ""
    collector_number: str = ""
    finish: str = "normal"  # normal | foil | etched


async def all_decks(session: AsyncSession) -> list[Deck]:
    return list((await session.execute(select(Deck).order_by(Deck.name))).scalars().all())


async def rename_deck(session: AsyncSession, deck: Deck, name: str = '') -> str:
    if deck and name.strip():
        deck.name = name.strip()[:128]
        await session.commit()
    return deck.name


def _peel_marker(text: str) -> tuple[str, str] | None:
    """Peel one trailing ``*F*``-style export marker off ``text``.

    Returns ``(marker_body, remainder)``, or None when ``text`` doesn't end in a marker. Done with
    string operations rather than a pattern so the scan is unambiguously linear.
    """
    if not text.endswith("*"):
        return None
    start = text.rfind("*", 0, len(text) - 1)
    if start < 0:
        return None
    return text[start + 1 : -1], text[:start].rstrip()


def _parse_deck_line(s: str, board: str) -> ParsedLine | None:
    """Parse one decklist line, keeping its printing hint and finish (or None if unparseable).

    Understands the common export shape ``2 Lightning Bolt (MH2) 122 *F*`` — the set code and
    collector number identify the exact printing, and ``*F*``/``*E*`` the finish.
    """
    sb = False
    if s.lower().startswith("sb:"):
        sb, s = True, s[3:].strip()
    m = _LINE.match(s)
    if not m:
        return None
    name = m.group(2).rstrip()
    finish = "normal"
    while (mark := _peel_marker(name)) is not None:
        body, name = mark
        finish = _FINISH_MARKERS.get(body.strip().lower(), finish)
    set_code = collector_number = ""
    hint = _SET_SUFFIX.search(name)
    if hint:
        set_code, collector_number = hint.group(1), hint.group(2)
        name = name[: hint.start()]
    name = name.strip()
    if not name:
        return None
    return ParsedLine(int(m.group(1)), name, "side" if sb else board,
                      set_code=set_code, collector_number=collector_number, finish=finish)


def parse_decklist(text: str | None) -> list[ParsedLine]:
    out: list[ParsedLine] = []
    board = "main"
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if re.search(r"^.{0,20}sideboard", s, re.IGNORECASE):
            board = "side"
            continue
        if s.startswith(("#", "//")):
            continue
        line = _parse_deck_line(s, board)
        if line:
            out.append(line)
    return out


def _merge_lines(parsed: list[ParsedLine]) -> list[ParsedLine]:
    """Combine identical lines, keeping distinct printings/finishes as separate lines.

    Two lines only merge when their card, board, printing hint *and* finish match — so a deck
    running the same card in two printings (or foil + nonfoil) keeps both.
    """
    merged: dict[tuple, ParsedLine] = {}
    order: list[tuple] = []
    for p in parsed:
        key = (p.name.lower(), p.board, p.set_code.lower(), p.collector_number, p.finish)
        if key in merged:
            merged[key].quantity += p.quantity
        else:
            merged[key] = ParsedLine(p.quantity, p.name, p.board, p.set_code,
                                     p.collector_number, p.finish)
            order.append(key)
    return [merged[k] for k in order]


async def _owned_by_oracle(session: AsyncSession) -> dict:
    rows = await session.execute(
        select(Card.oracle_id, func.sum(CollectionCard.quantity))
        .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
        .group_by(Card.oracle_id)
    )
    return {o: int(q) for o, q in rows.all() if o}


def _is_playable(legalities: dict | None) -> bool:
    """True for a real, tournament-usable printing.

    Scryfall marks non-playable variants (art-series, tokens, gold-bordered World Championship /
    Collector's Edition, oversized, acorn un-cards) as ``not_legal`` in *every* format, so a
    printing counts as playable when it is legal/restricted/banned in at least one format.
    """
    return bool(legalities) and any(v != "not_legal" for v in legalities.values())


async def _resolve_names(session: AsyncSession, names: list[str], owned_sids: set) -> dict:
    """Map each lowercased name -> (oracle_id, scryfall_id).

    Prefers a printing the user owns (collection alignment), then a tournament-legal printing, then
    the newest — so a deck line never silently resolves to an art-series / oversized variant that
    Scryfall reports as illegal in every format.
    """
    wanted = {n.lower() for n in names}
    if not wanted:
        return {}
    rows = (
        await session.execute(
            select(
                Card.name, Card.oracle_id, Card.scryfall_id, Card.released_at, Card.legalities
            ).where(func.lower(Card.name).in_(wanted))
        )
    ).all()
    by_name: dict[str, list] = {}
    for name, oracle, sid, released, legalities in rows:
        by_name.setdefault(name.lower(), []).append((oracle, sid, released, legalities))

    resolved: dict[str, tuple] = {}
    for low, cands in by_name.items():
        # Owned first (nicer image/price + collection alignment), then playable, then newest.
        cands.sort(
            key=lambda c: (c[1] in owned_sids, _is_playable(c[3]), c[2] or datetime.date.min),
            reverse=True,
        )
        resolved[low] = (cands[0][0], cands[0][1])

    # Fallback: match the front face of split / double-faced cards ("Name // Other"). Prefer a
    # playable printing so we don't land on an art-series card, which is also named "Name // Name".
    for low in wanted - set(resolved):
        cands = (
            await session.execute(
                select(Card.oracle_id, Card.scryfall_id, Card.released_at, Card.legalities)
                .where(func.lower(Card.name).like(low + " //%"))
                .order_by(Card.released_at.desc().nulls_last())
            )
        ).all()
        if cands:
            best = max(cands, key=lambda c: (_is_playable(c[3]), c[2] or datetime.date.min))
            resolved[low] = (best[0], best[1])
    return resolved


async def _resolve_exact(session: AsyncSession, pairs: set[tuple[str, str]]) -> dict:
    """(lowercased set code, collector number) -> (oracle_id, scryfall_id) for exact printings.

    Lets an import honour the printing the source actually specified instead of re-picking one by
    name — the difference between a deck's real value and a base-printing approximation.
    """
    wanted = {(s.lower(), cn) for s, cn in pairs if s and cn}
    if not wanted:
        return {}
    rows = (await session.execute(
        select(Card.set_code, Card.collector_number, Card.oracle_id, Card.scryfall_id)
        .where(tuple_(func.lower(Card.set_code), Card.collector_number).in_(list(wanted)))
    )).all()
    return {(sc.lower(), cn): (oracle, sid) for sc, cn, oracle, sid in rows}


def _printing_for(line: ParsedLine, exact: dict, by_name: dict) -> tuple:
    """The printing a line asked for: its exact (SET) NUM if we have it, else the by-name pick."""
    if line.set_code and line.collector_number:
        hit = exact.get((line.set_code.lower(), line.collector_number))
        if hit:
            return hit
    return by_name.get(line.name.lower(), (None, None))


async def resync_printings(session: AsyncSession, deck: Deck, decklist_text: str) -> int:
    """Correct an existing deck's printings + finishes from a freshly-fetched decklist.

    Matches each source line to a deck line by name + board (each deck line is used once) and
    updates only its printing and finish — quantities, extra lines and the deck itself are left
    alone, so a deck imported before printings were honoured can be repaired in place. Returns how
    many lines changed.
    """
    lines = _merge_lines(parse_decklist(decklist_text))
    owned_sids = set(await session.scalars(select(CollectionCard.scryfall_id)))
    resolved = await _resolve_names(session, [x.name for x in lines], owned_sids)
    exact = await _resolve_exact(session, {(x.set_code, x.collector_number) for x in lines})

    remaining: dict[tuple, list[DeckCard]] = {}
    for dc in deck.cards:
        remaining.setdefault((dc.name.lower(), dc.board), []).append(dc)

    changed = 0
    for line in lines:
        bucket = remaining.get((line.name.lower(), line.board))
        if not bucket:
            continue
        dc = bucket.pop(0)
        oracle, sid = _printing_for(line, exact, resolved)
        if sid is None:
            continue
        if dc.scryfall_id != sid or dc.finish != line.finish:
            dc.oracle_id, dc.scryfall_id, dc.finish = oracle, sid, line.finish
            changed += 1
    if changed:
        await session.commit()
    return changed


async def add_card_to_deck(session: AsyncSession, deck_id: int, card: Card) -> bool:
    """Add one copy of ``card`` to a deck's main board (bumps quantity if already present).

    Used by the unified location picker (#160): filing a stack "into a deck" adds it to the
    decklist. Returns False if the deck is missing.
    """
    deck = await session.get(Deck, deck_id)
    if deck is None:
        return False
    # On an owned deck, a card filed in is owned too (its later edits sync to the collection, #298).
    owned = deck.ownership in ("full", "partial")
    existing = next(
        (dc for dc in deck.cards if dc.board == "main"
         and dc.name.lower() == card.name.lower()), None
    )
    if existing:
        existing.quantity += 1
    else:
        deck.cards.append(DeckCard(
            name=card.name, quantity=1, board="main",
            oracle_id=card.oracle_id, scryfall_id=card.scryfall_id, owned=owned,
        ))
    await session.commit()
    return True


@dataclass
class OwnershipRow:
    name: str
    quantity: int              # summed across boards (ownership is shared)
    scryfall_id: str | None    # representative printing, or None when unmatched
    matched: bool
    finish: str = "normal"     # the finish the deck runs, so owned copies land as foil/etched


async def resolve_ownership_rows(session: AsyncSession, decklist_text: str) -> list[OwnershipRow]:
    """One row per unique card in a decklist (quantities summed across boards), each resolved to a
    representative printing — backs the import "which of these do you own?" checklist."""
    merged: dict[tuple, ParsedLine] = {}
    order: list[tuple] = []
    for p in parse_decklist(decklist_text):
        # Keep distinct printings apart so ownership lands on the printing the deck actually runs.
        key = (p.name.lower(), p.set_code.lower(), p.collector_number)
        if key in merged:
            merged[key].quantity += p.quantity
        else:
            merged[key] = ParsedLine(p.quantity, p.name, "main", p.set_code,
                                     p.collector_number, p.finish)
            order.append(key)
    lines = [merged[k] for k in order]

    owned_sids = set(await session.scalars(select(CollectionCard.scryfall_id)))
    resolved = await _resolve_names(session, [line.name for line in lines], owned_sids)
    exact = await _resolve_exact(session, {(x.set_code, x.collector_number) for x in lines})
    rows: list[OwnershipRow] = []
    for line in lines:
        _oracle, sid = _printing_for(line, exact, resolved)
        rows.append(OwnershipRow(line.name, line.quantity,
                                 str(sid) if sid else None, sid is not None, line.finish))
    return rows


async def create_deck(session: AsyncSession, name: str, decklist_text: str,
                      source_url: str = "") -> Deck:
    parsed = _merge_lines(parse_decklist(decklist_text))
    owned_sids = set(await session.scalars(select(CollectionCard.scryfall_id)))
    resolved = await _resolve_names(session, [p.name for p in parsed], owned_sids)
    exact = await _resolve_exact(session, {(p.set_code, p.collector_number) for p in parsed})

    deck = Deck(name=(name or "").strip()[:256] or "Untitled deck",
                source_url=(source_url or None))
    for p in parsed:
        oracle, sid = _printing_for(p, exact, resolved)
        deck.cards.append(
            DeckCard(name=p.name, quantity=p.quantity, board=p.board,
                     oracle_id=oracle, scryfall_id=sid, finish=p.finish)
        )
    session.add(deck)
    await session.commit()
    await session.refresh(deck)
    return deck


# Formats offered for the deck legality check (Scryfall reports ~20; this is the useful subset).
LEGALITY_FORMATS = [
    "standard", "pioneer", "modern", "legacy", "vintage",
    "commander", "pauper", "brawl", "historic", "oathbreaker",
]
# A card is allowed in a deck when legal (restricted = legal but limited to one copy).
_ALLOWED_LEGALITIES = {"legal", "restricted"}

# Languages a physical printing can be in (Scryfall codes). English is the default; the card DB is
# English-only per printing, so a deck line just records the language it's played in.
DECK_LANGUAGES = [
    ("en", "English"), ("es", "Spanish"), ("fr", "French"), ("de", "German"),
    ("it", "Italian"), ("pt", "Portuguese"), ("ja", "Japanese"), ("ko", "Korean"),
    ("ru", "Russian"), ("zhs", "Chinese (S)"), ("zht", "Chinese (T)"), ("ph", "Phyrexian"),
]
_LANGUAGE_CODES = {code for code, _ in DECK_LANGUAGES}


def normalize_language(code: str | None) -> str:
    """Clamp a language code to one we offer, defaulting to English."""
    code = (code or "").strip().lower()
    return code if code in _LANGUAGE_CODES else "en"


@dataclass
class CardRow:
    name: str                       # Although scryfall cards have names, they are not guaranteed. A user could submit any name... so we track DeckCard.name independantly
    scryfall_card: Card | None      # Inherit the attributes of a scryfall card
    board: str
    quantity: int
    stack_coverage: StackCoverage
    matched: bool
    legality: str | None = None     # status in the selected format, or None when no format chosen
    card_id: int = 0                # deck_card row id (for editing the printing)
    proxy: bool = False             # printed proxy
    special: bool = False           # art card / alter / other genuine non-standard copy
    language: str = "en"            # language the copy is played in (Scryfall code)
    finish: str = "normal"          # normal | foil | etched — drives this line's price


async def _legalities_by_oracle(session: AsyncSession, oracles: set) -> dict:
    """Legalities per oracle id, taken from a *playable* printing.

    Legality is a property of the card, not of whichever printing a deck line resolved to, so we
    ignore non-playable variants (all ``not_legal``) when a real printing of the same card exists.
    """
    ids = [o for o in oracles if o]
    if not ids:
        return {}
    rows = (
        await session.execute(
            select(Card.oracle_id, Card.legalities).where(Card.oracle_id.in_(ids))
        )
    ).all()
    best: dict = {}
    for oracle, legalities in rows:
        legalities = legalities or {}
        # Keep the first printing seen, but upgrade to a playable one as soon as we find it.
        if oracle not in best or (_is_playable(legalities) and not _is_playable(best[oracle])):
            best[oracle] = legalities
    return best


async def apply_deck_card_edit(
    session: AsyncSession,
    dc: DeckCard,
    *,
    scryfall_id=None,
    language: str | None = None,
    proxy: bool | None = None,
    special: bool | None = None,
) -> DeckCard:
    """Apply a printing / language / proxy / special change to a deck card.

    Shared by the HTML deck page and the JSON API. Only non-``None`` fields change; a new printing
    is accepted only when it belongs to the same card (matching ``oracle_id``).
    """
    if scryfall_id:
        try:
            sid = scryfall_id if isinstance(scryfall_id, uuid.UUID) else uuid.UUID(str(scryfall_id))
        except (ValueError, AttributeError):
            sid = None
        chosen = await session.get(Card, sid) if sid else None
        if chosen is not None and chosen.oracle_id == dc.oracle_id:
            dc.scryfall_id = chosen.scryfall_id
    if language is not None:
        dc.language = normalize_language(language)
    if proxy is not None:
        dc.proxy = bool(proxy)
    if special is not None:
        dc.special = bool(special)
    await session.commit()
    return dc


async def deck_printings(session: AsyncSession, oracle_id) -> list[dict]:
    """Every printing of a card, newest first with playable printings ahead of variants."""
    rows = (
        await session.execute(
            select(
                Card.scryfall_id, Card.set_code, Card.set_name,
                Card.collector_number, Card.legalities, Card.released_at,
            )
            .where(Card.oracle_id == oracle_id)
            .order_by(Card.released_at.desc().nulls_last())
        )
    ).all()
    out = [
        {"scryfall_id": str(sid), "set_code": sc, "set_name": sn,
         "collector_number": cn, "playable": _is_playable(leg)}
        for sid, sc, sn, cn, leg, _rel in rows
    ]
    out.sort(key=lambda p: not p["playable"])  # stable: playable first, newest order kept within
    return out




@dataclass
class StackCoverage:
    """Detailed coverage information for a single stack in a deck."""
    scryfall_id: uuid.UUID
    quantity: int  # The quantity of this stack in the deck
    in_deck: int   # Quantity in the deck (already in this deck)
    owned: int     # Quantity owned but not in this deck (perfect match)
    swappable: int # Quantity eligible for substitution by name match
    
    def __init__(self, scryfall_id: uuid.UUID, quantity: int):
        self.scryfall_id = scryfall_id
        self.quantity = quantity
        self.in_deck = 0
        self.owned = 0
        self.swappable = 0

    @property
    def total_available(self) -> int:
        """Total quantity available to build this stack."""
        return self.in_deck + self.owned + self.swappable

    @property
    def is_complete(self) -> bool:
        """True if all quantity of this stack is available."""
        return self.quantity <= self.total_available

    def __str__(self) -> str:
        """String representation for display."""
        if self.in_deck > 0:
            return f"✓ {self.in_deck} in deck, {self.owned} owned, {self.swappable} swappable"
        elif self.owned > 0:
            return f"✓ {self.owned} owned, {self.swappable} swappable"
        else:
            return f"{self.swappable} swappable"



@dataclass
class DeckCoverage:
    deck: Deck
    main: list[CardRow] = field(default_factory=list)
    side: list[CardRow] = field(default_factory=list)
    main_combined: list[CardRow] = field(default_factory=list)
    side_combined: list[CardRow] = field(default_factory=list)
    # Statistics for deck coverage
    total_owned: int = 0
    pct_own: float = 0.0
    total_in_deck: int = 0          # total quantity of cards located in this deck
    pct_in_deck: float = 0.0        # percentage of cards located in this deck
    total_needed: int = 0
    missing_count: int = 0          # total physical cards still needed
    unique_missing: int = 0         # distinct cards (oracle / unmatched line) not fully owned
    missing_cost: float = 0.0
    unmatched: int = 0              # lines whose name didn't resolve to a card
    fmt: str | None = None          # selected legality format, if any
    illegal_count: int = 0          # distinct cards not legal in the selected format
    # Legalities for all formats (pre-calculated for display)
    all_legalities: dict[str, bool] = field(default_factory=dict)
    # Detailed breakdown of why a deck is illegal or incomplete for each format
    # Maps format -> {"illegal_cards": [names], "unmatched": [names]}
    legality_details: dict[str, dict[str, list[str]]] = field(default_factory=dict)
    # Stack coverage
    stack_coverage = dict[uuid.UUID, StackCoverage]
    combined_stack_coverage = dict[uuid.UUID, StackCoverage]

    @property
    def is_legal(self) -> bool:
        return bool(self.fmt) and self.illegal_count == 0 and self.unmatched == 0


# Proxy print sheets (#161): which cards to lay out, and basics we skip by default
# (nobody needs to proxy a Plains). Snow-Covered basics count as basics too.
_BASICS = ("Plains", "Island", "Swamp", "Mountain", "Forest", "Wastes")
BASIC_LAND_NAMES = frozenset([*_BASICS, *(f"Snow-Covered {b}" for b in _BASICS)])
PROXY_SOURCES = ("proxy", "missing", "all")


@dataclass
class ProxyCard:
    name: str
    scryfall_id: str | None
    special: bool = False


def proxy_cards(cov: DeckCoverage, source: str = "proxy",
                include_lands: bool = False) -> list[ProxyCard]:
    """Flat, quantity-expanded list of cards to print as proxies for a deck (#161).

    source: 'proxy' = lines flagged Proxy; 'missing' = copies not yet owned; 'all' = the
    whole deck. Basic lands are skipped unless ``include_lands``. Main + sideboard are both
    considered so you can proxy a missing sideboard card too.
    """
    if source not in PROXY_SOURCES:
        source = "proxy"
    out: list[ProxyCard] = []
    for row in [*cov.main, *cov.side]:
        if not include_lands and row.name in BASIC_LAND_NAMES:
            continue
        if source == "proxy":
            copies = row.quantity if row.proxy else 0
        elif source == "missing":
            copies = max(0, row.quantity - row.owned)
        else:  # all
            copies = row.quantity
        out.extend(ProxyCard(row.name, row.scryfall_id, row.special) for _ in range(copies))
    return out


def _card_legality(c, fmt: str | None, legal_by_oracle: dict, illegal_oracles: set) -> str | None:
    """Format legality for a card's oracle; records illegal oracles as a side effect."""
    if not (fmt and c.oracle_id):
        return None
    legality = legal_by_oracle.get(c.oracle_id, {}).get(fmt, "not_legal")
    if legality not in _ALLOWED_LEGALITIES:
        illegal_oracles.add(c.oracle_id)
    return legality


def _tally_missing(cov, deck, needed_by_oracle, owned, price_by_sid, oracle_sid, currency) -> None:
    """Missing counts/cost: once per oracle for matched cards, per line for unmatched.

    Each oracle is costed at the finish the deck runs it in, so a foil deck quotes foil prices.
    """
    finish_by_oracle = {c.oracle_id: c.finish for c in deck.cards if c.oracle_id}
    for oracle, needed in needed_by_oracle.items():
        miss = max(0, needed - owned.get(oracle, 0))
        if miss:
            cov.missing_count += miss
            cov.unique_missing += 1
            cov.missing_cost += miss * unit_price(
                price_by_sid.get(oracle_sid.get(oracle, ""), {}),
                finish_by_oracle.get(oracle, "normal"), currency,
            )
    for c in deck.cards:
        if not c.oracle_id:
            cov.missing_count += c.quantity
            cov.unique_missing += 1
            cov.unmatched += 1


async def deck_coverage(
    session: AsyncSession, deck: Deck, fmt: str | None = None, currency: str = "usd",
    source: str = "tcgplayer",
) -> DeckCoverage:
    
    owned = await _owned_by_oracle(session)
    fmt = fmt if fmt in LEGALITY_FORMATS else None

    cov = DeckCoverage(deck=deck, fmt=fmt)

    # Load card data
    cards = (
            await session.execute(
                select(Card).where(Card.scryfall_id.in_([c.scryfall_id for c in deck.cards if c.scryfall_id]))
            )
        ).all()
    cards = {card[0].scryfall_id: card[0] for card in cards}
    
    price_by_sid: dict[str, dict] = {}
    oracle_sid: dict = {}

    for scryfall_id, card in cards.items():
        price_by_sid[str(scryfall_id)] = resolve_prices(
            card.prices, card.market_prices, source
        ) or {}

        oracle_sid[card.oracle_id] = str(scryfall_id)

    # Legality is judged per oracle from a playable printing, independent of the line's printing.
    legal_by_oracle = await _legalities_by_oracle(
        session, {c.oracle_id for c in deck.cards if c.oracle_id}
    ) if fmt else {}

    # Needed totals per oracle across both boards (ownership is shared between main + side).
    needed_by_oracle: dict = {}
    for c in deck.cards:
        if c.oracle_id:
            needed_by_oracle[c.oracle_id] = needed_by_oracle.get(c.oracle_id, 0) + c.quantity

    # Calculate deck legality
    all_legalities,legality_details,illegal_oracles  = await deck_legality(session, deck)
    cov.all_legalities = all_legalities
    cov.legality_details = legality_details
    cov.illegal_count = len(illegal_oracles)


    # Calculate stack coverage
    stack_coverages = await deck_stack_coverages(session, deck)

    
    ## Generate details for each deck card
    for c in deck.cards:
        row = CardRow(
            name=c.name,
            card_id=c.id,
            scryfall_card=cards[c.scryfall_id] if c.scryfall_id else None,
            board=c.board,
            quantity=c.quantity,
            stack_coverage=stack_coverages[c.id],
            matched=c.oracle_id is not None,
            legality=_card_legality(c, fmt, legal_by_oracle, illegal_oracles),
            proxy=c.proxy,
            special=c.special,
            language=c.language,
            finish=c.finish,
        )
        (cov.main if c.board == "main" else cov.side).append(row)
        cov.total_needed += c.quantity

    # Add a second coverage by combining CardRow by name
    cov.main_combined = []
    cov.side_combined = []
    for result_store, source_board in [[cov.main_combined, cov.main], [cov.side_combined, cov.side]]:
        for name in list({obj.name for obj in source_board}):
        
            rows_to_be_combined = [row for row in source_board if row.name == name]
            result_store.append(copy.deepcopy(rows_to_be_combined[0]))
            for row_to_be_combined in rows_to_be_combined[1:]:
                result_store[-1].quantity += row_to_be_combined.quantity
                result_store[-1].stack_coverage.in_deck += row_to_be_combined.stack_coverage.in_deck
                result_store[-1].stack_coverage.owned=0
                result_store[-1].stack_coverage.swappable += row_to_be_combined.stack_coverage.owned+row_to_be_combined.stack_coverage.swappable

    # Count total cards in deck location (those that are not in a deck location or are in this deck)
    cov.total_owned = 0
    cov.total_in_deck = 0
    for card_row in cov.main_combined+cov.side_combined: 
        cov.total_in_deck += min(card_row.quantity, card_row.stack_coverage.in_deck)
        cov.total_owned += min(card_row.quantity, card_row.stack_coverage.in_deck+card_row.stack_coverage.owned+card_row.stack_coverage.swappable)
    cov.pct_own = round(100 * cov.total_owned / cov.total_needed) if cov.total_needed else 0
    cov.pct_in_deck = round(100 * cov.total_in_deck / cov.total_needed) if cov.total_needed else 0

    # Missing math, counted once per oracle (and per unmatched line).
    _tally_missing(cov, deck, needed_by_oracle, owned, price_by_sid, oracle_sid, currency)
    
    return cov


@dataclass
class MissingEntry:
    name: str
    scryfall_id: str
    missing: int


async def deck_missing(session: AsyncSession, deck: Deck) -> list[MissingEntry]:
    """Matched cards the deck still needs, one entry per oracle (for adding to the wishlist).

    Ownership is shared across both boards and counted by oracle id; unmatched lines (no resolved
    printing) are skipped since the wishlist is keyed by ``scryfall_id``.
    """
    owned = await _owned_by_oracle(session)
    needed_by_oracle: dict = {}
    name_by_oracle: dict = {}
    sid_by_oracle: dict = {}
    for c in deck.cards:
        if not c.oracle_id:
            continue
        needed_by_oracle[c.oracle_id] = needed_by_oracle.get(c.oracle_id, 0) + c.quantity
        name_by_oracle.setdefault(c.oracle_id, c.name)
        if c.scryfall_id:
            sid_by_oracle.setdefault(c.oracle_id, str(c.scryfall_id))

    out: list[MissingEntry] = []
    for oracle, needed in needed_by_oracle.items():
        miss = max(0, needed - owned.get(oracle, 0))
        sid = sid_by_oracle.get(oracle)
        if miss and sid:
            out.append(MissingEntry(name=name_by_oracle[oracle], scryfall_id=sid, missing=miss))
    return out


_MAX_MV_BUCKET = 7  # 7+ collapses into one bucket
_CURVE_ORDER = [str(i) for i in range(_MAX_MV_BUCKET)] + [f"{_MAX_MV_BUCKET}+"]


@dataclass
class DeckStats:
    mana_curve: list[Bar] = field(default_factory=list)   # nonland spells by mana value (mainboard)
    by_color: list[Bar] = field(default_factory=list)     # mainboard cards by color identity
    by_type: list[Bar] = field(default_factory=list)        # mainboard cards by type
    type_counts: dict[str, int] = field(default_factory=dict) # mainboard cards by type (raw)
    total_value: float = 0.0                              # qty * USD across the whole deck

    @property
    def has_data(self) -> bool:
        return bool(self.mana_curve or self.by_color or self.total_value)



async def deck_stats(
    session: AsyncSession, deck: Deck, currency: str = "usd", source: str = "tcgplayer"
) -> DeckStats:
    """Mana curve (nonland mainboard spells), color breakdown, and total USD value."""
    sids = [c.scryfall_id for c in deck.cards if c.scryfall_id]
    info: dict = {}
    if sids:
        rows = (
            await session.execute(
                select(
                    Card.scryfall_id, Card.cmc, Card.color_identity, Card.type_line,
                    Card.prices, Card.market_prices,
                ).where(Card.scryfall_id.in_(sids))
            )
        ).all()
        info = {sid: (cmc, ci, tl, resolve_prices(prices, mp, source))
                for sid, cmc, ci, tl, prices, mp in rows}

    curve: dict[str, int] = {}
    colors: dict[str, int] = {}
    types: dict[str, int] = {}
    total = 0.0
    for c in deck.cards:
        cmc, ci, type_line, prices = info.get(c.scryfall_id, (None, None, None, None))
        total += c.quantity * unit_price(prices, c.finish, currency)
        
        # Types are counted for all mainboard cards
        if c.board == "main" and c.scryfall_id:
            types[_primary_type(type_line)] = types.get(_primary_type(type_line), 0) + c.quantity
        
        # Curve and colors only cover mainboard nonland spells
        if c.board == "main" and c.scryfall_id and not (type_line and "Land" in type_line):
            colors[_color_bucket(ci)] = colors.get(_color_bucket(ci), 0) + c.quantity
            bucket = f"{_MAX_MV_BUCKET}+" if (cmc or 0) >= _MAX_MV_BUCKET else str(int(cmc or 0))
            curve[bucket] = curve.get(bucket, 0) + c.quantity

        
    return DeckStats(
        mana_curve=_bars(curve, order=_CURVE_ORDER),
        by_color=_bars(colors),
        by_type=_bars(types),
        type_counts=types,
        total_value=round(total, 2),
    )


async def deck_legality(
    session: AsyncSession, deck: Deck) -> dict[uuid.UUID, StackCoverage]:
    """Calculate legality of deck."""
    
    illegal_oracles: set = set()
    
    # Pre-calculate legality for all formats
    all_legalities: dict[str, bool] = {}
    legality_details: dict[str, dict[str, list[str]]] = {}
    
    if {c.oracle_id for c in deck.cards if c.oracle_id}:
        # First we get the legalities for all oracle IDs for all formats
        legal_by_oracle_all = await _legalities_by_oracle(
            session, {c.oracle_id for c in deck.cards if c.oracle_id}
        )
        
        # For each format, determine if the deck is legal
        for f in LEGALITY_FORMATS:
            illegal_oracles_for_format: set = set()
            
            # Check each card in the deck
            for c in deck.cards:
                _card_legality(c, f, legal_by_oracle_all, illegal_oracles_for_format)
            
            # Format-specific validation rules
            is_legal_for_format = (len(illegal_oracles_for_format) == 0 
                                  and sum(1 for c in deck.cards if not c.oracle_id) == 0)
            
            # Validate deck size requirements by format - only mainboard cards count toward minimum requirements
            total_mainboard_cards = sum(c.quantity for c in deck.cards if c.board == "main")
            
            # Track format-specific violations separately
            format_violations = []
            
            # Check deck size is greater or equal to the format's minimum decksize (only mainboard)
            if total_mainboard_cards < (100 if f == "commander" else 60):
                is_legal_for_format = False
                format_violations.append(f"{f.title()} format requires at least {(100 if f == "commander" else 60)} mainboard cards ({total_mainboard_cards} found)")
            
            # Other formats: maximum 4 copies of each card name 
            regular_card_counts = {}
            for c in deck.cards:
                if c.board == "main":  # Only check mainboard
                    regular_card_counts[c.name] = regular_card_counts.get(c.name, 0) + c.quantity
            
            # Check for any non-basic land card with more than specified number of copies copies
            for name, count in regular_card_counts.items():
                # Basic lands are allowed to have multiple copies
                if name not in BASIC_LAND_NAMES and count > (1 if f == "commander" else 4):
                    is_legal_for_format = False
                    format_violations.append(f"{f.title()} format allows at most {('1 copy' if f == "commander" else '4 copies')} of each non-basic land card ({name} has {count})")
            
            all_legalities[f] = is_legal_for_format
            
            # Build the details for each format - separate card-specific violations from format violations
            illegal_cards = []
            unmatched_cards = []
            for c in deck.cards:
                if not c.oracle_id:
                    unmatched_cards.append(c.name)
                elif c.oracle_id in illegal_oracles_for_format:
                    illegal_cards.append(c.name)
            
            legality_details[f] = {
                "illegal_cards": list(set(illegal_cards)),
                "unmatched": list(set(unmatched_cards)),
                "format_violations": format_violations  # Separate field for format-specific issues
            }

    return all_legalities, legality_details,illegal_oracles




async def deck_stack_coverages(
    session: AsyncSession, deck: Deck) -> dict[int, StackCoverage]:
    """Calculate detailed coverage for each stack in the deck."""
    await session.refresh(deck, ["location"])
    # Get all collection cards that are located in this deck (already in the deck)
    deck_collection_cards = {}
    if deck.location.id is not None:
        result = (await session.execute(
            select(CollectionCard.scryfall_id, CollectionCard.finish, CollectionCard.language, func.sum(CollectionCard.quantity).label('quantity'))
            .where(CollectionCard.location == deck.location.id)
            .group_by(*CollectionCard.get_group_by_unique_columns())
        )).all()
        deck_collection_cards = {CollectionCard._get_unique_stack_key(*row[:3]): row.quantity for row in result}
    # Get all collection cards that might match which are not in any deck location (available for building the deck)
    unreserved_collection_cards = {}
    result = (await session.execute(
        select(CollectionCard.scryfall_id, CollectionCard.finish, CollectionCard.language, func.sum(CollectionCard.quantity).label('quantity'))
        .outerjoin(Location, CollectionCard.location == Location.id)
        .where((Location.type != "deck") | (Location.id.is_(None)))
        .group_by(*CollectionCard.get_group_by_unique_columns())
    )).all()
    unreserved_collection_cards = {CollectionCard._get_unique_stack_key(*row[:3]): row.quantity for row in result}
    # Calculate coverage for each deck card stack
    stack_coverage = {}
    for dc in deck.cards:
        if dc.scryfall_id is None:
            continue
        # Create StackCoverage object
        sc = StackCoverage(scryfall_id=dc.scryfall_id, quantity=dc.quantity)
        match_id = dc.get_unique_stack_key()
        
        # Count cards already in this deck (matching the exact same scryfall_id, finish, and location)
        if match_id in deck_collection_cards:
            sc.in_deck = deck_collection_cards[match_id]
            
        # Count cards available but not in any deck location (matching by scryfall_id, with exact match)
        if match_id in unreserved_collection_cards:
            sc.owned = unreserved_collection_cards[match_id]
        
        # Count all cards that can be used for substitution (matching by name only)
        sc.swappable = 0  # Will compute this properly below
        
        stack_coverage[dc.id] = sc ## We use dc.id and not dc.scryfall_id because a deck could specify multiple different stacks with the same scryfall_id, for example one normal copy and one foil copy

    # For swappable cards, we need to find all collection cards that match by name only
    # not necessarily by scryfall_id (to allow substitution) - but only for the specific card names in the deck
    if deck.cards:
        # Get unique card names from this deck
        card_names = {dc.name.lower() for dc in deck.cards if dc.scryfall_id}

        if card_names:
            # Get all CollectionCards with matching names (but not in deck location) 
            # and group them by scryfall_id to get total quantities per scryfall_id
            result = await session.execute(
                select(CollectionCard.scryfall_id, Card.name, func.sum(CollectionCard.quantity).label('quantity'))
                .join(Card, CollectionCard.scryfall_id == Card.scryfall_id)
                .outerjoin(Location, CollectionCard.location == Location.id)
                .where(
                    func.lower(Card.name).in_(list(card_names)),
                    (Location.type != "deck") | (Location.id.is_(None))
                )
                .group_by(CollectionCard.scryfall_id, Card.name)
            )

            # Group all matching cards by their scryfall_id
            name_matching_cards = {}
            for row in result.all():
                if row.scryfall_id not in name_matching_cards:
                    name_matching_cards[row.scryfall_id] = 0
                name_matching_cards[row.scryfall_id] += row.quantity

            # Now update the swappable counts - we need to calculate how many unreserved cards 
            # with matching names are available, but exclude exact matches from our calculation
            
            # Create a mapping of scryfall_id -> card name for all cards in name_matching_cards
            # This avoids making individual queries for each card
            scryfall_id_to_name = {}
            if name_matching_cards:
                result = await session.execute(
                    select(Card.scryfall_id, Card.name)
                    .where(Card.scryfall_id.in_(list(name_matching_cards.keys())))
                )
                for row in result.all():
                    scryfall_id_to_name[row.scryfall_id] = row.name.lower()
            
            for dc in deck.cards:
                if dc.scryfall_id is None:
                    continue
                    
                # For each deck card, count all unreserved cards that match by name (but not the exact same scryfall_id)
                # This counts all substitute candidates for this specific card
                swappable_count = 0
                for scryfall_id, quantity in name_matching_cards.items():
                    # Only count as swappable if it's a different scryfall_id (not the exact same card) 
                    # but has the same name
                    if scryfall_id != dc.scryfall_id:
                        # Check if this scryfall_id corresponds to a card with the same name as our deck card
                        card_name = scryfall_id_to_name.get(scryfall_id)
                        if card_name and card_name == dc.name.lower():
                            swappable_count += quantity
                
                stack_coverage[dc.id].swappable = swappable_count
    return stack_coverage




async def match_deck_cards_in_deck_collection_cards(session: AsyncSession, source_cards: list[DeckCard], source_location: int | None) -> dict[str, list[CollectionCard]]:
    """Find CollectionCards matching DeckCards, in source_location. (Custom wrapper for match_deck_cards_in_collection_cards)
    
    Returns a dict mapping each DeckCard's scryfall_id to a list of matching CollectionCards.
    """

    # Get all the unique scryfall_ids we're looking for
    scryfall_ids = [card.scryfall_id for card in source_cards if card.scryfall_id]
    # Get all CollectionCards with matching scryfall_ids and not in a deck location
    custom_collection_cards = list((await session.execute(select(CollectionCard).where(CollectionCard.scryfall_id.in_(scryfall_ids), (CollectionCard.location == source_location)))).scalars().all())
    
    return await match_deck_cards_in_collection_cards(session, source_cards=source_cards, custom_collection_cards=custom_collection_cards)



async def match_deck_cards_in_unreserved_collection_cards(session: AsyncSession, source_cards: list[DeckCard]) -> dict[str, list[CollectionCard]]:
    """Find CollectionCards matching DeckCards, excluding those in deck locations. (Custom wrapper for match_deck_cards_in_collection_cards)
    
    Returns a dict mapping each DeckCard's scryfall_id to a list of matching CollectionCards.
    """

    # Get all the unique scryfall_ids we're looking for
    scryfall_ids = [card.scryfall_id for card in source_cards if card.scryfall_id]
    # Get all CollectionCards with matching scryfall_ids and not in a deck location
    custom_collection_cards = list((await session.execute(select(CollectionCard).outerjoin(Location, CollectionCard.location == Location.id).where(CollectionCard.scryfall_id.in_(scryfall_ids), (Location.type != "deck") | (Location.id.is_(None))))).scalars().all())
    
    return await match_deck_cards_in_collection_cards(session, source_cards=source_cards, custom_collection_cards=custom_collection_cards)


async def match_deck_cards_in_collection_cards(session: AsyncSession, source_cards: list[DeckCard], custom_collection_cards: list[CollectionCard] | None = None) -> dict[str, list[CollectionCard]]:
    """Find CollectionCards matching DeckCards.
    
    Returns a dict mapping each DeckCard's scryfall_id to a list of matching CollectionCards.
    """
    
    # Construct base output
    matching_cards = {unique_stack_key: [] for unique_stack_key in [card.get_unique_stack_key() for card in source_cards if card.scryfall_id]}
    
    # Get all CollectionCards with matching scryfall_ids and not in a deck location
    if(custom_collection_cards is not None):
        collection_cards = custom_collection_cards
    else:
        collection_cards = (await session.execute(select(CollectionCard).where(CollectionCard.scryfall_id.in_([card.scryfall_id for card in source_cards if card.scryfall_id])))).scalars().all()

    # Filter cards based on exact matching properties
    for source_card in source_cards:
        for collection_card in (obj for obj in collection_cards if obj.scryfall_id == source_card.scryfall_id):
        
            if(source_card.language!=collection_card.language): continue
            if(source_card.finish!=collection_card.finish): continue

            # Add to the result list for this scryfall_id
            if source_card.scryfall_id is not None:
                matching_cards[source_card.get_unique_stack_key()].append(collection_card)
    
    return matching_cards


async def checkout_deck(session: AsyncSession, deck: Deck, mainboard: bool = True, sideboard: bool = False) -> bool:
    """ Move cards in the collection (other than decks) to this deck's location. 
   
    For each card in the deck:
    1. If there is no matching unreserved cards in collection, do nothing
    2. If there are more than 0 but insufficient cards (quantity), move existing cards to the deck
    3. If there are matching card stack quanities in collection simply move them
    4. If there are more cards than needed (quantity), handle properly via splitting and quantity adjustment
    """
    await session.refresh(deck, ["location"])

    # Get cards to checkout based on parameters 
    deck_cards = []
    if mainboard: deck_cards += [c for c in deck.cards if c.board == "main"]
    if sideboard: deck_cards += [c for c in deck.cards if c.board == "side"]

    unreserved_matching_stacks = await match_deck_cards_in_unreserved_collection_cards(session, deck_cards)
    deck_matching_stacks = await match_deck_cards_in_deck_collection_cards(session, deck_cards, source_location=deck.location.id)

    # For each card to checkout:
    for deck_card in deck_cards:

        unique_stack_key = deck_card.get_unique_stack_key()
        available_cards = sum([c.quantity for c in unreserved_matching_stacks[unique_stack_key]])
        existing_cards = sum([c.quantity for c in deck_matching_stacks[unique_stack_key]])
        quantity_to_checkout = deck_card.quantity-existing_cards


        # Look through available stacks as long as not all cards are checked out and there are still cards to checkout
        while quantity_to_checkout > 0 and available_cards > 0:
            
            # Find largest matching stack
            stack_to_move_ix = max(range(len(unreserved_matching_stacks[unique_stack_key])), key=lambda i: unreserved_matching_stacks[unique_stack_key][i].quantity)

            moved_cards_count = await move_qty_from_existing_stack(session, collection_card=unreserved_matching_stacks[unique_stack_key][stack_to_move_ix], quantity=quantity_to_checkout, new_location_id=deck.location.id)
            quantity_to_checkout -= moved_cards_count
            available_cards -= moved_cards_count

            # If the stack was insufficient to fill the need, and more stacks exist, we will pop this stack out of the canddidates sos that we can move to the next biggest stack.
            if(quantity_to_checkout > 0 and available_cards > 0):
                unreserved_matching_stacks[unique_stack_key].pop(stack_to_move_ix)


    await session.commit()
    return True






async def deconstruct_deck(session: AsyncSession, deck: Deck, location: int | None = None) -> bool:
    """ Move cards in a deck to the specified location. """

    # For each deck card, find all matching collection cards and move them to the location
    for deck_card in deck.cards:
        if deck_card.scryfall_id is not None:
            # Find all collection cards that match this scryfall_id (regardless of their current location)
            result = await session.execute(
                select(CollectionCard).where(
                    CollectionCard.scryfall_id == deck_card.scryfall_id
                )
            )
            collection_cards = result.scalars().all()

            # Move each matching collection card to the specified location
            for collection_card in collection_cards:
                await move_stack_location(session, collection_card.id, location_id=location)

    await session.commit()
    return True
