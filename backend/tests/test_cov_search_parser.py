"""Unit coverage for src/search/parser.py — atom decoding + recursive-descent structure."""

import pytest
from src.search.ast import And, Not, Or, Term
from src.search.errors import SearchError
from src.search.parser import atom_to_term, parse


def test_bare_word_and_quoted():
    assert atom_to_term("goblin") == Term("name", ":", "goblin", False)
    assert atom_to_term('"Black Lotus"') == Term("name", ":", "Black Lotus", False)


def test_bare_regex_is_name_regex():
    assert atom_to_term("/^Gob/") == Term("name", "~", "^Gob", True)


def test_keyed_regex_forces_regex_op():
    assert atom_to_term("o:/draw/") == Term("oracle", "~", "draw", True)


def test_two_and_one_char_operators():
    assert atom_to_term("mv!=2") == Term("mv", "!=", "2", False)
    assert atom_to_term("mv>=2") == Term("mv", ">=", "2", False)
    assert atom_to_term("mv<=2") == Term("mv", "<=", "2", False)
    assert atom_to_term("usd>5") == Term("usd", ">", "5", False)
    assert atom_to_term("usd<5") == Term("usd", "<", "5", False)
    assert atom_to_term("mv=2") == Term("mv", "=", "2", False)


def test_unknown_filter_raises():
    with pytest.raises(SearchError):
        atom_to_term("bogus:value")


def test_explicit_and_is_optional_sugar():
    node = parse("c:r AND c:u")
    assert isinstance(node, And)
    assert len(node.operands) == 2


def test_implicit_and():
    node = parse("c:r c:u")
    assert isinstance(node, And)


def test_or_and_negation_and_grouping():
    assert isinstance(parse("c:r OR c:u"), Or)
    neg = parse("-t:creature")
    assert isinstance(neg, Not)
    grouped = parse("(c:r OR c:u) t:instant")
    assert isinstance(grouped, And)
    assert isinstance(grouped.operands[0], Or)


def test_empty_query_is_none():
    assert parse("") is None
    assert parse("   ") is None


def test_dash_alone_unexpected_end():
    # '-' becomes a NOT with no operand -> "Unexpected end of query."
    with pytest.raises(SearchError):
        parse("-")


def test_leading_operator_token_is_unexpected():
    # A bare OR at a term position hits the "Unexpected token" branch.
    with pytest.raises(SearchError):
        parse("OR")


def test_unbalanced_parens():
    with pytest.raises(SearchError):
        parse("(c:r")
    with pytest.raises(SearchError):
        parse("c:r)")
