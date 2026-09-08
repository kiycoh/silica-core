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


def test_camel_case_is_not_segmented():
    """Measured, not an oversight. Segments let a prose query reach a name,
    which is right in a code vault; on the 254-paper bench corpus abstracts
    and introductions name every concept, so segments inflated the front
    matter and pushed the answering section out of a `per_doc` of 2."""
    assert _tokens("the OrderStateMachine broke") == ["orderstatemachine", "broke"]
    assert _tokens("HTMLParser") == ["htmlparser"]


def test_atoms_still_work():
    """Task 2's contract survives the rewrite."""
    assert "2026-09-08" in _tokens("released 2026-09-08")
