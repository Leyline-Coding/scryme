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
from dataclasses import dataclass, field

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.models import Card, CollectionCard, Deck, DeckCard
from src.pricing import resolve_prices
from src.stats import Bar, _bars, _color_bucket

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
        if not s or s.startswith(("#", "//")):
            continue
        if s.lower().startswith("sideboard"):
            board = "side"
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
    name: str
    quantity: int
    board: str
    owned: int
    matched: bool
    scryfall_id: str | None
    legality: str | None = None     # status in the selected format, or None when no format chosen
    card_id: int = 0                # deck_card row id (for editing the printing)
    set_code: str | None = None     # representative printing shown for this line
    set_name: str | None = None
    collector_number: str | None = None
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
class DeckCoverage:
    deck: Deck
    main: list[CardRow] = field(default_factory=list)
    side: list[CardRow] = field(default_factory=list)
    total_needed: int = 0
    missing_count: int = 0          # total physical cards still needed
    unique_missing: int = 0         # distinct cards (oracle / unmatched line) not fully owned
    missing_cost: float = 0.0
    unmatched: int = 0              # lines whose name didn't resolve to a card
    fmt: str | None = None          # selected legality format, if any
    illegal_count: int = 0          # distinct cards not legal in the selected format

    @property
    def owned_count(self) -> int:
        return self.total_needed - self.missing_count

    @property
    def pct_complete(self) -> int:
        return round(100 * self.owned_count / self.total_needed) if self.total_needed else 0

    @property
    def is_legal(self) -> bool:
        return bool(self.fmt) and self.illegal_count == 0 and self.unmatched == 0


async def _load_deck_printings(session: AsyncSession, sids: list, source: str):
    """Per-sid price map, printing tuple, and oracle→sid map for the deck's resolved cards."""
    price_by_sid: dict[str, dict] = {}
    print_by_sid: dict[str, tuple] = {}   # sid -> (set_code, set_name, collector_number)
    oracle_sid: dict = {}
    if not sids:
        return price_by_sid, print_by_sid, oracle_sid
    rows = (
        await session.execute(
            select(
                Card.scryfall_id, Card.oracle_id, Card.prices, Card.market_prices,
                Card.set_code, Card.set_name, Card.collector_number,
            ).where(Card.scryfall_id.in_(sids))
        )
    ).all()
    for sid, oracle, prices, market_prices, set_code, set_name, collector in rows:
        price_by_sid[str(sid)] = resolve_prices(prices, market_prices, source) or {}
        print_by_sid[str(sid)] = (set_code, set_name, collector)
        oracle_sid[oracle] = str(sid)
    return price_by_sid, print_by_sid, oracle_sid


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

    sids = [c.scryfall_id for c in deck.cards if c.scryfall_id]
    price_by_sid, print_by_sid, oracle_sid = await _load_deck_printings(session, sids, source)
    # Legality is judged per oracle from a playable printing, independent of the line's printing.
    legal_by_oracle = await _legalities_by_oracle(
        session, {c.oracle_id for c in deck.cards if c.oracle_id}
    ) if fmt else {}

    # Needed totals per oracle across both boards (ownership is shared between main + side).
    needed_by_oracle: dict = {}
    for c in deck.cards:
        if c.oracle_id:
            needed_by_oracle[c.oracle_id] = needed_by_oracle.get(c.oracle_id, 0) + c.quantity

    cov = DeckCoverage(deck=deck, fmt=fmt)
    illegal_oracles: set = set()
    for c in deck.cards:
        legality = _card_legality(c, fmt, legal_by_oracle, illegal_oracles)
        set_code, set_name, collector = print_by_sid.get(str(c.scryfall_id), (None, None, None))
        row = CardRow(
            name=c.name, quantity=c.quantity, board=c.board,
            owned=owned.get(c.oracle_id, 0) if c.oracle_id else 0,
            matched=c.oracle_id is not None,
            scryfall_id=str(c.scryfall_id) if c.scryfall_id else None,
            legality=legality,
            card_id=c.id,
            set_code=set_code, set_name=set_name, collector_number=collector,
            proxy=c.proxy, special=c.special, language=c.language, finish=c.finish,
        )
        (cov.main if c.board == "main" else cov.side).append(row)
        cov.total_needed += c.quantity
    cov.illegal_count = len(illegal_oracles)

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
    total = 0.0
    for c in deck.cards:
        cmc, ci, type_line, prices = info.get(c.scryfall_id, (None, None, None, None))
        total += c.quantity * unit_price(prices, c.finish, currency)
        # Curve + color pie cover mainboard nonland spells, so basics don't dominate.
        if not c.scryfall_id or c.board != "main" or (type_line and "Land" in type_line):
            continue
        colors[_color_bucket(ci)] = colors.get(_color_bucket(ci), 0) + c.quantity
        bucket = f"{_MAX_MV_BUCKET}+" if (cmc or 0) >= _MAX_MV_BUCKET else str(int(cmc or 0))
        curve[bucket] = curve.get(bucket, 0) + c.quantity

    return DeckStats(
        mana_curve=_bars(curve, order=_CURVE_ORDER),
        by_color=_bars(colors),
        total_value=round(total, 2),
    )
