"""M6 (docs/specs/epistemic-state.md): when a source is re-nucleated at a new
sha, the notes of the previous version are re-checked against the new text
span by span. A note that lost nothing leaves the drifted list on its own; a
note that lost a span stays drifted and the digest names the span.
"""
from __future__ import annotations

import json
import types
from pathlib import Path

from silica.kernel.progress import RunManifestEntry
from silica.kernel.write.provenance import (
    DEFAULT_PROVENANCE_FILENAME,
    append_record,
    drifted_notes,
    read_records,
)
from silica.router.states import finalize

KEPT = "$m_t = \\beta_1 m_{t-1} + (1 - \\beta_1) g_t$"
LOST = "$\\epsilon_{stability} = 10^{-8}$"


class _Ledger:
    def __init__(self):
        self.rows = []

    def add(self, path, kind, detail):
        self.rows.append((path, kind, detail))


def _fsm(entries, run_id, sha, ledger=None):
    return types.SimpleNamespace(
        manifest=types.SimpleNamespace(entries=entries),
        progress=types.SimpleNamespace(run_id=run_id),
        _file_content_hashes=[sha],
        context={},
        warning_ledger=ledger,
    )


def _entry(src, path):
    return RunManifestEntry(title=path, path=path, parent=None, cluster_id=-1,
                            source_basename=src, op="write")


def _seed(tmp_vault):
    from silica.config import CONFIG
    from silica.kernel.vault_manifest import archive_path_for

    vault = Path(CONFIG.vault_path)
    tmp_vault.note("Corso/A.md", f"# A\n\nfirst moment {KEPT} stays.\n")
    tmp_vault.note("Corso/B.md", f"# B\n\nstability {LOST} was in v1 only.\n")
    append_record("lez.md", "sha1", "run1", ["Corso/A", "Corso/B"], vault_path=str(vault))
    # v2 of the source, already archived by CLEANUP where validate would look too
    tmp_vault.note(archive_path_for("Inbox/lez.md"), f"v2 keeps only {KEPT} in its text.\n")
    return vault


def test_reverify_scores_prior_notes_against_the_new_source(tmp_vault):
    vault = _seed(tmp_vault)
    ledger = _Ledger()
    finalize._record_provenance(_fsm([_entry("lez.md", "Corso/C")], "run2", "sha2", ledger),
                                0, "Inbox/lez.md")

    rec = json.loads((vault / DEFAULT_PROVENANCE_FILENAME).read_text(encoding="utf-8"))[-1]
    assert rec["sha256"] == "sha2" and rec["notes"] == ["Corso/C"]
    assert rec["grounding"]["Corso/A"] == {"spans": 1, "ungrounded": 0, "profile": "reverify"}
    assert rec["grounding"]["Corso/B"] == {"spans": 1, "ungrounded": 1, "profile": "reverify"}
    assert rec["reverified"] == ["Corso/A"]
    assert [(p, k) for p, k, _d in ledger.rows] == [("Corso/B", "reverify_lost_span")]
    assert "epsilon" in ledger.rows[0][2]


def test_reverified_note_leaves_the_drifted_list_the_lost_one_stays(tmp_vault):
    vault = _seed(tmp_vault)
    finalize._record_provenance(_fsm([], "run2", "sha2"), 0, "Inbox/lez.md")
    assert drifted_notes(vault_path=str(vault)) == [("Corso/B", "lez.md")]


def test_same_sha_does_not_reverify(tmp_vault):
    vault = _seed(tmp_vault)
    finalize._record_provenance(_fsm([_entry("lez.md", "Corso/C")], "run2", "sha1"), 0, "Inbox/lez.md")
    rec = read_records("lez.md", vault_path=str(vault))[-1]
    assert "reverified" not in rec and "grounding" not in rec


def test_unreadable_new_source_skips_reverify_but_records(tmp_vault):
    from silica.config import CONFIG

    vault = Path(CONFIG.vault_path)
    tmp_vault.note("Corso/A.md", f"# A\n\n{KEPT}\n")
    append_record("lez.md", "sha1", "run1", ["Corso/A"], vault_path=str(vault))
    finalize._record_provenance(_fsm([], "run2", "sha2"), 0, "Inbox/lez.md")
    rec = read_records("lez.md", vault_path=str(vault))[-1]
    assert rec["sha256"] == "sha2" and "reverified" not in rec
    assert drifted_notes(vault_path=str(vault)) == [("Corso/A", "lez.md")]


def test_a_note_this_run_patched_is_still_rechecked_whole_body(tmp_vault):
    """Manual pass 2026-09-04: the gate grounded the patch snippet (new
    constants, 2/2) but the landing kept the old paragraph, so the note carried
    a span the new source no longer has while the ledger said fully grounded.
    The op row scores only what the op proposed; the whole body is what the
    reader gets, so the reverify row must cover every prior note and win."""
    vault = _seed(tmp_vault)
    ledger = _Ledger()
    fsm = _fsm([_entry("lez.md", "Corso/B")], "run2", "sha2", ledger)
    fsm.context["grounding"] = [{"path": "Corso/B.md", "heading": "B", "source_basename": "lez.md",
                                 "spans": 1, "ungrounded": 0, "profile": "default"}]
    finalize._record_provenance(fsm, 0, "Inbox/lez.md")

    rec = json.loads((vault / DEFAULT_PROVENANCE_FILENAME).read_text(encoding="utf-8"))[-1]
    assert rec["notes"] == ["Corso/B"]
    assert rec["grounding"]["Corso/B"] == {"spans": 1, "ungrounded": 1, "profile": "reverify"}
    assert rec["grounding"]["Corso/A"]["profile"] == "reverify"
    assert rec["reverified"] == ["Corso/A"]
    assert [(p, k) for p, k, _d in ledger.rows] == [("Corso/B", "reverify_lost_span")]


OTHER = "$v_t = \\beta_2 v_{t-1} + (1 - \\beta_2) g_t^2$"


def test_reverify_checks_a_multi_source_note_against_every_source(tmp_vault):
    """Real ledgers 2026-09-04: 32% of the test vault's notes sit under two or
    more sources. Checking such a note against the one source being
    re-nucleated read every span from the other source as lost."""
    from silica.config import CONFIG
    from silica.kernel.vault_manifest import archive_path_for

    vault = _seed(tmp_vault)
    tmp_vault.note("Corso/A.md", f"# A\n\nfirst {KEPT} and second {OTHER} moment.\n")
    append_record("other.md", "shaO", "runO", ["Corso/A"], vault_path=str(CONFIG.vault_path))
    tmp_vault.note(archive_path_for("Inbox/sub/other.md"), f"second moment {OTHER} here.\n")
    ledger = _Ledger()

    finalize._record_provenance(_fsm([], "run2", "sha2", ledger), 0, "Inbox/lez.md")

    rec = json.loads((vault / DEFAULT_PROVENANCE_FILENAME).read_text(encoding="utf-8"))[-1]
    assert rec["grounding"]["Corso/A"] == {"spans": 2, "ungrounded": 0, "profile": "reverify"}
    assert "Corso/A" in rec["reverified"]
    assert [p for p, _k, _d in ledger.rows] == ["Corso/B"]
