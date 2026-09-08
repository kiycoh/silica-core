# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Structured tokens survive the tokenizer whole.

A date, a version, a number with a unit and a path are the terms that
discriminate in a corpus of notes and papers. `[^\\W_]+` alone shreds every
one of them into fragments the whole corpus shares.
"""
from silica.kernel.recall.lexical import _tokens


def test_iso_date_is_one_token_and_keeps_its_parts():
    t = _tokens("released 2026-09-08 finally")
    assert "2026-09-08" in t, t
    assert "2026" in t, "the bare year must still reach the document"


def test_iso_datetime_is_one_token():
    assert "2026-09-08t14:30" in _tokens("at 2026-09-08T14:30 exactly")


def test_version_is_one_token():
    assert "v0.4.0" in _tokens("pinned to v0.4.0 today")
    assert "1.2.3" in _tokens("bumped to 1.2.3")
    assert "v0.4.0-rc1" in _tokens("shipped v0.4.0-rc1")


def test_number_with_unit_stays_glued():
    t = _tokens("budget 100ms per call, 5GB resident")
    assert "100ms" in t and "5gb" in t, t


def test_bare_number_is_untouched():
    assert "42" in _tokens("exactly 42 documents")


def test_path_with_extension_is_one_token():
    assert "docs/plans/file.md" in _tokens("see docs/plans/file.md")
    assert "silica/core.py" in _tokens("edit silica/core.py now")


def test_deep_path_without_extension_is_one_token():
    assert "docs/research/papers" in _tokens("under docs/research/papers somewhere")
    assert "01-retrieval/md/notes" in _tokens("see 01-retrieval/md/notes here")


def test_prose_slash_pairs_are_not_paths():
    """`and/or`, `w/o`, `b/c` are prose, and so is a segment shorter than two
    characters. A two-segment slash pair only counts as a path when it carries
    a file extension; a deeper one needs every segment to look like a name."""
    t = _tokens("and/or semantics, w/o exception, a/b/c nonsense")
    assert "and/or" not in t, t
    assert "w/o" not in t, t
    assert "a/b/c" not in t, t


def test_stopwords_and_short_words_still_go():
    assert _tokens("the and of it") == []
