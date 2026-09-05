# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""The seven invariants of spec-contested-bitemporal §7, as the §8.1 gate.

Fasi A/B/C/D each carry their own unit tests (test_claim_stamp, test_superseded,
test_reliability_tier). This file is the gate the spec asks for instead of a QA
metric: one test per invariant, each phrased so it fails if a later change
breaks the guarantee, whatever the implementation looks like by then.

Every violation here is a blocking bug, not a flag.
"""
from __future__ import annotations

import types

from silica.kernel.progress import RunManifestEntry
from silica.kernel.write import frontmatter
from silica.kernel.write.contested import (
    STAMP_RE,
    SUPERSEDED_HEADING,
    contested_callout,
    mark_contested,
)
from silica.router.states import finalize

SUPERSEDED_TAIL = f"{SUPERSEDED_HEADING}\n\n> [!quote] Superseded 2026-07-25\n> claim vecchio.\n"


def _fsm(entries, *, keep_sources=False, seen_override=None):
    return types.SimpleNamespace(
        manifest=types.SimpleNamespace(entries=entries),
        keep_sources=keep_sources,
        seen_override=seen_override,
        _run_inverses=[],
    )


def _entry(source_basename: str, op: str, path: str) -> RunManifestEntry:
    return RunManifestEntry(
        title=path, path=path, parent=None, cluster_id=-1,
        source_basename=source_basename, op=op,
    )


# --- Invariant 2: `## Superseded` is the last section after every write ------

def test_sources_block_lands_above_the_superseded_section(tmp_vault):
    """The second EOF appender: a `## Sources` link is live content, not a grave.

    `patch_snippet` is covered in test_superseded; the leaf writer is the other
    appender, and it used to be EOF-by-construction too.
    """
    tmp_vault.note("Inbox/src.md", "---\ndate: 2026-03-01\n---\nverbatim source words\n")
    note = tmp_vault.note("Concepts/A.md", f"# A\n\nbody\n\n{SUPERSEDED_TAIL}")

    finalize._write_source_leaf(
        _fsm([_entry("src.md", "write", "Concepts/A")], keep_sources=True),
        "Inbox/src.md",
    )

    out = tmp_vault.read(note)
    assert "[[sources/src]]" in out, out
    assert out.index("## Sources") < out.index(SUPERSEDED_HEADING)
    assert out.rstrip().endswith("claim vecchio.")


def test_undated_source_leaves_the_leaf_undated(tmp_vault):
    """No today fallback on the leaf: `date: <today>` would be read back by
    `_incoming_side` as the incoming clock of every future contest this leaf
    feeds — ingest noise wearing the event clock's uniform."""
    from silica.driver import DRIVER
    from silica.kernel.recall.paths import SOURCES_DIR
    from silica.kernel.vault_manifest import in_write_dir

    tmp_vault.note("Inbox/nodate.md", "verbatim source words\n")
    tmp_vault.note("Concepts/B.md", "# B\n\nbody\n")

    finalize._write_source_leaf(
        _fsm([_entry("nodate.md", "write", "Concepts/B")], keep_sources=True),
        "Inbox/nodate.md",
    )

    leaf = DRIVER.read_note(f"{in_write_dir(SOURCES_DIR)}/nodate.md").content
    assert "date:" not in leaf, leaf
    assert "source_id: nodate.md" in leaf


# --- Invariant 4: a `valid_to` stamp implies a block under `## Superseded` ---

def _assert_valid_to_is_filed(content: str) -> None:
    """No stamp may declare a claim dead while its text sits in the live body."""
    dead = [m for m in STAMP_RE.finditer(content) if "valid_to" in m.group(1)]
    if not dead:
        return
    assert SUPERSEDED_HEADING in content, "valid_to stamped outside a superseded section"
    grave = content.index(SUPERSEDED_HEADING)
    assert all(m.start() > grave for m in dead), "a valid_to stamp escaped the section"


