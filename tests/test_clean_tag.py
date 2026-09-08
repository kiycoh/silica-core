# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

from silica.kernel.write.frontmatter import clean_tag


def test_clean_tag_keeps_leading_digit_fused_to_word():
    # A20: a digit that is part of a word must survive.
    assert clean_tag("3d") == "3d"
    assert clean_tag("2fa") == "2fa"
    assert clean_tag("3D-Printing") == "3d-printing"
    assert clean_tag("web3") == "web3"  # trailing digit already safe


def test_clean_tag_transliterates_accents_not_truncates():
    # Italian vault: accented vowels must map to ASCII, not be deleted
    # ("scalabilità"→"scalabilit" was the bug).
    assert clean_tag("scalabilità") == "scalabilita"
    assert clean_tag("similarità coseno") == "similarita-coseno"
    assert clean_tag("città") == "citta"
    assert clean_tag("caffè") == "caffe"


def test_clean_tag_strips_list_ordinal():
    # Real numbered-list ordinals (digit + separator + space) are still stripped.
    assert clean_tag("1. Machine Learning") == "machine-learning"
    assert clean_tag("2) Notes") == "notes"
