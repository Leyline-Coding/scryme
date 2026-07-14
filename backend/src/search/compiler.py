"""Compile a parsed AST into a SQLAlchemy boolean expression over the ``cards`` table.

Each filter field has a handler that returns a ColumnElement. Regex (``/…/``) uses Postgres
``~*`` (case-insensitive POSIX) — close to Scryfall's RE2 flavor but not identical; see the
project wiki for caveats. Numeric fields treat the ``:`` operator as ``=``.
"""

from __future__ import annotations

import datetime
import operator
from collections.abc import Callable

from sqlalchemy import Float, and_, cast, func, not_, or_, select
from sqlalchemy.sql.elements import ColumnElement

from src.models import Card, CollectionCard
from src.search.ast import And, Node, Not, Or, Term
from src.search.colors import ColorParseError, parse_colors
from src.search.errors import SearchError

_COMPARATORS: dict[str, Callable] = {
    ":": operator.eq,
    "=": operator.eq,
    "!=": operator.ne,
    "<": operator.lt,
    "<=": operator.le,
    ">": operator.gt,
    ">=": operator.ge,
}

_RARITY_RANK = {"common": 0, "uncommon": 1, "rare": 2, "mythic": 3, "special": 4, "bonus": 5}
_RARITY_ABBR = {"c": "common", "u": "uncommon", "r": "rare", "m": "mythic", "s": "special",
                "b": "bonus"}

_NUMERIC_RE = r"^-?[0-9]+(\.[0-9]+)?$"


# --- helpers ---------------------------------------------------------------

def _like_escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _text_match(col, term: Term) -> ColumnElement:
    if term.regex:
        return col.op("~*")(term.value)
    return col.ilike(f"%{_like_escape(term.value)}%", escape="\\")


def _as_float(term: Term) -> float:
    try:
        return float(term.value)
    except ValueError as exc:
        raise SearchError(f"“{term.field}” needs a number, got “{term.value}”.") from exc


def _comparator(term: Term) -> Callable:
    cmp = _COMPARATORS.get(term.op)
    if cmp is None:
        raise SearchError(f"Operator “{term.op}” is not valid for {term.field}.")
    return cmp


# --- field handlers --------------------------------------------------------

def _name(term: Term) -> ColumnElement:
    return _text_match(Card.name, term)


def _oracle(term: Term) -> ColumnElement:
    return _text_match(Card.oracle_text, term)


def _type(term: Term) -> ColumnElement:
    return _text_match(Card.type_line, term)


def _artist(term: Term) -> ColumnElement:
    return _text_match(Card.raw["artist"].astext, term)


def _watermark(term: Term) -> ColumnElement:
    return _text_match(Card.raw["watermark"].astext, term)


def _border(term: Term) -> ColumnElement:
    return Card.raw["border_color"].astext == term.value.lower()


def _frame(term: Term) -> ColumnElement:
    return Card.raw["frame"].astext == term.value.lower()


def _set_type(term: Term) -> ColumnElement:
    return Card.raw["set_type"].astext == term.value.lower()


def _stamp(term: Term) -> ColumnElement:
    return Card.raw["security_stamp"].astext == term.value.lower()


def _game(term: Term) -> ColumnElement:
    # Card.raw["games"] is a JSON array like ["paper", "mtgo", "arena"].
    return Card.raw["games"].contains([term.value.lower()])


def _mana(term: Term) -> ColumnElement:
    # Approximate m:: require the cost to contain each requested symbol.
    raw = term.value.lower()
    symbols = [s for s in raw.replace("{", " ").replace("}", " ").split() if s] or [raw]
    return and_(*[Card.mana_cost.ilike(f"%{{{_like_escape(s)}}}%", escape="\\") for s in symbols])


def _color_clause(col, term: Term) -> ColumnElement:
    try:
        letters, special = parse_colors(term.value)
    except ColorParseError as exc:
        raise SearchError(str(exc)) from exc

    size = func.coalesce(func.array_length(col, 1), 0)
    if special == "colorless":
        return size == 0
    if special == "multicolor":
        return size >= 2

    wanted = sorted(letter.upper() for letter in letters)  # Scryfall stores WUBRG uppercase
    contains = col.contains(wanted)       # @>  has all requested
    contained = col.contained_by(wanted)  # <@  has only requested
    n = len(wanted)
    op = term.op
    if op in (":", ">="):
        return contains
    if op == "=":
        return and_(contains, contained)
    if op == "!=":
        return not_(and_(contains, contained))
    if op == "<=":
        return contained
    if op == ">":
        return and_(contains, size > n)
    if op == "<":
        return and_(contained, size < n)
    raise SearchError(f"Operator “{op}” is not valid for {term.field}.")


def _color(term: Term) -> ColumnElement:
    return _color_clause(Card.colors, term)


def _identity(term: Term) -> ColumnElement:
    return _color_clause(Card.color_identity, term)


def _numeric_col(col, term: Term) -> ColumnElement:
    return _comparator(term)(col, _as_float(term))


def _mv(term: Term) -> ColumnElement:
    return _numeric_col(Card.cmc, term)


def _string_numeric(col, term: Term) -> ColumnElement:
    # power/toughness/loyalty are stored as text (may be '*'); guard before casting.
    guard = col.op("~")(_NUMERIC_RE)
    return and_(guard, _comparator(term)(cast(col, Float), _as_float(term)))


def _power(term: Term) -> ColumnElement:
    return _string_numeric(Card.power, term)


def _toughness(term: Term) -> ColumnElement:
    return _string_numeric(Card.toughness, term)


