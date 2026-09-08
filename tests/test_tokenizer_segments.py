# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Accents fold and identifiers split, so prose reaches names.

The vault is mixed Italian/English: `references` and `références` must be one
term, and the query "state machine" must reach `OrderStateMachine`, which
`[^\\W_]+` hands over as the single token `orderstatemachine`.
"""
from silica.kernel.recall.lexical import STOPWORDS, STOPWORDS_FOLDED, _fold, _tokens


def test_accents_fold_to_one_term():
    assert _tokens("références") == _tokens("references")
    assert _fold("città") == "citta"


def test_folded_italian_stopwords_are_still_dropped():
    """STOPWORDS holds `perché`/`più` accented; tokens arrive folded, so the
    membership test must consult the folded set or every Italian note indexes
    its own function words."""
    assert "perche" not in _tokens("perché non funziona")
    assert "piu" not in _tokens("più veloce di prima")


def test_folded_stopword_set_covers_the_raw_one():
    assert len(STOPWORDS_FOLDED) <= len(STOPWORDS)
    assert "perche" in STOPWORDS_FOLDED and "piu" in STOPWORDS_FOLDED


def test_camel_case_yields_segments_and_the_whole_token():
    t = _tokens("the OrderStateMachine broke")
    assert "orderstatemachine" in t, "the whole name must still match verbatim"
    assert {"order", "state", "machine"} <= set(t), t


def test_acronym_run_splits_before_the_last_capital():
    assert {"html", "parser"} <= set(_tokens("HTMLParser"))


def test_digits_stay_glued_to_their_word():
    t = _tokens("base64Encode")
    assert "base64" in t and "encode" in t, t


def test_a_plain_word_contributes_no_segments():
    assert _tokens("machine") == ["machine"]


def test_single_char_segments_are_dropped():
    """`aB` splits into `a`/`B`; both are below the two-character floor."""
    assert _tokens("aB") == ["ab"]


def test_atoms_still_work():
    """Task 2's contract survives the rewrite."""
    assert "2026-09-08" in _tokens("released 2026-09-08")
