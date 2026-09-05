"""`_balanced` (kernel/link/ast.py): the delimiter counts read markdown-it's
text pieces so `$$` inside code stays invisible; the pieces must not be glued
together, or adjacent inline math forms a fake `$$` across a token boundary."""
from silica.kernel.link.ast import _balanced


def test_inline_math_on_adjacent_list_items_is_not_an_unbalanced_block():
    # Two of Lezione 2's notes were deferred at WRITE with this exact shape
    # (2026-09-05): "...= 0$" + "$f (x + y)..." counted as one "$$".
    body = "Proprietà:\n- $f (x) = 0 \\Rightarrow x = 0$\n- $f (x + y) \\leq f (x) + f (y)$\n"
    assert _balanced(body) == []


def test_a_real_unclosed_display_block_is_still_caught():
    assert _balanced("intro\n\n$$\n a = b\n\nfine") == ["unbalanced $$ block"]
    assert _balanced("intro\n\n$$\n a = b\n$$\n\nfine") == []
