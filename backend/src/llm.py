"""LLM integration (#163): config, an OpenAI-compatible chat client, and grounded deck features.

Config is stored in-app (:class:`~src.models.LLMSettings`, single row) with the API key encrypted
at rest (Fernet, key file in the data dir); it falls back to ``SCRYME_LLM_*`` env vars. Works with
any OpenAI ``/chat/completions``-compatible endpoint — OpenAI, OpenRouter, or a local Ollama /
LM Studio server. The HTTP client is injectable so tests use a deterministic fake (no network).

Grounding: features feed the model the user's real deck + owned cards and **validate** any card the
model names against the database, so a hallucinated card never reaches the UI.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

import httpx
from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src import __version__
from src.config import get_settings
from src.llm_card_analysis import collection_ai_context, deck_ai_context
from src.models import Card, CollectionCard, Deck, DeckChatMessage, LLMSettings, NLQCache
from src.search import SearchError, build_search

_UA = f"scryme/{__version__} (+https://github.com/Leyline-Coding/scryme)"


# --- secret store (encrypt the API key at rest) -------------------------------------------------

def _fernet() -> Fernet:
    """Load (or lazily create) the data-dir Fernet key used to encrypt the stored API key."""
    path = Path(get_settings().data_dir) / "llm.key"
    if path.exists():
        key = path.read_bytes()
    else:
        key = Fernet.generate_key()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(key)
        os.chmod(path, 0o600)
    return Fernet(key)


def encrypt_secret(value: str) -> str:
    return _fernet().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_secret(token: str | None) -> str:
    if not token:
        return ""
    try:
        return _fernet().decrypt(token.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""  # key rotated / corrupt — user must re-enter


# --- config -------------------------------------------------------------------------------------

@dataclass
class LLMConfig:
    base_url: str = ""
    api_key: str = ""
    chat_model: str = ""
    embed_model: str = ""
    enabled: bool = False

    @property
    def ready(self) -> bool:
        return self.enabled and bool(self.base_url)


async def get_config(session: AsyncSession) -> LLMConfig:
    """Resolve LLM config: the in-app row if present, else the SCRYME_LLM_* environment."""
    s = get_settings()
    if s.read_only:
        # A read-only instance is a public demo: anonymous visitors must not be able to spend the
        # owner's model credits or compute, whatever is configured.
        return LLMConfig()
    row = await session.get(LLMSettings, 1)
    if row is None:
        return LLMConfig(
            base_url=s.llm_base_url, api_key=s.llm_api_key, chat_model=s.llm_chat_model,
            embed_model=s.llm_embed_model, enabled=bool(s.llm_base_url),
        )
    return LLMConfig(
        base_url=row.base_url, api_key=decrypt_secret(row.api_key_enc),
        chat_model=row.chat_model or s.llm_chat_model,
        embed_model=row.embed_model or s.llm_embed_model, enabled=row.enabled,
    )


async def save_config(
    session: AsyncSession, *, base_url: str, api_key: str | None,
    chat_model: str, embed_model: str, enabled: bool,
) -> None:
    """Upsert the config row. A blank ``api_key`` keeps the existing key (so it isn't wiped)."""
    row = await session.get(LLMSettings, 1)
    if row is None:
        row = LLMSettings(id=1)
        session.add(row)
    row.base_url = (base_url or "").strip()
    if api_key:
        row.api_key_enc = encrypt_secret(api_key)
    row.chat_model = (chat_model or "").strip()
    row.embed_model = (embed_model or "").strip()
    row.enabled = enabled
    await session.commit()


# --- chat client --------------------------------------------------------------------------------

class ChatClient:
    """Minimal OpenAI-compatible chat client."""

    def __init__(self, cfg: LLMConfig):
        self.cfg = cfg

    async def chat(self, messages: list[dict], temperature: float = 0.4,
                   max_tokens: int = 2000) -> str:
        # Note: reasoning models (e.g. Gemma QAT) spend part of this budget on hidden reasoning
        # before emitting `content`, so keep max_tokens generous or `content` can come back empty.
        headers = {"User-Agent": _UA, "Content-Type": "application/json"}
        if self.cfg.api_key:
            headers["Authorization"] = f"Bearer {self.cfg.api_key}"
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                f"{self.cfg.base_url.rstrip('/')}/chat/completions",
                headers=headers,
                json={"model": self.cfg.chat_model, "messages": messages,
                      "temperature": temperature, "max_tokens": max_tokens, "stream": False},
            )
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()


