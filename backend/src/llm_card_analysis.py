
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.deck_builder import classify_role
from src.deck_synergy import deck_themes, deck_tribes
from src.decks import DeckCoverage, deck_coverage, deck_stats
from src.models import Card, CollectionCard, Deck, Location

# Commander text is joined onto one line so the grounding block stays line-oriented.
_COMMANDER_TEXT = " / "
_COMMANDER_TEXT_LIMIT = 600


@dataclass
class DeckContext:
    name: str
    description: str | None
    identity: set[str] = field(default_factory=set)  # color identity (union of the deck's cards)
    is_commander: bool = False
    commander: str = ""
    commander_text: str = ""
    commanders: list[str] = field(default_factory=list)  # all legendary creatures (ambiguous which)
    deck_coverage: DeckCoverage | None = None
    useCombined: bool = False
    curve: str = ""
    colors: str = ""
    themes: list[str] = field(default_factory=list)
    tribes: list[str] = field(default_factory=list)   # creature types the deck cares about (#295)
    key_cards: list[str] = field(default_factory=list)


    @property
    def identity_str(self) -> str:
        return "".join(c for c in "WUBRG" if c in self.identity) or "colorless"

    @property
    def format_note(self) -> str:
        return (f"Commander (color identity {self.identity_str})" if self.is_commander
                else f"color identity {self.identity_str}")

    def block(self) -> str:
        """Grounded context block for a prompt."""
        lines = [f"Deck '{self.name}' — {self.format_note}."]
        if self.description:
            lines.append("Deck description: " + self.description)
        if len(self.commanders) > 1:
            lines.append("Possible commanders (deck has several legendary creatures): "
                         + ", ".join(self.commanders))
        elif self.commander:
            lines.append(f"Commander: {self.commander} — {self.commander_text}")
        lines.append(f"Mana curve (nonland): {self.curve or 'n/a'}")
        lines.append(f"Colors: {self.colors or 'n/a'}")
        if self.themes:
            lines.append("Themes/keywords: " + ", ".join(self.themes))
        if self.tribes:
            lines.append("Creature types this deck cares about: " + ", ".join(self.tribes))
        if self.key_cards:
            lines.append("Notable cards: " + ", ".join(self.key_cards))
        lines.append(f"Decklist:\n{self.decklist_text()}")
        return "\n".join(lines)

    def identity_rule(self) -> str:
        return (f"Only recommend cards within the deck's color identity ({self.identity_str}) "
                "and legal in its format — never cards of other colors.")

    def decklist_text(self):
        if not self.deck_coverage:
            return ""
        lines = [
            f"{r.quantity} {r.name}"
            for r in (
                self.deck_coverage.main_combined if self.useCombined else self.deck_coverage.main
            )
        ]
        return "\n".join(lines)



@dataclass
class _DeckScan:
    identity: set
    kw_counts: Counter
    text_cards: list
    valued: list          # (usd, name, type_line)
    commander: str
    commander_text: str
    commanders: list
    creature_lines: list = field(default_factory=list)   # for tribe detection (#295)
    legend_text: str = ""   # every legendary creature's full text, pooled for tribe detection


def _scan_one_card(scan: _DeckScan, name, type_line, ci, oracle, keywords, prices) -> None:
    """Fold a single card's data into the running deck scan."""
    scan.identity.update(ci or [])
    for k in (keywords or []):
        scan.kw_counts[k] += 1
    scan.text_cards.append((oracle or "").lower())
    tl = (type_line or "").lower()
    if "creature" in tl:
        scan.creature_lines.append(type_line)
    if "legendary" in tl and "creature" in tl:
        scan.commanders.append(name)
        scan.legend_text += " " + (oracle or "")
        if not scan.commander:
            scan.commander = name
            # The WHOLE ability, not just its first line (#295). A commander's engine is routinely
            # on line two or three — truncating to the first line hid the deck's actual plan from
            # the model, which is a large part of why its picks read as generic goodstuff.
            scan.commander_text = _COMMANDER_TEXT.join(
                ln for ln in (oracle or "").splitlines() if ln.strip()
            )[:_COMMANDER_TEXT_LIMIT]
    if "land" not in tl:
        scan.valued.append((float((prices or {}).get("usd") or 0.0), name, tl))


def _scan_deck_cards(info: dict) -> _DeckScan:
    """Aggregate identity, keywords, oracle text, commanders, and values across the deck's cards."""
    scan = _DeckScan(set(), Counter(), [], [], "", "", [])
    for _sid, row in info.items():
        _scan_one_card(scan, *row)
    return scan




