"""Unit tests for the Mana/Keyrune symbol renderers."""

from src.symbols import mana_symbols, set_symbol


def test_mana_basic_cost():
    out = str(mana_symbols("{2}{W}{U}"))
    assert 'class="ms ms-2 ms-cost"' in out
    assert 'class="ms ms-w ms-cost"' in out
    assert 'class="ms ms-u ms-cost"' in out


def test_mana_hybrid_phyrexian_and_specials():
    assert "ms-wu" in str(mana_symbols("{W/U}"))
    assert "ms-2w" in str(mana_symbols("{2/W}"))
    assert "ms-gp" in str(mana_symbols("{G/P}"))
    assert "ms-tap" in str(mana_symbols("{T}"))
    assert "ms-untap" in str(mana_symbols("{Q}"))


def test_mana_preserves_and_escapes_surrounding_text():
    out = str(mana_symbols("Add {G}."))
    assert out.startswith("Add ")
    assert out.endswith(".")
    # Non-symbol HTML is escaped, not emitted raw.
    assert "&lt;b&gt;" in str(mana_symbols("a <b> {R}"))
    assert "<b>" not in str(mana_symbols("a <b> {R}"))


def test_mana_empty_and_plain():
    assert str(mana_symbols("")) == ""
    assert str(mana_symbols(None)) == ""
    assert str(mana_symbols("no symbols here")) == "no symbols here"


def test_set_symbol_rarity_and_unknown():
    out = str(set_symbol("MH2", "rare"))
    assert 'class="ss ss-mh2 ss-rare"' in out
    # Unknown rarity -> no rarity modifier class.
    assert str(set_symbol("MH2", "bogus")) == str(set_symbol("MH2"))
    assert "ss-bogus" not in str(set_symbol("MH2", "bogus"))


def test_set_symbol_empty():
    assert str(set_symbol("")) == ""
    assert str(set_symbol(None)) == ""