async def test_connection(cfg: LLMConfig, client: ChatClient | None = None) -> tuple[bool, str]:
    """Round-trip a tiny prompt. Returns (ok, human-readable message)."""
    if not cfg.base_url:
        return False, "No endpoint URL set."
    client = client or ChatClient(cfg)
    try:
        await client.chat([{"role": "user", "content": "Reply with: ok"}], max_tokens=5)
    except httpx.HTTPStatusError as exc:
        return False, f"HTTP {exc.response.status_code} from the endpoint."
    except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
        return False, f"Could not reach the endpoint: {exc}"
    return True, f"Connected to {cfg.base_url} ({cfg.chat_model})."


# --- grounded deck features ---------------------------------------------------------------------



# Distilled deckbuilding guidance (paraphrased from established sources) that grounds the deck AI
# features so advice follows accepted ratios/archetypes instead of being generic.
_DECKBUILD_GUIDE = (
    "Deckbuilding guidance:\n"
    "- Commander (100-card singleton) target mix: ~1 commander, 36-38 lands, 10-12 ramp, 8-10 card "
    "draw, 8-10 removal (mostly spot removal plus a few board wipes), 7-10 win conditions, and "
    "15-20 synergy/theme cards. Aim for an average mana value around 2.5-3.5, and let roughly 30% "
    "of nonland cards be interaction or card draw. More colors -> run more ramp and fixing.\n"
    "- 60-card constructed: ~17-18 lands for aggro up to 24-26 for control, a low focused curve, "
    "usually 4 copies of key cards, and one clear game plan.\n"
    "- Archetypes: aggro (cheap threats, race, reach), control (removal + wraths + card advantage, "
    "few win conditions), midrange (efficient threats plus interaction), combo (a 2-3 card engine "
    "with tutors and protection).\n"
    "- Prefer repeatable effects over one-shots, run multiple win-condition paths, and make every "
    "card advance the deck's plan and synergize with the commander / theme."
)






async def _chat_nonempty(client: ChatClient, messages: list[dict], retries: int = 2, **kw) -> str:
    """Call chat, retrying on an empty reply. Reasoning models sometimes spend the whole token
    budget on hidden reasoning and return empty ``content``; a retry usually succeeds."""
    text = ""
    for _ in range(retries + 1):
        text = await client.chat(messages, **kw)
        if text.strip():
            break
    return text


@dataclass
class Suggestion:
    name: str
    scryfall_id: str
    reason: str


@dataclass
class SuggestResult:
    suggestions: list[Suggestion] = field(default_factory=list)
    considered: int = 0  # size of the owned candidate pool shown to the model
    empty: bool = False  # the model returned no usable text (e.g. reasoning ate the token budget)



def _parse_suggestions(text: str, pool: dict[str, tuple[str, str]]) -> list[Suggestion]:
    """Keep only 'Name — reason' lines whose card is actually in the owned candidate pool.

    Names are normalized (list markers + markdown emphasis stripped) before the exact-match check,
    so a card is only ever suggested if the model named a real card the user owns.
    """
    out: list[Suggestion] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*•·0123456789.) \t").strip()
        if not line:
            continue
        for sep in (" — ", " - ", "—", " – ", ":"):
            if sep in line:
                name, reason = line.split(sep, 1)
                break
        else:
            name, reason = line, ""
        key = name.strip().strip("*_`\"'.").strip().lower()
        entry = pool.get(key)
        if entry and key not in seen:
            seen.add(key)
            out.append(Suggestion(name=entry[0], scryfall_id=entry[1],
                                  reason=reason.strip().strip("*_`").strip()))
    return out



async def has_config_row(session: AsyncSession) -> bool:
    return (await session.scalar(select(func.count()).select_from(LLMSettings))) > 0


# --- natural language -> Scryfall search syntax (#171) ------------------------------------------

