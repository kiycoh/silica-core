# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Verbatim source leaves (spec-harness-promotion 2026-07-24 §2).

Write side: `finalize._write_source_leaf` at CLEANUP (fake-FSM style, like
test_finalize_provenance) and `web_research._write_leaf`. Read side: leaves
are retrieval-invisible — one test per index (search, embed, cooccurrence,
autolink title index) — while staying reachable via silica_read_note.
"""
from __future__ import annotations

import types
from pathlib import Path

from silica.kernel.write.ops import InverseOpKind
from silica.kernel.progress import RunManifestEntry
from silica.router.states import finalize


def _entry(source_basename: str, op: str, path: str) -> RunManifestEntry:
    return RunManifestEntry(
        title=path, path=path, parent=None, cluster_id=-1,
        source_basename=source_basename, op=op,
    )


def _fsm(entries, *, keep_sources=False, seen_override=None):
    return types.SimpleNamespace(
        manifest=types.SimpleNamespace(entries=entries),
        keep_sources=keep_sources,
        seen_override=seen_override,
        _run_inverses=[],
    )


def _vault_file(rel: str) -> Path:
    from silica.config import CONFIG

    return Path(CONFIG.vault_path) / rel


# ---------------------------------------------------------------------------
# Write side — CLEANUP hook
# ---------------------------------------------------------------------------

def test_keep_sources_writes_leaf_and_links(tmp_vault):
    tmp_vault.note("Inbox/src.md", "---\ndate: 2026-03-01\n---\nverbatim source words\n")
    tmp_vault.note("Concepts/A.md", "# A\nbody\n")
    fsm = _fsm([_entry("src.md", "write", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/src.md")

    leaf = _vault_file("sources/src.md").read_text(encoding="utf-8")
    assert "verbatim source words" in leaf
    assert "source_id: src.md" in leaf
    assert "date: 2026-03-01" in leaf  # the source's own date is preserved
    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert "## Sources" in note and "[[sources/src]]" in note
    kinds = [inv.kind for _, inv, _ in fsm._run_inverses]
    assert kinds == [InverseOpKind.delete_created, InverseOpKind.restore_version]


def test_leaf_carries_source_file_from_converted_source(tmp_vault):
    """A converted inbox note names its real file in `source_file:`; the leaf
    rebuilds the frontmatter wholesale, so the key must be carried over or the
    pointer dies with the archived inbox note."""
    tmp_vault.note(
        "Inbox/src.md",
        '---\ndate: 2026-03-01\nsource_file: "/home/u/paper.pdf"\n---\nverbatim words\n',
    )
    tmp_vault.note("Concepts/A.md", "# A\nbody\n")
    fsm = _fsm([_entry("src.md", "write", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/src.md")

    leaf = _vault_file("sources/src.md").read_text(encoding="utf-8")
    head = leaf.split("\n---\n")[0]
    assert 'source_file: "/home/u/paper.pdf"' in head
    assert "source_file" not in leaf.split("\n---\n", 1)[1]  # not duplicated in the body


def test_plain_ingest_writes_no_leaf(tmp_vault):
    tmp_vault.note("Inbox/src.md", "words\n")
    tmp_vault.note("Concepts/A.md", "# A\n")
    fsm = _fsm([_entry("src.md", "write", "Concepts/A")])

    finalize._write_source_leaf(fsm, "Inbox/src.md")

    assert not _vault_file("sources/src.md").exists()
    assert "## Sources" not in _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert fsm._run_inverses == []


def test_capture_always_writes_leaf_with_capture_date(tmp_vault):
    tmp_vault.note("Inbox/session_2.md", "alice said hello\n")
    tmp_vault.note("memory/Alice.md", "# Alice\n")
    fsm = _fsm([_entry("session_2.md", "write", "memory/Alice")],
               seen_override="2023-05-20")

    finalize._write_source_leaf(fsm, "Inbox/session_2.md")

    leaf = _vault_file("sources/session_2.md").read_text(encoding="utf-8")
    assert "date: 2023-05-20" in leaf and "alice said hello" in leaf
    assert "[[sources/session_2]]" in _vault_file("memory/Alice.md").read_text(encoding="utf-8")


def test_sources_block_idempotent_on_reingest(tmp_vault):
    tmp_vault.note("Inbox/src.md", "words\n")
    tmp_vault.note("Concepts/A.md", "# A\n")
    fsm = _fsm([_entry("src.md", "write", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/src.md")
    finalize._write_source_leaf(_fsm([_entry("src.md", "write", "Concepts/A")],
                                     keep_sources=True), "Inbox/src.md")

    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert note.count("[[sources/src]]") == 1 and note.count("## Sources") == 1


def test_second_source_appends_link_to_existing_block(tmp_vault):
    tmp_vault.note("Inbox/b.md", "more words\n")
    tmp_vault.note("Concepts/A.md", "# A\n\n## Sources\n[[a]]\n")
    fsm = _fsm([_entry("b.md", "patch", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/b.md")

    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert note.count("## Sources") == 1
    assert "[[a]]" in note and "[[sources/b]]" in note


def test_prewritten_leaf_links_without_any_flag(tmp_vault):
    """web_research writes its leaf up front; a later plain /nucleate of the
    findings note must still link the distilled notes to it."""
    tmp_vault.note("sources/topic.md", "---\ndate: 2026-01-01\n---\nraw excerpts\n")
    tmp_vault.note("Inbox/topic.md", "findings\n")
    tmp_vault.note("Concepts/T.md", "# T\n")
    fsm = _fsm([_entry("topic.md", "write", "Concepts/T")])

    finalize._write_source_leaf(fsm, "Inbox/topic.md")

    note = _vault_file("Concepts/T.md").read_text(encoding="utf-8")
    assert "[[sources/topic]]" in note
    # the pre-existing leaf is not rewritten
    assert "raw excerpts" in _vault_file("sources/topic.md").read_text(encoding="utf-8")


def test_undo_removes_leaf_and_block(tmp_vault):
    tmp_vault.note("Inbox/src.md", "words\n")
    prior_note = "# A\nbody\n"
    tmp_vault.note("Concepts/A.md", prior_note)
    fsm = _fsm([_entry("src.md", "write", "Concepts/A")], keep_sources=True)
    finalize._write_source_leaf(fsm, "Inbox/src.md")

    from silica.tools.wrapped import silica_restore

    res = silica_restore(
        txn_id="t0", inverses=[inv.model_dump() for _, inv, _ in fsm._run_inverses]
    )
    assert res.get("success")
    assert not _vault_file("sources/src.md").exists()
    assert _vault_file("Concepts/A.md").read_text(encoding="utf-8") == prior_note


def test_never_raises_on_broken_fsm(tmp_vault):
    finalize._write_source_leaf(types.SimpleNamespace(), "Inbox/x.md")  # must not raise


def test_wired_before_archive(monkeypatch, tmp_vault):
    """handle_cleanup writes the leaf BEFORE the source is archived away."""
    order = []
    monkeypatch.setattr(finalize, "_write_source_leaf",
                        lambda fsm, src: order.append(("leaf", src)))
    monkeypatch.setattr(finalize, "_record_provenance", lambda *a: None)
    monkeypatch.setattr(finalize, "_log_nucleate_completion", lambda *a: None)
    monkeypatch.setattr("silica.tools.wrapped.silica_cleanup",
                        lambda *a, **k: order.append(("archive", a[0])) or {"success": True})

    fsm = types.SimpleNamespace(
        _get_chunks_from_context_if_empty=lambda: None,
        _chunk_flat_to_fi_ci={0: (0, 0)},
        _current_chunk_idx=0,
        _progress_note=lambda *a, **k: None,
        _write_ledger_for_file=lambda *a, **k: None,
        _file_chunks={0: {"chunks": [{}], "source_file": "Inbox/a.md"}},
        progress=types.SimpleNamespace(tasks=[]),
        inbox_file="Inbox/a.md",
        context={},
        _undo_run_id=None,
        _run_inverses=[],
        _transition_success=lambda: None,
        _chunk_task_id=lambda *a: "cleanup",
    )
    finalize.handle_cleanup(fsm)

    assert order == [("leaf", "Inbox/a.md"), ("archive", "Inbox/a.md")]


# ---------------------------------------------------------------------------
# Write side — web_research
# ---------------------------------------------------------------------------

def test_web_research_leaf_from_tool_trace(tmp_vault):
    from silica.sources.web_research import _write_leaf

    # The trace is the tool results themselves, recorded at emission — not the
    # message history, which run_agent's compaction sweep rewrites in place.
    results = [
        '[{"title": "T", "url": "u", "content": "excerpt one"}]',
        '[{"title": "U", "url": "v", "content": "excerpt two"}]',
    ]
    _write_leaf("Inbox/topic.md", results)

    leaf = _vault_file("sources/topic.md").read_text(encoding="utf-8")
    assert "excerpt one" in leaf and "excerpt two" in leaf
    assert "source_id: topic.md" in leaf


def test_web_research_no_trace_no_leaf(tmp_vault):
    from silica.sources.web_research import _write_leaf

    _write_leaf("Inbox/topic.md", [])
    assert not _vault_file("sources/topic.md").exists()


# ---------------------------------------------------------------------------
# Read side — retrieval invisibility, one test per index
# ---------------------------------------------------------------------------

def _leafed_vault(tmp_vault):
    tmp_vault.note("Concepts/Neural.md", "# Neural\nneural network architecture\n")
    tmp_vault.note(
        "sources/uniqueleaf.md",
        "---\ndate: 2026-01-01\nsource_id: uniqueleaf.md\n---\nzanzibar verbatim excerpt\n",
    )


def test_search_invisible_but_readable(tmp_vault):
    _leafed_vault(tmp_vault)
    from silica.driver import get_driver

    d = get_driver()
    assert not d.search_names("uniqueleaf")
    assert not d.search_context("zanzibar")
    assert all(not r.path.startswith("sources/") for r in d.list_files())
    # the sanctioned path stays open: read by stem (a `## Sources` wikilink)
    assert "zanzibar" in d.read_note("uniqueleaf").content


def test_embed_index_invisible(tmp_vault, monkeypatch):
    _leafed_vault(tmp_vault)

    class _Emb:
        def embed(self, texts):
            return [[1.0, 0.0] for _ in texts]

    monkeypatch.setattr("silica.agent.providers.get_embedder", lambda cfg: _Emb())
    from silica.tools.composed import silica_embed_refresh

    res = silica_embed_refresh(force=True)
    assert "error" not in res
    from silica.kernel.recall.embed import get_store

    assert not any("uniqueleaf" in p for p in get_store().paths())


def test_cooccur_index_invisible(tmp_vault):
    _leafed_vault(tmp_vault)
    from silica.tools.composed import silica_cooccurrence_refresh

    res = silica_cooccurrence_refresh(force=True)
    assert "error" not in res
    from silica.kernel.recall.cooccurrence import CooccurStore

    assert not any("uniqueleaf" in p for p in CooccurStore().paths())


def test_lexical_index_invisible(tmp_vault):
    _leafed_vault(tmp_vault)
    from silica.tools.composed import silica_lexical_refresh

    res = silica_lexical_refresh(force=True)
    assert "error" not in res
    from silica.kernel.recall.lexical import get_lexical_store

    assert not any("uniqueleaf" in p for p in get_lexical_store().paths())


def test_autolink_title_index_invisible(tmp_vault):
    _leafed_vault(tmp_vault)
    from silica.driver import get_driver
    from silica.kernel.link.autolink import build_title_index

    titles = build_title_index(get_driver().list_files())
    assert not any("uniqueleaf" in t.lower() for t in titles)
    assert any("neural" in t.lower() for t in titles)


def test_grounded_lines_carry_the_leaf_id_and_its_definition(tmp_vault):
    """Keyed attribution (OKF §5.1): the label joins the line to the leaf."""
    verbatim = "the twentieth of May at the community center downtown"
    tmp_vault.note("Inbox/src.md", f"---\ndate: 2026-03-01\n---\nElena starts {verbatim}.\n")
    tmp_vault.note("Concepts/A.md", f"# A\n\n{verbatim}\n")
    fsm = _fsm([_entry("src.md", "write", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/src.md")

    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert f"{verbatim}[^src]" in note        # the claim carries the key
    assert "[^src]: [[sources/src]]" in note          # the key resolves to the leaf
    assert "## Sources" in note and "[[sources/src]]" in note


def test_a_note_with_no_verbatim_line_gets_no_footnote(tmp_vault):
    """No dangling `[^id]`, and no definition for a marker that was never emitted."""
    tmp_vault.note("Inbox/src.md", "---\ndate: 2026-03-01\n---\nverbatim source words\n")
    tmp_vault.note("Concepts/A.md", "# A\n\nSomething the source never said at all.\n")
    fsm = _fsm([_entry("src.md", "write", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/src.md")

    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert "[^src]" not in note
    assert "## Sources" in note and "[[sources/src]]" in note   # the block still lands


def test_leaf_in_frontmatter_does_not_suppress_the_sources_block(tmp_vault):
    """The distiller names the source basename as a `related:`/`parent note:`
    wikilink long before the leaf exists. The old idempotency check grepped the
    WHOLE note, so those notes read as "already linked", never got the block,
    and reliability_tier (which looks for the `## Sources` marker) filed them as
    distilled. Measured 2026-08-18: 10 of 30 notes on one paper."""
    tmp_vault.note("Inbox/src.md", "---\ndate: 2026-03-01\n---\nverbatim words\n")
    tmp_vault.note(
        "Concepts/A.md",
        '---\nAI: true\nparent note: "[[src]]"\nrelated:\n  - "[[src]]"\n---\n\n# A\nbody\n',
    )
    fsm = _fsm([_entry("src.md", "write", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/src.md")

    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert "## Sources" in note

    from silica.kernel.write.contested import TIER_GROUNDED, reliability_tier
    assert reliability_tier(note) == TIER_GROUNDED


def test_relinking_the_same_leaf_twice_is_still_a_no_op(tmp_vault):
    """Re-ingest safety: the block must not grow a second copy of the link."""
    tmp_vault.note("Inbox/src.md", "---\ndate: 2026-03-01\n---\nverbatim words\n")
    tmp_vault.note("Concepts/A.md", "# A\nbody\n")
    fsm = _fsm([_entry("src.md", "write", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/src.md")
    once = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    finalize._write_source_leaf(fsm, "Inbox/src.md")
    twice = _vault_file("Concepts/A.md").read_text(encoding="utf-8")

    assert once == twice
    assert twice.count("[[sources/src]]") == 1


# ---------------------------------------------------------------------------
# 2026-09-04: the link is path-qualified and lands inside the block
# ---------------------------------------------------------------------------

def test_sources_link_is_path_qualified_so_a_spine_note_cannot_capture_it(tmp_vault):
    """The outline lane writes a spine note with the source's own stem next to
    the concept notes; a bare `[[Lezione 3]]` resolves to that sibling, so
    every `## Sources` block pointed at the spine and the leaf was unreachable."""
    tmp_vault.note("Inbox/Lezione 3.md", "verbatim words\n")
    tmp_vault.note("ML/Lezione 3.md", "# Lezione 3\n1. [[A]]\n")
    tmp_vault.note("ML/A.md", "# A\nbody\n")
    fsm = _fsm([_entry("Lezione 3.md", "write", "ML/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/Lezione 3.md")

    note = _vault_file("ML/A.md").read_text(encoding="utf-8")
    assert "[[sources/Lezione 3]]" in note
    assert "\n[[Lezione 3]]\n" not in note


def test_second_source_link_lands_inside_the_existing_sources_block(tmp_vault):
    """A patch appended after the block used to push the next link to EOF,
    orphaned under whatever section came last."""
    tmp_vault.note("Inbox/b.md", "more words\n")
    tmp_vault.note("Concepts/A.md",
                   "# A\n\n## Sources\n[[sources/a]]\n\n## Relations\n- applies [[X]]: y\n")
    fsm = _fsm([_entry("b.md", "patch", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/b.md")

    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    block = note.split("## Sources", 1)[1].split("## Relations", 1)[0]
    assert "[[sources/a]]" in block and "[[sources/b]]" in block
    assert note.rstrip().endswith("- applies [[X]]: y")


def test_a_legacy_bare_link_in_the_block_still_counts_as_linked(tmp_vault):
    tmp_vault.note("Inbox/src.md", "words\n")
    tmp_vault.note("Concepts/A.md", "# A\n\n## Sources\n[[src]]\n")
    fsm = _fsm([_entry("src.md", "write", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/src.md")

    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert "[[sources/src]]" not in note and note.count("[[src]]") == 1


def test_a_patch_marker_gets_its_definition_even_when_no_line_is_verbatim(tmp_vault):
    """The patch executor cites distilled prose with the leaf's label; the
    definition is CLEANUP's to write, or the marker dangles."""
    tmp_vault.note("Inbox/src.md", "---\ndate: 2026-03-01\n---\nverbatim source words\n")
    tmp_vault.note("Concepts/A.md", "# A\n\nDistilled, not verbatim.[^src]\n")
    fsm = _fsm([_entry("src.md", "patch", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/src.md")
    finalize._write_source_leaf(fsm, "Inbox/src.md")

    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert note.count("[^src]: [[sources/src]]") == 1


def test_without_a_leaf_the_patch_marker_is_defined_as_plain_text(tmp_vault):
    """`--no-keep-sources`: no leaf, no `## Sources` (the tier stays honest),
    but the marker still resolves to the source's name."""
    tmp_vault.note("Inbox/src.md", "words\n")
    tmp_vault.note("Concepts/A.md", "# A\n\nDistilled.[^src]\n")
    fsm = _fsm([_entry("src.md", "patch", "Concepts/A")])

    finalize._write_source_leaf(fsm, "Inbox/src.md")
    finalize._write_source_leaf(fsm, "Inbox/src.md")

    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert note.count("[^src]: src.md") == 1
    assert "## Sources" not in note


def test_attribute_lines_leaves_embeds_and_display_math_alone():
    """An image embed or a formula is not a claim a reader can check against
    the leaf; the marker lands on prose only (observed live 2026-09-04:
    `![[img.jpg]][^Lezione-6]`)."""
    from silica.kernel.write.provenance import attribute_lines

    src = ("Il gradiente indica la direzione di massima crescita della funzione.\n"
           "![[f55d1720baa2a486f64b0b06e2f6bdea915a214f1bdd38d54a50e3d99bd66912.jpg]]\n"
           "$$x^{*} = \\arg \\min f(x) \\quad \\text{con } f \\text{ convessa}$$\n"
           "$$\n\\boldsymbol{C} = \\boldsymbol{A} + \\boldsymbol{B} \\quad \\boldsymbol{C}_{i,j} = \\boldsymbol{A}_{i,j} + \\boldsymbol{B}_{i,j}\n$$\n")
    out = attribute_lines("# N\n\n" + src, src, "src")

    assert "Il gradiente indica la direzione di massima crescita della funzione.[^src]" in out
    assert "]][^src]" not in out
    assert "$$[^src]" not in out
    assert "_{i,j}[^src]" not in out


def test_leaf_upgrades_the_patch_executors_plain_definition_to_the_link(tmp_vault):
    """The executor defines its marker as the source's name (it cannot know
    whether a leaf will exist); once the leaf does, CLEANUP owns the
    definition and points it at the leaf, leaving exactly one."""
    tmp_vault.note("Inbox/src.md", "---\ndate: 2026-03-01\n---\nverbatim source words\n")
    tmp_vault.note("Concepts/A.md", "# A\n\nDistilled.[^src]\n\n[^src]: src.md\n")
    fsm = _fsm([_entry("src.md", "patch", "Concepts/A")], keep_sources=True)

    finalize._write_source_leaf(fsm, "Inbox/src.md")
    finalize._write_source_leaf(fsm, "Inbox/src.md")

    note = _vault_file("Concepts/A.md").read_text(encoding="utf-8")
    assert "[^src]: src.md" not in note
    assert note.count("[^src]: [[sources/src]]") == 1
    assert note.count("[^src]:") == 1
