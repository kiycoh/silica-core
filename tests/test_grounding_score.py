"""M1 (docs/specs/epistemic-state.md): the grounding verdict the gate already
computes is persisted per note in the provenance ledger and rendered in the
recall header. On the default (paraphrasing) profile only math/code spans are
gateable, so a note with zero checkable spans must read `n/a`, never 100%.
"""
from __future__ import annotations

import json
import types

import pytest

from silica.kernel.write.provenance import (
    DEFAULT_PROVENANCE_FILENAME,
    append_record,
    grounding_by_note,
    grounding_counts,
)
from silica.kernel.write.validate import validate_operations


@pytest.fixture(autouse=True)
def _low_snippet_floor(monkeypatch):
    monkeypatch.setenv("SILICA_MIN_WRITE_SNIPPET_CHARS", "1")


SRC = "Adam uses $m_t = \\beta_1 m_{t-1} + (1 - \\beta_1) g_t$ for the first moment."
BODY = ("First moment: $m_t = \\beta_1 m_{t-1} + (1 - \\beta_1) g_t$. Stability constant: "
        "$\\epsilon_{stability} = 10^{-8}$ for robustness.")


# --- pure function -----------------------------------------------------------

def test_grounding_counts_total_and_ungrounded():
    assert grounding_counts(BODY, SRC) == (2, 1)


def test_grounding_counts_zero_when_nothing_gateable():
    assert grounding_counts("plain prose, no markup", "plain source") == (0, 0)


# --- validate_operations -----------------------------------------------------

def _payload(excerpt: str) -> dict:
    return {
        "schema_version": 1,
        "batches": [{
            "inbox_file": "/inbox/lez.md",
            "concepts": [{
                "name": "Adam Optimizer",
                "action_hint": "create",
                "inbox_excerpt": excerpt,
                "vault_collision": None,
            }],
        }],
    }


def test_validate_reports_grounding_for_accepted_ops(tmp_vault):
    op = {"op": "write", "path": "Corso/Adam Optimizer.md", "heading": "Adam Optimizer",
          "source_basename": "lez.md", "snippet": BODY}
    grounding: list[dict] = []
    validated, rejected = validate_operations(
        [op], [_payload(SRC)], "Corso", grounding_out=grounding)
    assert not rejected  # the gate also adds the hub note; only the sourced op is scored
    assert grounding == [{
        "path": "Corso/Adam Optimizer.md", "heading": "Adam Optimizer",
        "source_basename": "lez.md", "spans": 2, "ungrounded": 1, "profile": "default",
    }]


def test_validate_grounding_omits_rejected_ops(tmp_vault):
    op = {"op": "write", "path": "../outside.md", "heading": "X",
          "source_basename": "lez.md", "snippet": BODY}
    grounding: list[dict] = []
    validate_operations([op], [_payload(SRC)], "Corso", grounding_out=grounding)
    assert grounding == []


# --- ledger ------------------------------------------------------------------

def test_append_record_stores_grounding_per_note(tmp_path):
    append_record("lez.md", "sha1", "run1", ["Corso/Adam Optimizer"],
                  vault_path=str(tmp_path), date="2026-09-04",
                  grounding={"Corso/Adam Optimizer": {"spans": 2, "ungrounded": 1,
                                                     "profile": "default"}})
    raw = json.loads((tmp_path / DEFAULT_PROVENANCE_FILENAME).read_text(encoding="utf-8"))
    assert raw[0]["grounding"] == {
        "Corso/Adam Optimizer": {"spans": 2, "ungrounded": 1, "profile": "default"}}
    assert grounding_by_note(vault_path=str(tmp_path)) == {
        "corso/adam optimizer": {"spans": 2, "ungrounded": 1, "profile": "default"}}


def test_append_record_without_grounding_keeps_legacy_shape(tmp_path):
    append_record("lez.md", "sha1", "run1", ["N"], vault_path=str(tmp_path), date="2026-09-04")
    raw = json.loads((tmp_path / DEFAULT_PROVENANCE_FILENAME).read_text(encoding="utf-8"))
    assert "grounding" not in raw[0]


def test_grounding_by_note_latest_record_wins(tmp_path):
    append_record("lez.md", "sha1", "run1", ["N"], vault_path=str(tmp_path),
                  grounding={"N": {"spans": 2, "ungrounded": 2, "profile": "default"}})
    append_record("lez.md", "sha2", "run2", ["N"], vault_path=str(tmp_path),
                  grounding={"N": {"spans": 2, "ungrounded": 0, "profile": "default"}})
    assert grounding_by_note(vault_path=str(tmp_path))["n"]["ungrounded"] == 0