def test_resolution_files_every_valid_to_stamp(tmp_vault):
    from silica.tools.notes import silica_flag_note

    note = tmp_vault.note(
        "Farmacologia/Warfarin.md",
        mark_contested(
            "---\nAI: true\n---\n\n# Warfarin\n\nIl dosaggio è 5mg/die.\n\n"
            + contested_callout("Il dosaggio è 50mg/die.", "appunti.md") + "\n",
            "source: appunti.md",
        ),
    )

    res = silica_flag_note(name="Farmacologia/Warfarin.md", clear=True)
    assert "error" not in res, res

    out = tmp_vault.read(note)
    assert "valid_to" in out  # the resolution really stamped one
    _assert_valid_to_is_filed(out)


# --- Invariant 5: equal tier never auto-resolves -----------------------------

def test_equal_tier_contradiction_stays_contested(tmp_vault):
    """Recency alone must never decide: that is last-write-wins repainted.

    Strict tier dominance is the only signal allowed to auto-resolve, so with
    the two sides ranking the same the claim is recorded, never filed away.
    """
    from unittest.mock import patch

    from silica.capabilities.dedup import DedupDecision, run_dedup
    from silica.config import SilicaConfig
    from silica.kernel.workqueue import WorkItem
    from silica.kernel.write.contested import (
        TIER_DISTILLED,
        contested_refs,
        suppress_contest,
    )

    incoming = "Il dosaggio raccomandato è 50mg/die."
    # Both sides distilled: an agent note with no source link, contradicted by a
    # claim whose source is not on disk. The precondition goes through the
    # production predicate — ranking the raw excerpt with `reliability_tier`
    # would return TIER_HUMAN, the category error §6.1 is built around.
    target_body = "---\nAI: true\n---\n\n# Warfarin\n\nIl dosaggio raccomandato è 5mg/die.\n"
    assert not suppress_contest(target_body, incoming_tier=TIER_DISTILLED,
                                incoming_clock=None)

    tmp_vault.note("Farmacologia/Farmacologia.md", "# Farmacologia\n")
    target = tmp_vault.note("Farmacologia/Warfarin.md", target_body)
    tmp_vault.note("Inbox/appunti.md", f"# Appunti\n\n{incoming}\n")

    item = WorkItem(
        kind="dedup",
        target_path="Farmacologia/Warfarin.md",
        context={"concept": "Dosaggio", "excerpt": incoming, "candidate": "Warfarin",
                 "hub": "Farmacologia", "inbox_file": "Inbox/appunti.md"},
        reason="dedup score=0.88",
    )
    decision = DedupDecision(verdict="contradicts", rationale="5 vs 50 mg",
                             addition=incoming)
    with patch("silica.capabilities.dedup._decide_dedup", return_value=decision):
        res = run_dedup(item, SilicaConfig())
    assert res["status"] == "committed", res

    out = tmp_vault.read(target)
    assert contested_refs(out), "the contradiction was not recorded"
    assert SUPERSEDED_HEADING not in out, "equal tier auto-resolved"
    assert "5mg/die" in out and "50mg/die" in out  # both claims stay live


# --- Invariant 1: no path removes claim text from the vault ------------------

def _vault_text() -> str:
    from pathlib import Path

    from silica.config import CONFIG

    return "\n".join(
        p.read_text(encoding="utf-8") for p in Path(CONFIG.vault_path).rglob("*.md")
    )