async def deck_ai_context(
    session: AsyncSession, deck: Deck, useCombined: bool = True
) -> DeckContext:
    """Rich grounded context for the AI deck features: identity, commander, themes, key cards."""
    stats = await deck_stats(session, deck)
    curve = ", ".join(f"{b.label}:{b.count}" for b in stats.mana_curve)
    colors = ", ".join(f"{b.label}:{b.count}" for b in stats.by_color)

    sids = [c.scryfall_id for c in deck.cards if c.scryfall_id]
    info: dict = {}
    if sids:
        for row in (await session.execute(
            select(Card.scryfall_id, Card.name, Card.type_line, Card.color_identity,
                   Card.oracle_text, Card.keywords, Card.prices).where(Card.scryfall_id.in_(sids))
        )).all():
            info[row[0]] = row[1:]

    scan = _scan_deck_cards(info)
    themes = deck_themes(scan.kw_counts, scan.text_cards)
    key_cards = [n for _v, n, _t in sorted(scan.valued, reverse=True)[:5]]

    tribes = deck_tribes(scan.legend_text, scan.creature_lines)
    return DeckContext(
        name=deck.name,
        description=deck.description,
        identity=scan.identity,
        is_commander=bool(scan.commander),
        commander=scan.commander,
        commander_text=scan.commander_text,
        commanders=scan.commanders[:4],
        deck_coverage=(await deck_coverage(session, deck)),
        useCombined=useCombined,
        curve=curve,
        colors=colors,
        themes=themes[:6],
        tribes=sorted(tribes)[:6],
        key_cards=key_cards,
    )





# What each shortlisted candidate is described as, and how many are sent (#295). Fewer, better
# annotated candidates beat a longer bare list: reasoning models spend tokens proportional to input
# size, so the budget is better spent telling the model what a card *does* than naming more of them.
_SHORTLIST = 40
_SNIPPET = 110



@dataclass
class CollectionContext:
    cardlist: list[CardScan]

    def block(self) -> str:
        """Grounded context block for a prompt."""
        lines = []
        lines.append(f"Card List:\n{self.cardlist_text()}")
        return "\n".join(lines)


    def cardlist_text(self):
        if not self.cardlist:
            return ""
        lines = [f"{r.name}" for r in self.cardlist]
        return "\n".join(lines)


@dataclass
class CardScan:
    name: str
    scryfall_id: str
    role: str
    keywords: list[str]
    snippet: str
    #synergy: int

    def line(self) -> str:
        """One annotated candidate line: name, role, matched keywords, what it actually does."""
        bits = [self.role]
        if self.keywords:
            bits.append("/".join(self.keywords[:3]))
        if self.snippet:
            bits.append(self.snippet)
        return f"{self.name} — " + " | ".join(bits)



def _snippet(oracle_text: str | None) -> str:
    """A one-line gist of a card's rules text, short enough to send 40 of them."""
    text = " ".join((oracle_text or "").split())
    return text[:_SNIPPET] + ("…" if len(text) > _SNIPPET else "")



async def collection_ai_context(session: AsyncSession, collection_cards_filter: str) -> CollectionContext:
    """The owned, in-identity candidate pool, annotated and scored for deck fit (#294/#295)."""
   

    rows = (await session.execute(
        select(Card.name, Card.oracle_id, Card.scryfall_id, Card.color_identity,
                Card.type_line, Card.oracle_text, Card.keywords)
        .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
        .outerjoin(Location, CollectionCard.location == Location.id)
        .where(
            CollectionCard.quantity>0, Card.oracle_id.is_not(None),
            (Location.type != "deck") | (Location.id.is_(None)),
            and_(Card.type_line != "Card", Card.type_line.not_ilike("%token%"))
        )
        .distinct(Card.oracle_id)
        .order_by(Card.oracle_id, Card.released_at.desc().nulls_last())
    )).all()

    '''
    deck_sids = [c.scryfall_id for c in deck.cards if c.scryfall_id]
    deck_oracles = {c.oracle_id for c in deck.cards if c.oracle_id}
    identity: set[str] = set()
    if deck_sids:
        for (ci,) in (await session.execute(
            select(Card.color_identity).where(Card.scryfall_id.in_(deck_sids))
        )).all():
            identity.update(ci or [])

    profile = DeckProfile()
    seen: set[str] = set()
    pool: list[_PoolCard] = []
    for name, _oracle_id, sid, _ci, type_line, oracle_text, _keywords in rows:
        if oracle_id in deck_oracles or name.lower() in seen:
            continue
        if identity and not set(ci or []).issubset(identity):
            continue
        seen.add(name.lower())
        score, _ = card_synergy(
            profile, keywords=keywords, type_line=type_line, oracle_text=oracle_text
        )
        pool.append(_PoolCard(
            name=name, scryfall_id=str(sid), role=classify_role(type_line, oracle_text),
            keywords=[k for k in (keywords or []) if k in profile.themes],
            snippet=_snippet(oracle_text), synergy=score,
        ))
    '''

    pool: list[CardScan] = []
    for name, _oracle_id, sid, _ci, type_line, oracle_text, _keywords in rows:
        pool.append(CardScan(
            name=name,
            scryfall_id=str(sid),
            role=classify_role(type_line, oracle_text),
            keywords=[],
            snippet=_snippet(oracle_text)
        ))



    return CollectionContext(
        cardlist=pool
    )