_NL_SYSTEM = (
    "You translate a Magic: The Gathering search request into ONE Scryfall-syntax query. "
    "Output only the query on a single line — no explanation, no code fences.\n"
    "Filters: bare words match the card NAME (so put rules text under o:, never as loose words); "
    "c: or id: colors (w/u/b/r/g or a guild name); t: type; o: oracle text; "
    "mv (or cmc), pow, tou, loy take a number with = < > <= >= or :; "
    "r: rarity (common/uncommon/rare/mythic); s: set code; f: format legality "
    "(standard/pioneer/modern/legacy/vintage/commander/pauper/brawl); usd/eur price; kw: keyword; "
    "is: (e.g. is:foil); year:; combine with spaces (AND), OR, - for NOT, parentheses, and "
    "/regex/ on text fields.\n"
    "For multi-word rules text, use a SEPARATE o: for EACH significant word — o:counter o:spell, "
    "NOT a quoted phrase like o:\"counter spell\". Bare words match the card NAME, so always put "
    "rules-text words under their own o:.\n"
    "Examples:\n"
    "cheap red removal that damages creatures -> c:r o:damage t:instant mv<=2\n"
    "blue fliers under five dollars -> c:u o:flying usd<5\n"
    "blue instants that counter spells -> c:u t:instant o:counter o:spell\n"
    "green ramp legal in commander -> c:g o:add f:commander\n"
    "legendary dragons -> t:legendary t:dragon"
)


def _clean_query(text: str) -> str:
    """Extract a single query line from a model reply (strip code fences / 'query:' wrapper)."""
    for line in text.splitlines():
        line = line.strip().strip("`").strip()
        if line.lower().startswith("query:"):
            line = line[6:].strip()
        line = line.strip("`").strip()
        if not line:
            continue
        # Unwrap a whole query the model put in quotes (e.g. `"c:r t:instant"`).
        if len(line) > 1 and line.startswith('"') and line.endswith('"'):
            line = line[1:-1].strip()
        # Close a dangling quote (small models sometimes emit o:"foo without the closing ").
        if line.count('"') % 2 == 1:
            line += '"'
        return line
    return ""


def validate_query(query: str) -> bool:
    """True if *query* parses/compiles as Scryfall syntax (does not execute it)."""
    try:
        build_search(query)
        return True
    except SearchError:
        return False


def _looks_useful(query: str) -> bool:
    """Reject a degenerate reply (e.g. a lone 'c') that validates but searches nothing useful."""
    return ":" in query or "/" in query or len(query) >= 4


async def nl_to_query(prompt: str, client: ChatClient) -> str:
    """Translate a natural-language request into a validated Scryfall query, or '' on failure."""
    messages = [{"role": "system", "content": _NL_SYSTEM},
                {"role": "user", "content": prompt}]
    for _ in range(2):
        raw = await _chat_nonempty(client, messages, retries=1, temperature=0.1, max_tokens=2500)
        query = _clean_query(raw)
        if query and _looks_useful(query) and validate_query(query):
            return query
        # Feed the failure back and ask for a correction.
        messages.append({"role": "assistant", "content": raw})
        messages.append({"role": "user", "content":
            "That wasn't valid Scryfall syntax. Reply with ONLY a corrected single-line query."})
    return ""


# --- build a deck from a prompt (#170) ----------------------------------------------------------

_COLOR_KEYWORDS = {
    "white": "W", "blue": "U", "black": "B", "red": "R", "green": "G",
    "azorius": "WU", "dimir": "UB", "rakdos": "BR", "gruul": "RG", "selesnya": "GW",
    "orzhov": "WB", "izzet": "UR", "golgari": "BG", "boros": "RW", "simic": "GU",
    "mardu": "WBR", "jeskai": "WUR", "sultai": "UBG", "abzan": "WBG", "temur": "URG",
    "bant": "WUG", "esper": "WUB", "grixis": "UBR", "jund": "BRG", "naya": "RGW",
}
_ALLOWED_LEGAL = {"legal", "restricted"}
_QTY_LINE = re.compile(r"^\s*(\d+)\s*x?\s+(.+)$", re.IGNORECASE)