def test_the_whole_arc_never_deletes_a_claim(tmp_vault):
    """Contest, resolve, then merge: a losing claim moves, it never evaporates.

    Each step alone is covered elsewhere; what this asserts is the composition,
    which is where a claim would go missing without anyone noticing.
    """
    from unittest.mock import patch

    from silica.capabilities.dedup import DedupDecision, run_dedup
    from silica.config import SilicaConfig
    from silica.kernel.workqueue import WorkItem
    from silica.tools.notes import silica_flag_note

    claims = ["Il dosaggio raccomandato è 5mg/die.",
              "Il dosaggio raccomandato è 50mg/die.",
              "L'INR va controllato ogni settimana."]

    tmp_vault.note("Farmacologia/Farmacologia.md", "# Farmacologia\n")
    tmp_vault.note("Farmacologia/Warfarin.md", mark_contested(
        f"---\nAI: true\n---\n\n# Warfarin\n\n{claims[0]}\n\n"
        + contested_callout(claims[1], "appunti.md") + "\n",
        "source: appunti.md",
    ))
    tmp_vault.note("Farmacologia/Dosaggio.md", f"---\nAI: true\n---\n\n# Dosaggio\n\n{claims[2]}\n")
    assert all(c in _vault_text() for c in claims)

    assert "error" not in silica_flag_note(name="Farmacologia/Warfarin.md", clear=True)

    item = WorkItem(
        kind="dedup",
        target_path="Farmacologia/Warfarin.md",
        context={"concept": "Dosaggio", "excerpt": claims[2], "candidate": "Warfarin",
                 "hub": "Farmacologia", "inbox_file": "Farmacologia/Dosaggio.md",
                 "loser_path": "Farmacologia/Dosaggio.md"},
        reason="dedup score=0.91",
    )
    decision = DedupDecision(verdict="duplicate", rationale="same claim",
                             addition=claims[2])
    with patch("silica.capabilities.dedup._decide_dedup", return_value=decision):
        assert run_dedup(item, SilicaConfig())["status"] == "committed"

    after = _vault_text()
    assert [c for c in claims if c not in after] == []


# --- Invariant 7: a supersede cycle is revertible ----------------------------

def test_revert_undoes_a_supersede_write(tmp_vault):
    """Why supersede is an `overwrite` with base_content and not a new OpType:
    the existing journal covers it, so `/revert` restores the pre-write body."""
    from silica.agent.bounds import dedup_supersede_bounds
    from silica.agent.commit import _current_undo_run, commit_ops
    from silica.kernel.write.contested import mark_superseded_by
    from silica.kernel.write.ops import Op, OpType
    from silica.kernel.write.undo_journal import get_undo_journal, revert_run

    # `type:` present up front: the driver stamps it on every write (OKF §4.1),
    # so a note without it would come back normalised and hide the real question.
    prior = "---\nAI: true\ntype: Note\n---\n\n# Dosaggio\n\nIl dosaggio è 5mg/die.\n"
    rel = "Farmacologia/Dosaggio.md"
    disk = tmp_vault.note(rel, prior)
    tmp_vault.note("Farmacologia/Farmacologia.md", "# Farmacologia\n")

    run_id = get_undo_journal().start_run(source="dedup")
    token = _current_undo_run.set(run_id)
    try:
        res = commit_ops(
            [Op(op=OpType.overwrite, heading="Dosaggio", source_basename="dedup",
                path=rel, content=mark_superseded_by(prior, "Warfarin.md"),
                base_content=prior, reason="dedup merge: superseded_by pointer")],
            target_dir="Farmacologia",
            bounds=dedup_supersede_bounds(rel),
        )
    finally:
        _current_undo_run.reset(token)
    assert res["status"] == "committed", res
    assert "superseded_by" in tmp_vault.read(disk)

    reverted = revert_run(run_id)
    assert reverted["reverted"], reverted
    assert tmp_vault.read(disk) == prior


# --- Invariant 6: no `valid_from`, no stamp, anywhere ------------------------