# --- CLEANUP wiring ----------------------------------------------------------

def test_record_provenance_sums_grounding_per_note_from_context(tmp_vault):
    from silica.config import CONFIG
    from silica.kernel.progress import RunManifestEntry
    from silica.router.states import finalize

    entries = [RunManifestEntry(title="Corso/A", path="Corso/A", parent=None, cluster_id=-1,
                                source_basename="lez.md", op="write")]
    fsm = types.SimpleNamespace(
        manifest=types.SimpleNamespace(entries=entries),
        progress=types.SimpleNamespace(run_id="run1"),
        _file_content_hashes=["sha1"],
        context={"grounding": [
            {"path": "Corso/A.md", "heading": "A", "source_basename": "lez.md",
             "spans": 2, "ungrounded": 1, "profile": "default"},
            {"path": "Corso/A.md", "heading": "A", "source_basename": "lez.md",
             "spans": 3, "ungrounded": 0, "profile": "default"},
            {"path": "Corso/B.md", "heading": "B", "source_basename": "other.md",
             "spans": 1, "ungrounded": 1, "profile": "default"},
        ]},
    )
    finalize._record_provenance(fsm, 0, "Inbox/lez.md")
    raw = json.loads((__import__("pathlib").Path(CONFIG.vault_path) / DEFAULT_PROVENANCE_FILENAME)
                     .read_text(encoding="utf-8"))
    assert raw[0]["grounding"] == {"Corso/A": {"spans": 5, "ungrounded": 1, "profile": "default"}}


# --- recall header -----------------------------------------------------------

def _bind(vault, monkeypatch):
    import silica.config
    import silica.driver
    vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(silica.config.CONFIG, "vault_path", str(vault))
    silica.driver._driver = None


def test_perceive_header_carries_grounding(tmp_path, monkeypatch):
    _bind(tmp_path / "v", monkeypatch)
    from silica.driver import DRIVER
    DRIVER.create("c/scored.md", '---\ndate: "2026-01-01"\n---\n\nyoga on Tuesday\n')
    DRIVER.create("c/prose.md", '---\ndate: "2026-01-01"\n---\n\nyoga on Monday\n')
    DRIVER.create("c/hand.md", '---\ndate: "2026-01-01"\n---\n\nyoga on Sunday\n')
    append_record("lez.md", "sha1", "run1", ["c/scored", "c/prose"],
                  vault_path=str(tmp_path / "v"),
                  grounding={"c/scored": {"spans": 3, "ungrounded": 1, "profile": "default"},
                             "c/prose": {"spans": 0, "ungrounded": 0, "profile": "default"}})
    from silica.kernel.recall.perception import perceive

    p = perceive("yoga?", now="2026-05-01", paths=["c/scored", "c/prose", "c/hand"],
                 use_embedder=False)
    ctx = p.render()
    assert "[#1 | c/scored | grounded: 2/3 spans | dated 2026-01-01]" in ctx
    assert "[#2 | c/prose | grounding: n/a | dated 2026-01-01]" in ctx
    assert "[#3 | c/hand | dated 2026-01-01]" in ctx


@pytest.mark.parametrize("span, source, grounded", [
    # Manual pass 2026-09-04: `0.99` read as grounded in a source saying `0.999`
    # because the numeral check was a substring test. Numerals are tokens.
    ("$\\beta_2 = 0.99$", "defaults $\\beta_2 = 0.999$ here", False),
    ("$\\beta_2 = 0.999$", "defaults $\\beta_2 = 0.999$ here", True),
    # A signed exponent is a numeral of its own: 10^{-6} is not 10^{-8}.
    ("$\\epsilon = 10^{-6}$", "with $\\epsilon = 10^{-8}$ added", False),
    ("$\\epsilon = 10^{-8}$", "with $\\epsilon = 10^{-8}$ added", True),
    # A year must not be grounded by a longer number that contains it.
    ("$N_{2012} = 1000$", "the count $N_{12012} = 1000$", False),
])
def test_numerals_are_matched_as_tokens_not_substrings(span, source, grounded):
    from silica.kernel.write.provenance import ungrounded_spans
    assert (ungrounded_spans(f"Claim: {span}.", source) == []) is grounded