def _colors_in(prompt: str) -> set[str]:
    low = prompt.lower()
    ident: set[str] = set()
    for word, colors in _COLOR_KEYWORDS.items():
        if re.search(rf"\b{word}\b", low):
            ident.update(colors)
    return ident


@dataclass
class BuildLine:
    name: str
    quantity: int
    scryfall_id: str
    price: float


@dataclass
class BuiltFromPrompt:
    name: str = ""
    decklist_text: str = ""
    lines: list[BuildLine] = field(default_factory=list)
    total_price: float = 0.0
    considered: int = 0
    empty: bool = False


async def _owned_candidates(session: AsyncSession, identity: set[str]) -> dict[str, tuple]:
    """Owned cards: lowercased name -> (name, scryfall_id, usd_price); filtered to *identity*."""
    rows = (await session.execute(
        select(Card.name, Card.oracle_id, Card.scryfall_id, Card.color_identity, Card.prices)
        .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
        .where(Card.oracle_id.is_not(None))
        .distinct(Card.oracle_id)
        .order_by(Card.oracle_id, Card.released_at.desc().nulls_last())
    )).all()
    out: dict[str, tuple] = {}
    for name, _oracle, sid, ci, prices in rows:
        if identity and not set(ci or []) <= identity:
            continue
        price = float((prices or {}).get("usd") or 0.0)
        out.setdefault(name.lower(), (name, str(sid), price))
    return out


def _parse_decklines(text: str, pool: dict[str, tuple]) -> list[BuildLine]:
    """Parse 'N Card Name' lines, keeping only cards present in the owned pool."""
    out: list[BuildLine] = []
    seen: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*• \t").strip()
        m = _QTY_LINE.match(line)
        if not m:
            continue
        qty = max(1, min(int(m.group(1)), 99))
        name = m.group(2).strip().strip("*_`\"'").strip()
        # Drop a trailing "(SET) 123" printing hint if the model added one.
        name = re.sub(r"\s*\([A-Za-z0-9]{2,6}\)\s*[A-Za-z0-9-]*$", "", name).strip()
        key = name.lower()
        entry = pool.get(key)
        if entry and key not in seen:
            seen.add(key)
            out.append(BuildLine(name=entry[0], quantity=qty, scryfall_id=entry[1], price=entry[2]))
    # Guard against the model numbering the lines (1 X, 2 Y, 3 Z …) instead of giving copy counts.
    if len(out) >= 4 and all(ln.quantity == i + 1 for i, ln in enumerate(out)):
        for ln in out:
            ln.quantity = 1
    return out


async def build_from_prompt(
    session: AsyncSession, prompt: str, client: ChatClient,
) -> BuiltFromPrompt:
    """Build a decklist from owned cards matching a natural-language request (validated)."""
    identity = _colors_in(prompt)
    pool = await _owned_candidates(session, identity)
    if not pool:
        return BuiltFromPrompt()
    names = [n for (n, _s, _p) in list(pool.values())[:50]]
    messages = [
        {"role": "system", "content":
            "Build a Magic deck using ONLY cards from the 'Owned cards' list (include lands). "
            "Each line is '<copies> <Card Name>' — copies is how many to run (1 for most, up to 4 "
            "for nonbasics, more for basics), e.g. '1 Sol Ring', '12 Mountain'. Do NOT number the "
            "lines. Output only deck lines, no commentary."},
        {"role": "user", "content": f"Request: {prompt}\n\nOwned cards:\n" + "\n".join(names)},
    ]
    text = await _chat_nonempty(client, messages, retries=3, temperature=0.4, max_tokens=8000)
    lines = _parse_decklines(text, pool)
    return BuiltFromPrompt(
        name=(prompt.strip()[:60] or "AI deck"),
        decklist_text="\n".join(f"{ln.quantity} {ln.name}" for ln in lines),
        lines=lines, total_price=round(sum(ln.price * ln.quantity for ln in lines), 2),
        considered=len(pool), empty=not text.strip(),
    )


# --- commander finder (#173) --------------------------------------------------------------------

@dataclass
class CommanderPick:
    name: str
    scryfall_id: str
    identity: list[str]
    owned_depth: int
    pitch: str = ""


