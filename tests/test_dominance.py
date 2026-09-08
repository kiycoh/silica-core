# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""`dominance` — how distinguishable the top hit is from the runner-up.

Orthogonal to `coverage`: coverage asks whether the corpus covers the query,
dominance asks whether one passage answers it better than the next.
"""
from silica.kernel.recall.lexical import dominance


def test_no_runner_up_has_no_ratio():
    """A lone hit is not infinitely confident — there is nothing to compare."""
    assert dominance([]) is None
    assert dominance([3.0]) is None


def test_ratio_is_top_over_runner_up():
    assert dominance([6.0, 2.0, 1.0]) == 3.0


def test_a_flat_pool_reads_as_one():
    assert dominance([2.0, 2.0, 2.0]) == 1.0


def test_zero_runner_up_has_no_ratio():
    """core.py appends dense-only hits at score 0.0. Dividing by one would
    manufacture certainty out of the weakest possible evidence."""
    assert dominance([4.0, 0.0]) is None


def test_negative_runner_up_has_no_ratio():
    assert dominance([4.0, -1.0]) is None
