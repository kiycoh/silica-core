# SPDX-License-Identifier: MIT
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


def test_version_needs_its_v():
    assert "v0.4.0" in _tokens("pinned to v0.4.0 today")
    assert "v0.4.0-rc1" in _tokens("shipped v0.4.0-rc1")
    assert "v1.2" in _tokens("still on v1.2")


def test_a_bare_decimal_is_not_a_version():
    """Measured on the 254 bench papers: `\\d+\\.\\d+` made every section
    number and every results-table decimal a term — 84% of all atoms, at
    near-zero idf — and the inflated document lengths moved the right paper
    off the top of two acceptance questions. A version carries its `v`."""
    assert "3.1" not in _tokens("see section 3.1 above")
    assert "0.025" not in _tokens("p = 0.025 overall")
    assert "1.2.3" not in _tokens("bumped to 1.2.3")


def test_hex_runs_are_not_shredded_into_units():
    """`\\d+[A-Za-z]{1,4}` with no left guard chopped MinerU asset hashes into
    dozens of fake atoms (`00923b`, `4834be`, `49cc`)."""
    t = _tokens("images/e00923b4834be49cc9b8f397da9823d0.jpg")
    assert not any(a in t for a in ("00923b", "4834be", "49cc")), t


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