def _loyalty(term: Term) -> ColumnElement:
    return _string_numeric(Card.loyalty, term)


def _rarity(term: Term) -> ColumnElement:
    raw = term.value.lower()
    name = _RARITY_ABBR.get(raw, raw)
    if name not in _RARITY_RANK:
        raise SearchError(f"Unknown rarity “{term.value}”.")
    if term.op in (":", "="):
        return Card.rarity == name
    if term.op == "!=":
        return Card.rarity != name
    cmp = _comparator(term)
    target = _RARITY_RANK[name]
    allowed = [r for r, rank in _RARITY_RANK.items() if cmp(rank, target)]
    return Card.rarity.in_(allowed)


def _set(term: Term) -> ColumnElement:
    val = term.value.lower()
    return Card.set_code != val if term.op == "!=" else Card.set_code == val


def _cn(term: Term) -> ColumnElement:
    return Card.collector_number != term.value if term.op == "!=" \
        else Card.collector_number == term.value


def _lang(term: Term) -> ColumnElement:
    return Card.lang == term.value.lower()


def _layout(term: Term) -> ColumnElement:
    return Card.layout == term.value.lower()


def _keyword(term: Term) -> ColumnElement:
    joined = func.lower(func.array_to_string(Card.keywords, "\x1f"))
    return joined.like(f"%{_like_escape(term.value.lower())}%", escape="\\")


def _tag(term: Term) -> ColumnElement:
    # Tags live on collection_card (owned stacks); match printings with a stack carrying the tag.
    tagged = select(CollectionCard.scryfall_id).where(
        CollectionCard.tags.contains([term.value.lower()])
    )
    return Card.scryfall_id.in_(tagged)


def _location(term: Term) -> ColumnElement:
    # Physical storage location lives on collection_card; match printings with a stack there
    # (case-insensitive substring, so `loc:box` matches "Box A").
    located = select(CollectionCard.scryfall_id).where(
        CollectionCard.location.ilike(f"%{term.value}%")
    )
    return Card.scryfall_id.in_(located)


_IS_LAYOUTS = {
    "split": ["split"],
    "flip": ["flip"],
    "transform": ["transform"],
    "mdfc": ["modal_dfc"],
    "dfc": ["transform", "modal_dfc"],
    "meld": ["meld"],
    "leveler": ["leveler"],
    "saga": ["saga"],
    "adventure": ["adventure"],
}


def _is(term: Term) -> ColumnElement:
    val = term.value.lower()
    if val in _IS_LAYOUTS:
        return Card.layout.in_(_IS_LAYOUTS[val])
    if val == "graded":
        # Owned copies with grading metadata (#179).
        graded = select(CollectionCard.scryfall_id).where(
            CollectionCard.grade_company.isnot(None)
        )
        return Card.scryfall_id.in_(graded)
    # Fall back to a boolean flag on the raw card object (foil, promo, reserved, reprint, ...).
    return Card.raw[val].astext == "true"


def _format(term: Term) -> ColumnElement:
    fmt = term.value.lower()
    return Card.legalities[fmt].astext.in_(["legal", "restricted"])


def _price(key: str):
    def handler(term: Term) -> ColumnElement:
        col = cast(Card.prices[key].astext, Float)
        return and_(Card.prices[key].astext.isnot(None), _comparator(term)(col, _as_float(term)))
    return handler


def _year(term: Term) -> ColumnElement:
    try:
        year = int(term.value)
    except ValueError as exc:
        raise SearchError(f"“year” needs a 4-digit year, got “{term.value}”.") from exc
    return _comparator(term)(func.extract("year", Card.released_at), year)


def _date(term: Term) -> ColumnElement:
    try:
        when = datetime.date.fromisoformat(term.value)
    except ValueError as exc:
        raise SearchError(f"“date” needs YYYY-MM-DD, got “{term.value}”.") from exc
    return _comparator(term)(Card.released_at, when)


_HANDLERS: dict[str, Callable[[Term], ColumnElement]] = {
    "name": _name,
    "oracle": _oracle,
    "type": _type,
    "artist": _artist,
    "mana": _mana,
    "color": _color,
    "identity": _identity,
    "mv": _mv,
    "power": _power,
    "toughness": _toughness,
    "loyalty": _loyalty,
    "rarity": _rarity,
    "set": _set,
    "cn": _cn,
    "lang": _lang,
    "layout": _layout,
    "keyword": _keyword,
    "is": _is,
    "format": _format,
    "usd": _price("usd"),
    "eur": _price("eur"),
    "tix": _price("tix"),
    "year": _year,
    "date": _date,
    "watermark": _watermark,
    "border": _border,
    "frame": _frame,
    "set_type": _set_type,
    "stamp": _stamp,
    "game": _game,
    "tag": _tag,
    "location": _location,
}


def compile_term(term: Term) -> ColumnElement:
    handler = _HANDLERS.get(term.field)
    if handler is None:  # pragma: no cover - parser already rejects unknown fields
        raise SearchError(f"Unknown filter “{term.field}”.")
    if term.regex and term.field not in ("name", "oracle", "type", "artist", "watermark"):
        raise SearchError(f"Regex is not supported for “{term.field}”.")
    return handler(term)


def compile_node(node: Node) -> ColumnElement:
    if isinstance(node, Term):
        return compile_term(node)
    if isinstance(node, Not):
        return not_(compile_node(node.operand))
    if isinstance(node, And):
        return and_(*[compile_node(n) for n in node.operands])
    if isinstance(node, Or):
        return or_(*[compile_node(n) for n in node.operands])
    raise SearchError("Malformed query.")  # pragma: no cover
