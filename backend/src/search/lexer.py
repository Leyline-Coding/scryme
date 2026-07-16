"""Tokenizer for Scryfall-style queries.

Produces a flat token stream of structural tokens (LPAREN, RPAREN, OR, AND, NOT) and ATOMs.
An ATOM is one whitespace-delimited unit such as ``c:r``, ``mv>=3``, ``name:"Black Lotus"``,
``o:/draw a card/`` or a bare word/phrase. Quotes and ``/regex/`` segments may contain spaces
and parentheses, so they are consumed whole.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto


class TokKind(Enum):
    ATOM = auto()
    LPAREN = auto()
    RPAREN = auto()
    OR = auto()
    AND = auto()
    NOT = auto()


@dataclass(frozen=True)
class Token:
    kind: TokKind
    value: str = ""


_PREFIX_KINDS = {TokKind.LPAREN, TokKind.OR, TokKind.AND, TokKind.NOT}


def _read_delimited(s: str, i: int, delim: str, escape: bool) -> tuple[str, int]:
    """Read from the opening ``delim`` at ``i`` through the matching close (inclusive)."""
    n = len(s)
    buf = [s[i]]  # opening delimiter
    i += 1
    while i < n and s[i] != delim:
        if escape and s[i] == "\\" and i + 1 < n:
            buf.append(s[i])
            buf.append(s[i + 1])
            i += 2
            continue
        buf.append(s[i])
        i += 1
    if i < n:  # closing delimiter
        buf.append(s[i])
        i += 1
    return "".join(buf), i


def _read_atom(s: str, i: int) -> tuple[str, int]:
    """Read one atom starting at index ``i``; returns (atom, next_index)."""
    n = len(s)
    buf: list[str] = []
    while i < n:
        ch = s[i]
        if ch.isspace() or ch in "()":
            break
        if ch == '"':
            chunk, i = _read_delimited(s, i, '"', escape=False)
            buf.append(chunk)
            continue
        if ch == "/":
            chunk, i = _read_delimited(s, i, "/", escape=True)
            buf.append(chunk)
            continue
        buf.append(ch)
        i += 1
    return "".join(buf), i


def tokenize(query: str) -> list[Token]:
    tokens: list[Token] = []
    i, n = 0, len(query)
    while i < n:
        ch = query[i]
        if ch.isspace():
            i += 1
            continue
        if ch == "(":
            tokens.append(Token(TokKind.LPAREN))
            i += 1
            continue
        if ch == ")":
            tokens.append(Token(TokKind.RPAREN))
            i += 1
            continue
        # A '-' negates the following term when it appears where a term may begin.
        if ch == "-" and (not tokens or tokens[-1].kind in _PREFIX_KINDS):
            tokens.append(Token(TokKind.NOT))
            i += 1
            continue
        atom, i = _read_atom(query, i)
        lowered = atom.lower()
        if lowered == "or":
            tokens.append(Token(TokKind.OR))
        elif lowered == "and":
            tokens.append(Token(TokKind.AND))
        else:
            tokens.append(Token(TokKind.ATOM, atom))
    return tokens
