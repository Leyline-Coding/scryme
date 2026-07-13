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

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.currency import unit_price
from src.models import Card, CollectionCard, Deck, DeckCard
from src.stats import Bar, _bars, _color_bucket

_LINE = re.compile(r"^\s*(\d+)\s*[xX]?\s+(.+?)\s*$")
# Strip trailing export markers like "*F*" (foil) / "*E*" (etched).
_MARKER = re.compile(r"(\s*\*[^*]*\*)+\s*$")
# Strip a trailing "(SET) 123" / "(SET)" printing hint from a card name.
_SET_SUFFIX = re.compile(r"\s*\([A-Za-z0-9]{2,6}\)\s*[A-Za-z0-9-]*\s*$")


@dataclass
class ParsedLine:
    quantity: int
    name: str
    board: str  # main | side


def parse_decklist(text: str | None) -> list[ParsedLine]:
    out: list[ParsedLine] = []
    board = "main"
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s or s.startswith(("#", "//")):
            continue
        low = s.lower()
        if low.startswith("sideboard"):
            board = "side"
            continue
        sb = False
        if low.startswith("sb:"):
            sb, s = True, s[3:].strip()
        m = _LINE.match(s)
        if not m:
            continue
        name = _MARKER.sub("", m.group(2))
        name = _SET_SUFFIX.sub("", name).strip()
        if name:
            out.append(ParsedLine(int(m.group(1)), name, "side" if sb else board))
    return out


def _merge_lines(parsed: list[ParsedLine]) -> list[ParsedLine]:
    """Combine lines with the same name + board (e.g. basic lands across collector numbers)."""
    merged: dict[tuple, ParsedLine] = {}
    order: list[tuple] = []
    for p in parsed:
        key = (p.name.lower(), p.board)
        if key in merged:
            merged[key].quantity += p.quantity
        else:
            merged[key] = ParsedLine(p.quantity, p.name, p.board)
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


async def create_deck(session: AsyncSession, name: str, decklist_text: str) -> Deck:
    parsed = _merge_lines(parse_decklist(decklist_text))
    owned_sids = set(await session.scalars(select(CollectionCard.scryfall_id)))
    resolved = await _resolve_names(session, [p.name for p in parsed], owned_sids)

    deck = Deck(name=(name or "").strip()[:256] or "Untitled deck")
    for p in parsed:
        oracle, sid = resolved.get(p.name.lower(), (None, None))
        deck.cards.append(
            DeckCard(name=p.name, quantity=p.quantity, board=p.board,
                     oracle_id=oracle, scryfall_id=sid)
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


async def deck_coverage(
    session: AsyncSession, deck: Deck, fmt: str | None = None, currency: str = "usd"
) -> DeckCoverage:
    owned = await _owned_by_oracle(session)
    fmt = fmt if fmt in LEGALITY_FORMATS else None

    sids = [c.scryfall_id for c in deck.cards if c.scryfall_id]
    price_by_sid: dict[str, dict] = {}
    print_by_sid: dict[str, tuple] = {}   # sid -> (set_code, set_name, collector_number)
    oracle_sid: dict = {}
    if sids:
        rows = (
            await session.execute(
                select(
                    Card.scryfall_id, Card.oracle_id, Card.prices,
                    Card.set_code, Card.set_name, Card.collector_number,
                ).where(Card.scryfall_id.in_(sids))
            )
        ).all()
        for sid, oracle, prices, set_code, set_name, collector in rows:
            price_by_sid[str(sid)] = prices or {}
            print_by_sid[str(sid)] = (set_code, set_name, collector)
            oracle_sid[oracle] = str(sid)
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
        legality = None
        if fmt and c.oracle_id:
            legality = legal_by_oracle.get(c.oracle_id, {}).get(fmt, "not_legal")
            if legality not in _ALLOWED_LEGALITIES:
                illegal_oracles.add(c.oracle_id)
        set_code, set_name, collector = print_by_sid.get(str(c.scryfall_id), (None, None, None))
        row = CardRow(
            name=c.name, quantity=c.quantity, board=c.board,
            owned=owned.get(c.oracle_id, 0) if c.oracle_id else 0,
            matched=c.oracle_id is not None,
            scryfall_id=str(c.scryfall_id) if c.scryfall_id else None,
            legality=legality,
            card_id=c.id,
            set_code=set_code, set_name=set_name, collector_number=collector,
            proxy=c.proxy, special=c.special, language=c.language,
        )
        (cov.main if c.board == "main" else cov.side).append(row)
        cov.total_needed += c.quantity
    cov.illegal_count = len(illegal_oracles)

    # Missing math, counted once per oracle (and per unmatched line).
    for oracle, needed in needed_by_oracle.items():
        miss = max(0, needed - owned.get(oracle, 0))
        if miss:
            cov.missing_count += miss
            cov.unique_missing += 1
            cov.missing_cost += miss * unit_price(
                price_by_sid.get(oracle_sid.get(oracle, ""), {}), "normal", currency
            )
    for c in deck.cards:
        if not c.oracle_id:
            cov.missing_count += c.quantity
            cov.unique_missing += 1
            cov.unmatched += 1
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


async def deck_stats(session: AsyncSession, deck: Deck, currency: str = "usd") -> DeckStats:
    """Mana curve (nonland mainboard spells), color breakdown, and total USD value."""
    sids = [c.scryfall_id for c in deck.cards if c.scryfall_id]
    info: dict = {}
    if sids:
        rows = (
            await session.execute(
                select(
                    Card.scryfall_id, Card.cmc, Card.color_identity, Card.type_line, Card.prices
                ).where(Card.scryfall_id.in_(sids))
            )
        ).all()
        info = {sid: (cmc, ci, tl, prices) for sid, cmc, ci, tl, prices in rows}

    curve: dict[str, int] = {}
    colors: dict[str, int] = {}
    total = 0.0
    for c in deck.cards:
        cmc, ci, type_line, prices = info.get(c.scryfall_id, (None, None, None, None))
        total += c.quantity * unit_price(prices, "normal", currency)
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
