"""Recursive-descent parser: tokens -> AST.

Grammar (precedence low -> high)::

    or_expr  := and_expr ( "OR" and_expr )*
    and_expr := unary ( ["AND"] unary )*      # juxtaposition = implicit AND
    unary    := "-" unary | primary
    primary  := "(" or_expr ")" | ATOM

Each ATOM decodes to a :class:`~src.search.ast.Term`. A ``key:value`` atom maps the key through
:data:`FIELD_ALIASES`; a bare word/phrase becomes a name search; a bare ``/regex/`` becomes a
name regex.
"""

from __future__ import annotations

from src.search.ast import And, Node, Not, Or, Term
from src.search.errors import SearchError
from src.search.lexer import Token, TokKind, tokenize

# Canonical field name for each accepted key/alias.
FIELD_ALIASES = {
    "name": "name",
    "o": "oracle", "oracle": "oracle",
    "t": "type", "type": "type",
    "c": "color", "color": "color", "colour": "color",
    "id": "identity", "identity": "identity", "ci": "identity",
    "m": "mana", "mana": "mana",
    "mv": "mv", "cmc": "mv", "manavalue": "mv",
    "pow": "power", "power": "power",
    "tou": "toughness", "toughness": "toughness",
    "loy": "loyalty", "loyalty": "loyalty",
    "r": "rarity", "rarity": "rarity",
    "s": "set", "set": "set", "e": "set", "edition": "set",
    "cn": "cn", "number": "cn",
    "is": "is",
    "f": "format", "format": "format", "legal": "format",
    "usd": "usd", "eur": "eur", "tix": "tix",
    "lang": "lang", "language": "lang",
    "kw": "keyword", "keyword": "keyword",
    "year": "year", "date": "date",
    "layout": "layout",
    "a": "artist", "artist": "artist",
    "wm": "watermark", "watermark": "watermark",
    "border": "border",
    "frame": "frame",
    "game": "game",
    "st": "set_type", "settype": "set_type",
    "stamp": "stamp",
    "tag": "tag", "tags": "tag",
}

_TWO_CHAR_OPS = ("!=", ">=", "<=")
_ONE_CHAR_OPS = (":", "=", "<", ">")


def _split_atom(atom: str) -> tuple[str | None, str, str]:
    """Return (key, op, value). key is None for a bare word / bare regex."""
    # Bare regex matches the card name.
    if atom.startswith("/") and atom.endswith("/") and len(atom) >= 2:
        return None, "~", atom[1:-1]

    # Find the operator that separates an optional key from its value. The key is the leading
    # run of letters; the operator is the first op token right after it.
    idx = 0
    while idx < len(atom) and (atom[idx].isalpha()):
        idx += 1
    if idx > 0 and idx < len(atom):
        two = atom[idx:idx + 2]
        if two in _TWO_CHAR_OPS:
            return atom[:idx].lower(), two, atom[idx + 2:]
        if atom[idx] in _ONE_CHAR_OPS:
            return atom[:idx].lower(), atom[idx], atom[idx + 1:]

    return None, ":", atom  # bare word -> name contains


def _unquote(value: str) -> tuple[str, bool]:
    """Strip surrounding quotes or /…/ markers. Returns (value, is_regex)."""
    if len(value) >= 2 and value.startswith("/") and value.endswith("/"):
        return value[1:-1], True
    if len(value) >= 2 and value.startswith('"') and value.endswith('"'):
        return value[1:-1], False
    return value, False


def atom_to_term(atom: str) -> Term:
    key, op, raw_value = _split_atom(atom)
    if key is None and op == "~":  # bare regex -> name regex
        return Term(field="name", op="~", value=raw_value, regex=True)

    value, is_regex = _unquote(raw_value)
    if key is None:
        return Term(field="name", op=":", value=value, regex=is_regex)

    field = FIELD_ALIASES.get(key)
    if field is None:
        raise SearchError(f"Unknown filter “{key}:”. See the Scryfall syntax guide.")
    if is_regex:
        op = "~"
    return Term(field=field, op=op, value=value, regex=is_regex)


class _Parser:
    def __init__(self, tokens: list[Token]):
        self._tokens = tokens
        self._pos = 0

    def _peek(self) -> Token | None:
        return self._tokens[self._pos] if self._pos < len(self._tokens) else None

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        self._pos += 1
        return tok

    def parse(self) -> Node:
        node = self._or()
        if self._peek() is not None:
            raise SearchError("Unbalanced parentheses in query.")
        return node

    def _or(self) -> Node:
        operands = [self._and()]
        while (tok := self._peek()) and tok.kind is TokKind.OR:
            self._advance()
            operands.append(self._and())
        return operands[0] if len(operands) == 1 else Or(tuple(operands))

    def _and(self) -> Node:
        operands = [self._unary()]
        while (tok := self._peek()) and tok.kind not in (TokKind.OR, TokKind.RPAREN):
            if tok.kind is TokKind.AND:
                self._advance()  # explicit AND is optional sugar
            operands.append(self._unary())
        return operands[0] if len(operands) == 1 else And(tuple(operands))

    def _unary(self) -> Node:
        tok = self._peek()
        if tok is None:
            raise SearchError("Unexpected end of query.")
        if tok.kind is TokKind.NOT:
            self._advance()
            return Not(self._unary())
        if tok.kind is TokKind.LPAREN:
            self._advance()
            node = self._or()
            closing = self._peek()
            if closing is None or closing.kind is not TokKind.RPAREN:
                raise SearchError("Unbalanced parentheses in query.")
            self._advance()
            return node
        if tok.kind is TokKind.ATOM:
            self._advance()
            return atom_to_term(tok.value)
        raise SearchError("Unexpected token in query.")


def parse(query: str) -> Node | None:
    """Parse a query string into an AST, or None if the query is empty."""
    tokens = tokenize(query)
    if not tokens:
        return None
    return _Parser(tokens).parse()
