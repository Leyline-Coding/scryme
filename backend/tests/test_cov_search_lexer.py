"""Unit coverage for src/search/lexer.py — tokenization."""

from src.search.lexer import TokKind, tokenize


def _kinds(query):
    return [t.kind for t in tokenize(query)]


def test_structural_tokens():
    # NOT only triggers at a term-start position (start / after LPAREN/OR/AND/NOT).
    toks = tokenize("-t:instant (c:r OR c:u)")
    kinds = [t.kind for t in toks]
    assert TokKind.NOT in kinds
    assert TokKind.LPAREN in kinds
    assert TokKind.RPAREN in kinds
    assert TokKind.OR in kinds


def test_and_keyword():
    toks = tokenize("c:r AND c:u")
    assert [t.kind for t in toks] == [TokKind.ATOM, TokKind.AND, TokKind.ATOM]


def test_or_keyword_case_insensitive():
    assert tokenize("a or b")[1].kind is TokKind.OR


def test_quoted_atom_consumed_whole():
    # A quoted phrase (with a space) stays a single ATOM including the quotes.
    toks = tokenize('name:"Black Lotus"')
    assert len(toks) == 1
    assert toks[0].kind is TokKind.ATOM
    assert toks[0].value == 'name:"Black Lotus"'


def test_regex_atom_with_escaped_delimiter():
    # /a\/b/ — the escaped slash must not close the regex early.
    toks = tokenize(r"o:/a\/b/")
    assert len(toks) == 1
    assert toks[0].value == r"o:/a\/b/"


def test_regex_atom_with_spaces_and_parens():
    toks = tokenize("o:/draw a card (or two)/")
    assert len(toks) == 1
    assert toks[0].value == "o:/draw a card (or two)/"


def test_leading_dash_is_not():
    # A '-' only negates where a term may begin (start or after prefix token).
    assert tokenize("-goblin")[0].kind is TokKind.NOT
    # A '-' inside a word is part of the atom (Niv-Mizzet).
    toks = tokenize("Niv-Mizzet")
    assert toks[0].kind is TokKind.ATOM
    assert toks[0].value == "Niv-Mizzet"


def test_unterminated_quote_reads_to_end():
    toks = tokenize('name:"unterminated')
    assert toks[0].value == 'name:"unterminated'