async def find_commanders(
    session: AsyncSession, client: ChatClient | None = None, limit: int = 6,
) -> list[CommanderPick]:
    """Rank owned legendary creatures by how many owned, in-identity, Commander-legal cards back
    them; optionally add a one-line LLM pitch for the top picks."""
    cmd_rows = (await session.execute(
        select(Card.name, Card.oracle_id, Card.scryfall_id, Card.color_identity)
        .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
        .where(Card.type_line.ilike("legendary%creature%"))
        .distinct(Card.oracle_id)
        .order_by(Card.oracle_id, Card.released_at.desc().nulls_last())
    )).all()
    if not cmd_rows:
        return []
    owned = (await session.execute(
        select(Card.oracle_id, Card.color_identity, Card.legalities)
        .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
        .where(Card.oracle_id.is_not(None))
        .distinct(Card.oracle_id)
        .order_by(Card.oracle_id, Card.released_at.desc().nulls_last())
    )).all()
    owned_ci = [(set(ci or []), (leg or {}).get("commander")) for _o, ci, leg in owned]

    picks: list[CommanderPick] = []
    for name, _oracle, sid, ci in cmd_rows:
        ident = set(ci or [])
        depth = sum(1 for oci, legal in owned_ci if legal in _ALLOWED_LEGAL and oci <= ident)
        picks.append(CommanderPick(name, str(sid), sorted(ident), depth))
    picks.sort(key=lambda p: p.owned_depth, reverse=True)
    top = picks[:limit]

    if client and top:
        listing = "\n".join(
            f"{p.name} ({''.join(p.identity) or 'C'}, {p.owned_depth} owned in-color cards)"
            for p in top
        )
        messages = [
            {"role": "system", "content":
                "For each Magic commander listed, give a one-line pitch of its playstyle/strength. "
                "Output only 'Commander Name - pitch' lines, one per commander."},
            {"role": "user", "content": listing},
        ]
        try:
            text = await _chat_nonempty(client, messages, retries=1, temperature=0.4)
            pitches = {n.lower(): r for n, r in _parse_named_reasons(text)}
            for p in top:
                p.pitch = pitches.get(p.name.lower(), "")
        except (httpx.HTTPError, KeyError, IndexError, ValueError):
            pass
    return top


# --- upgrade planner (#174) ---------------------------------------------------------------------

@dataclass
class UpgradeItem:
    name: str
    scryfall_id: str
    price: float
    reason: str


@dataclass
class UpgradePlan:
    items: list[UpgradeItem] = field(default_factory=list)
    total: float = 0.0
    budget: float = 0.0
    empty: bool = False


def _parse_named_reasons(text: str) -> list[tuple[str, str]]:
    """Parse 'Name — reason' / 'Name - reason' lines into (name, reason), stripping list markers."""
    out: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip().lstrip("-*•·0123456789.) \t").strip()
        if not line:
            continue
        name, reason = line, ""
        for sep in (" — ", " - ", "—", " – ", ":"):
            if sep in line:
                name, reason = line.split(sep, 1)
                break
        name = name.strip().strip("*_`\"'.").strip()
        if name:
            out.append((name, reason.strip().strip("*_`").strip()))
    return out


def _validate_upgrade(card, reason, ctx, owned_oracles, remaining):
    """Vet one candidate card; return (UpgradeItem, price) or None if it should be dropped."""
    if card is None or card.oracle_id in owned_oracles:  # not real, or already owned
        return None
    # Enforce the deck's color identity + format legality (#192): drop off-identity / illegal.
    if ctx.identity and not set(card.color_identity or []).issubset(ctx.identity):
        return None
    if ctx.is_commander and (card.legalities or {}).get("commander") not in _ALLOWED_LEGAL:
        return None
    price = float((card.prices or {}).get("usd") or 0.0)
    if price <= 0 or price > remaining:
        return None
    return UpgradeItem(card.name, str(card.scryfall_id), round(price, 2), reason), price