def test_paths_without_an_event_clock_emit_no_stamp(tmp_vault):
    """Golden parity: only a caller that resolved an event clock may stamp one.

    The FSM resolves `valid_from` per file; every other writer (interactive
    tools, dedup, enrich, refine) does not, and their output must stay exactly
    what it was before the claim stamp existed.
    """
    from unittest.mock import patch

    from silica.capabilities.dedup import DedupDecision, run_dedup
    from silica.config import SilicaConfig
    from silica.kernel.workqueue import WorkItem
    from silica.tools.notes import silica_write_note

    tmp_vault.note("Farmacologia/Farmacologia.md", "# Farmacologia\n")
    res = silica_write_note(path="Farmacologia/Warfarin.md", body="Il dosaggio è 5mg/die.")
    assert "error" not in res, res

    item = WorkItem(
        kind="dedup",
        target_path="Farmacologia/Warfarin.md",
        context={"concept": "Dosaggio", "excerpt": "L'INR va controllato.",
                 "candidate": "Warfarin", "hub": "Farmacologia",
                 "inbox_file": "Inbox/appunti.md"},
        reason="dedup score=0.91",
    )
    decision = DedupDecision(verdict="duplicate", rationale="same claim",
                             addition="L'INR va controllato.")
    with patch("silica.capabilities.dedup._decide_dedup", return_value=decision):
        assert run_dedup(item, SilicaConfig())["status"] == "committed"

    assert not STAMP_RE.search(_vault_text()), "a stamp leaked onto a clockless path"


# --- Invariant 3: resolve one ref, the others stay open ----------------------

def _two_contradictions() -> str:
    note = mark_contested(
        "---\nAI: true\n---\n\n# Warfarin\n\nIl dosaggio è 5mg/die.\n\n"
        + contested_callout("Il dosaggio è 50mg/die.", "appunti.md") + "\n\n"
        + contested_callout("Il dosaggio è 500mg/die.", "slides.md") + "\n",
        "source: appunti.md",
    )
    return mark_contested(note, "source: slides.md")


def test_resolving_one_ref_leaves_the_other_open():
    """The literal §7.3: one contradiction resolved, one still standing.

    `clear_contested` popped both frontmatter keys whatever the ref count, so
    resolving one erased the record of every other. Selectivity is what makes
    that expressible at all: without it the only resolution is all-or-nothing.
    """
    from silica.kernel.write.contested import contested_refs, resolve_contested

    out = resolve_contested(_two_contradictions(), resolved_by="user",
                            valid_to="2026-08-11", source_ref="source: appunti.md")

    assert contested_refs(out) == ["source: slides.md"]  # flag survives with it
    grave = out[out.index(SUPERSEDED_HEADING):]
    assert "from appunti.md" in grave
    assert "from slides.md" not in grave       # the open one stays in the live body
    assert "Unresolved." in out                # and still says so
    _assert_valid_to_is_filed(out)


def test_resolving_the_last_ref_clears_the_flag():
    """`contested: true` falls only when the list empties, never before."""
    from silica.kernel.write.contested import contested_refs, resolve_contested

    once = resolve_contested(_two_contradictions(), resolved_by="user",
                             valid_to="2026-08-11", source_ref="source: appunti.md")
    twice = resolve_contested(once, resolved_by="user",
                              valid_to="2026-08-12", source_ref="source: slides.md")

    assert contested_refs(twice) == []
    data, _, _ = frontmatter.split(twice)
    assert "contested" not in data and "contradictions" not in data
    assert "Unresolved." not in twice
    grave = twice[twice.index(SUPERSEDED_HEADING):]
    assert "from appunti.md" in grave and "from slides.md" in grave


def test_resolving_every_ref_at_once_keeps_both_records():
    """No `source_ref`: the all-or-nothing resolution the live tool still uses."""
    from silica.kernel.write.contested import contested_refs, parse_stamps, resolve_contested

    out = resolve_contested(_two_contradictions(), resolved_by="user", valid_to="2026-08-11")

    assert contested_refs(out) == []
    grave = out[out.index(SUPERSEDED_HEADING):]
    assert "from appunti.md" in grave and "from slides.md" in grave
    assert len([s for s in parse_stamps(out) if "valid_to" in s]) == 2
    _assert_valid_to_is_filed(out)
