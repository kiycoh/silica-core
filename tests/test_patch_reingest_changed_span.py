# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""A re-ingest whose snippet carries a formula or constant the note does not
have is not a duplicate.

Manual pass 2026-09-04: the lesson changed beta_2 from 0.999 to 0.99, the
model proposed the new paragraph, the gate scored it 2/2 grounded, and the
ledger leg of `_execute_patch` skipped it as "duplicate" because the note
already said the heading and was authored by that source. The note kept the
old constant in silence. Spans the note already has are the only test the
patch path can run without a judge; a prose-only snippet keeps the skip.
"""
from __future__ import annotations

from silica.kernel.write.bulk import execute_operations
from silica.kernel.write.ops import Op, OpType
from silica.kernel.write.provenance import append_record

NOTE = (
    "---\nAI: true\nlast modified: 2026-09-04\n---\n\n# Default hyperparameters\n\n"
    "The defaults are $\\beta_1 = 0.9$, $\\beta_2 = 0.999$ and $\\epsilon = 10^{-8}$.\n"
)


def _patch(snippet: str) -> Op:
    return Op(op=OpType.patch, heading="Default hyperparameters", source_basename="lez.md",
              path="concepts/Default hyperparameters.md", snippet=snippet, hub="concepts")


def _seed(tmp_vault):
    from silica.config import CONFIG
    path = tmp_vault.note("concepts/Default hyperparameters.md", NOTE)
    append_record("lez.md", "sha1", "run1", ["concepts/Default hyperparameters"],
                  vault_path=CONFIG.vault_path)
    return path


def test_reingest_with_a_changed_constant_lands(tmp_vault):
    path = _seed(tmp_vault)
    res = execute_operations([_patch(
        "The defaults are $\\beta_1 = 0.9$, $\\beta_2 = 0.99$ and $\\epsilon = 10^{-6}$.")])
    assert res.ok, res.failed
    assert "skipped" not in res.results[0]
    body = tmp_vault.read(path)
    assert "\\beta_2 = 0.99$" in body and "\\beta_2 = 0.999$" in body  # appended, v1 kept


def test_reingest_with_the_same_spans_is_still_a_duplicate(tmp_vault):
    path = _seed(tmp_vault)
    before = tmp_vault.read(path)
    res = execute_operations([_patch(
        "Restated: the defaults are $\\beta_2 = 0.999$ and $\\epsilon = 10^{-8}$.")])
    assert res.ok
    assert res.results[0].get("skipped") == "duplicate"
    assert "Restated" not in tmp_vault.read(path)
    assert tmp_vault.read(path).count("Default hyperparameters") == before.count("Default hyperparameters")


def test_prose_only_reingest_keeps_the_skip(tmp_vault):
    path = _seed(tmp_vault)
    res = execute_operations([_patch("The paper recommends these defaults for most tasks.")])
    assert res.ok
    assert res.results[0].get("skipped") == "duplicate"
    assert "most tasks" not in tmp_vault.read(path)