async def plan_upgrades(
    session: AsyncSession, deck, budget: float, client: ChatClient,
) -> UpgradePlan:
    """Suggest real cards to buy to improve a deck, validated to exist + priced + within budget."""
    ctx = await deck_ai_context(session, deck)
    owned_oracles = set((await session.execute(
        select(Card.oracle_id)
        .join(CollectionCard, CollectionCard.scryfall_id == Card.scryfall_id)
    )).scalars().all())
    messages = [
        {"role": "system", "content":
            "You are a Magic: The Gathering upgrade advisor. Suggest real cards to add that "
            "improve the deck (ramp, card draw, removal, mana base, win conditions) and fit its "
            "commander/theme. " + ctx.identity_rule() + " Output up to 15 lines, each exactly "
            "'Card Name - short reason'. Real card names only. No preamble, no prices.\n\n"
            + _DECKBUILD_GUIDE},
        {"role": "user", "content": f"Budget: ${budget:.0f}\n{ctx.block()}"},
    ]
    text = await _chat_nonempty(client, messages, retries=2, temperature=0.4, max_tokens=3000)
    items: list[UpgradeItem] = []
    total = 0.0
    for name, reason in _parse_named_reasons(text):
        card = (await session.execute(
            select(Card).where(func.lower(Card.name) == name.lower())
            .order_by(Card.released_at.desc().nulls_last()).limit(1)
        )).scalars().first()
        result = _validate_upgrade(card, reason, ctx, owned_oracles, budget - total)
        if result is None:
            continue
        item, price = result
        total += price
        items.append(item)
    return UpgradePlan(items=items, total=round(total, 2), budget=budget, empty=not text.strip())


# --- deck coaching chat (#172) ------------------------------------------------------------------


async def _deck_chat_history(
    session: AsyncSession, deck_id: int
) -> tuple[DeckChatMessage | None, list[DeckChatMessage]]:
    deck_messages = list((await session.execute(
        select(DeckChatMessage).where(DeckChatMessage.deck_id == deck_id)
        .order_by(DeckChatMessage.id)
    )).scalars().all())

    if deck_messages and hasattr(deck_messages[-1], "role") and deck_messages[-1].role=="user":
        last_user_message = deck_messages.pop()
        return (last_user_message, deck_messages)

    return (None, deck_messages)


async def deck_chat(
    session: AsyncSession,
    client: ChatClient,
    history: list[dict],
    user_message: str,
    deck: Deck | None,
    collection_cards: bool = False,
    interfaceContext: str = ''
) -> str:
    """One turn of a coaching conversation, grounded in the deck's list, identity, and themes."""
    system = (
        "You are a friendly, concise Magic: The Gathering (MTG) coach. Give specific, actionable "
        "advice relevant to MTG cards, rules of the game, deckbuilding, and the like. At any time, "
        "only reference real MTG cards. Keep replies short.\n\n"
    )
    if(interfaceContext == 'deck_building'):
        system += (
            "The user is examining or building a deck, so provide advice that is relevant for MTG "
            "deck building in general or about the composition of the deck specifically or, if the "
            "user asks, suggestions of relevant replacements which further the theme or winning "
            "strategy of desk while respecting relevant format rules. If suggesting replacements, "
            "rank by how well each card advances THIS deck's plan, not by how strong it is in the "
            "abstract, and identify the card that it should replace:\n"
            "1. Cards that interact with the commander's specific ability, if there is a "
            "commander.\n"
            "2. Cards that support the deck's stated themes and creature types.\n"
            "3. Only then, cards that fill a thin role (ramp, draw, removal, win conditions).\n"
            "Do NOT suggest generic staples that do not advance the plan, however powerful. Each "
            "suggested card must name the SPECIFIC interaction (which ability, which theme) — not "
            "'good card' or 'solid value'.\n\n"
        )
        system += _DECKBUILD_GUIDE + "\n\n"
    if(deck):
        deck_context = await deck_ai_context(session, deck)
        system += " "+deck_context.identity_rule()
        system += (
            "The user has opted to share the exact details of their deck as it looks right now. "
        )
        system += " "+deck_context.block()+"\n\n"
    if(collection_cards):
        collection_context = await collection_ai_context(session)
        system += (
            "The user has specifically shared the following list of MTG cards in their collection. "
            "These cards are presumably available for use, for deck construction for example. If "
            "the user has asked for recommended cards, only suggest cards from this list."
        )
        system += " "+collection_context.block()+"\n\n"


    messages = [{"role": "system", "content": system}]
    messages += [{"role": m["role"], "content": m["content"]} for m in history]
    messages.append({"role": "user", "content": user_message})

    return await _chat_nonempty(client, messages, retries=2, temperature=0.5, max_tokens=2000)


