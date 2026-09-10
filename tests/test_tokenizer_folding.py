# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Accents fold, so an Italian query and an English corpus meet.

The vault is mixed Italian/English: `references` and `références` must be one
term. Identifier segmentation was measured on the bench corpus and left out;
see `test_camel_case_is_not_segmented`.
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


def test_camel_case_is_indexed_whole_and_segmented():
    """The whole is a term, for an exact name, and so is each word, for a
    prose query. Segmentation was kept out until 2026-09-10 on a
    measurement over the 254-paper corpus (front matter inflated); the
    BEIR replay of that day (public/benchmarks.md) is the measurement that
    let it in for the code index."""
    assert set(_tokens("the OrderStateMachine broke")) == {"orderstatemachine", "broke", "order", "state", "machine"}
    assert set(_tokens("HTMLParser")) == {"htmlparser", "html", "parser"}


def test_atoms_still_work():
    """Task 2's contract survives the rewrite."""
    assert "2026-09-08" in _tokens("released 2026-09-08")