# --- rules Q&A (#175) ---------------------------------------------------------------------------

async def answer_card_question(
    card, rulings: list[str], question: str, client: ChatClient,
    rules_context: list[str] | None = None,
) -> str:
    """Answer a rules question grounded in the card's oracle text, official rulings, and (when
    available) relevant excerpts retrieved from the Comprehensive Rules."""
    ruling_text = "\n".join(f"- {r}" for r in rulings) if rulings else "(no official rulings found)"
    context = (
        f"Card: {card.name}\n"
        f"Type: {card.type_line or ''}\n"
        f"Oracle text:\n{card.oracle_text or '(none)'}\n\n"
        f"Official rulings:\n{ruling_text}"
    )
    if rules_context:
        context += "\n\nRelevant Comprehensive Rules:\n" + "\n\n".join(rules_context)
    messages = [
        {"role": "system", "content":
            "You answer Magic: The Gathering rules questions about this card. Use its oracle text, "
            "the official rulings, the Comprehensive Rules excerpts provided, and well-established "
            "core Magic rules. Be concise and cite the relevant rule number or ruling. Do NOT "
            "invent card-specific rulings; if it's a genuine corner case not covered, say so and "
            "suggest checking a judge."},
        {"role": "user", "content": f"{context}\n\nQuestion: {question}"},
    ]
    return await _chat_nonempty(client, messages, retries=2, temperature=0.3, max_tokens=1500)







# --- Background Workers ---------------------------------------------------------------------------



async def ai_deck_chat_response_worker(
    session: AsyncSession,
    deck_id: int,
    include_deck: bool,
    include_collection: bool,
) -> int | None:
    """Start a background task to process the AI response for a user message."""
    deck = await session.get(Deck, deck_id)
    if deck is None:
        return None
    cfg = await get_config(session)
    last_user_message, prior = await _deck_chat_history(session, deck_id)
    if not last_user_message:
        return False
    history = [{"role": m.role, "content": m.content} for m in prior]
    try:
        reply = await deck_chat(
            session,
            ChatClient(cfg),
            history,
            last_user_message.content,
            deck if include_deck else None,
            include_collection,
            interfaceContext='deck_building'
        )
    except (httpx.HTTPError, KeyError, IndexError, ValueError):
        reply = ""
    # Immediately add the ai's message to the chat log
    deck_message = DeckChatMessage(deck_id=deck_id, role="assistant",
                                content=reply or "(the AI endpoint didn't respond — try again)")
    session.add(deck_message)
    await session.commit()
    return deck_message.id



async def nlq_worker(
    session: AsyncSession,
    nlqcache_id: int,
) -> int | None:
    """Start a background task to Translate a plain-English request to a Scryfall query (#171)."""
    matching_nl_to_q = await session.get(NLQCache, nlqcache_id)
    if(not matching_nl_to_q):
        return None
    cfg = await get_config(session)
    if cfg.ready and matching_nl_to_q.nl_text:
        try:
            generated = await nl_to_query(matching_nl_to_q.nl_text, ChatClient(cfg))
            matching_nl_to_q.q_text = generated
            await session.commit()
            return matching_nl_to_q.id
        except httpx.HTTPStatusError as e:
            # Check for specific HTTP errors that indicate AI service issues
            if e.response.status_code == 404:
                # For a 404 error, let the user know the AI endpoint might be misconfigured
                # But still fallback gracefully to raw search as before
                raise Exception(
                    "AI service not found (404) - please check your LLM configuration."
                ) from e
            else:
                raise Exception(f"Unexpected AI service error ({e.response.status_code}).") from e
        except Exception as e:
            raise Exception("Unexpected AI service error.") from e
    raise Exception("Unexpected AI service error. cfg.ready or matching_nl_to_q.nl_text")
