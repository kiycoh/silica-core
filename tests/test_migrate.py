# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""/migrate: the pre-2026-09-04 provenance blocks rewritten through today's
writer, listed for free and written only on --write (ADR-0039)."""
from __future__ import annotations

import pytest

from silica.kernel.write import migrate

NOTE = """---
title: Margine geometrico
type: concept
---

# Margine geometrico

Il margine e' la distanza dal piano.

## Definizione

Formale.

## Relations
- [[Iperpiano]] defines this (Lezione 11): base.

## Sources
[[sources/Lezione 11]]
[^Lezione-11]: [[sources/Lezione 11]]
"""

REL_BLOCK = """
## Additional notes: Margine geometrico (from Lezione 12.md)

- [[Bound]] applies this (Lezione 12): limita l'errore.
"""

PROSE_BLOCK = """
## Additional notes: Margine geometrico (from Lezione 12.md)

Il margine geometrico limita l'errore atteso.

### Formulazione

Con la norma del vettore dei pesi.
"""

M = migrate.PROVENANCE_HEADER


def _section(text: str, heading: str) -> str:
    return text.split(f"{heading}\n", 1)[1].split("\n## ", 1)[0]


# ---------------------------------------------------------------------------
# the rewrite, pure
# ---------------------------------------------------------------------------

def test_detect_counts_blocks_in_both_spellings():
    note = NOTE + REL_BLOCK + "\n## Note aggiuntive — Margine (da Lezione 3.md)\n\ntesto\n"
    assert M.detect(note) == 2
    assert M.detect(NOTE) == 0


def test_a_note_without_the_shape_is_returned_as_is():
    assert M.apply(NOTE) == NOTE


def test_relation_block_joins_relations_and_the_header_goes():
    out = M.apply(NOTE + REL_BLOCK)
    assert "Additional notes" not in out
    assert "- [[Bound]] applies this (Lezione 12): limita l'errore." in _section(out, "## Relations")
    assert "[[Iperpiano]]" in _section(out, "## Relations")
    assert M.detect(out) == 0


def test_prose_block_lands_headerless_and_cited_once():
    out = M.apply(NOTE + PROSE_BLOCK)
    assert "Additional notes" not in out
    body = out.split("## Sources", 1)[0]
    assert "Il margine geometrico limita l'errore atteso.[^Lezione-12]" in body
    assert body.index("limita l'errore atteso") < body.index("## Definizione")  # lead above the first H2
    assert "\n## Formulazione\n" in body                                       # facet promoted to H2
    assert body.index("## Formulazione") < body.index("## Relations")            # and above the tail
    assert out.count("[^Lezione-12]:") == 1
    assert "[^Lezione-12]: Lezione 12.md" in out.split("## Sources", 1)[1]


def test_an_existing_definition_is_reused():
    out = M.apply(NOTE + PROSE_BLOCK.replace("Lezione 12", "Lezione 11"))
    assert out.count("[^Lezione-11]:") == 1


def test_the_old_snippets_own_relations_split_from_its_prose():
    block = PROSE_BLOCK + "\n## Relations\n- [[Bound]] applies this (Lezione 12): limita.\n"
    out = M.apply(NOTE + block)
    assert out.count("## Relations") == 1
    rel = _section(out, "## Relations")
    assert "[[Iperpiano]]" in rel and "[[Bound]]" in rel
    assert "limita l'errore atteso.[^Lezione-12]" in out


def test_a_block_above_the_notes_own_relations_keeps_every_line_once():
    # 10 of 142 live blocks sit above the note's own ## Relations; the block
    # swallows it and the section is re-created above ## Sources, nothing doubled.
    note = """---
type: concept
---

# X

Prosa.

## Additional notes: X (from Lezione 12.md)

- [[Bound]] applies this (Lezione 12): limita.

## Relations
- [[Iperpiano]] defines this (Lezione 11): base.
![[fig.png]]

## Sources
[[sources/Lezione 11]]
"""
    out = M.apply(note)
    assert "Additional notes" not in out and out.count("## Relations") == 1
    rel = _section(out, "## Relations")
    for line in ("- [[Bound]] applies this (Lezione 12): limita.",
                 "- [[Iperpiano]] defines this (Lezione 11): base.", "![[fig.png]]"):
        assert rel.count(line) == 1
    assert out.index("## Relations") < out.index("## Sources")
    assert "\n\n\n" not in out


def test_the_old_cleanups_trailing_source_link_becomes_the_definition():
    # 113 of 142 live blocks end with the link CLEANUP appended at EOF, which
    # was inside the last block: not a claim, the source's own link.
    out = M.apply(NOTE + REL_BLOCK + "[[ML/Lezione 12]]\n")
    assert "- [[Bound]] applies this (Lezione 12): limita l'errore." in _section(out, "## Relations")
    assert "[[ML/Lezione 12]]" not in out.split("## Sources", 1)[0]
    assert "[[ML/Lezione 12]]" in _section(out, "## Sources")
    assert "[^Lezione-12]" not in out      # a relation bullet cites inline, no footnote to define


def test_a_trailing_link_under_prose_defines_the_label_with_the_link():
    out = M.apply(NOTE + PROSE_BLOCK + "[[ML/Lezione 12]]\n")
    assert "limita l'errore atteso.[^Lezione-12]" in out
    assert out.count("[^Lezione-12]:") == 1 and "[^Lezione-12]: [[ML/Lezione 12]]" in out
    assert _section(out, "## Sources").count("[[ML/Lezione 12]]") == 2   # the link line and the definition


def test_an_existing_definition_beats_the_trailing_link():
    out = M.apply(NOTE + PROSE_BLOCK.replace("Lezione 12", "Lezione 11") + "[[ML/Lezione 11]]\n")
    assert out.count("[^Lezione-11]:") == 1 and "[^Lezione-11]: [[sources/Lezione 11]]" in out

def test_apply_is_idempotent():
    once = M.apply(NOTE + PROSE_BLOCK + REL_BLOCK)
    assert M.apply(once) == once and M.detect(once) == 0


def test_a_heading_inside_the_relations_part_is_not_guessed():
    block = REL_BLOCK + "\n## Relations\n- [[A]] applies this (Lezione 12): a.\n\n## Something\n\ntext\n"
    with pytest.raises(migrate.Unrecognized):
        M.apply(NOTE + block)


# ---------------------------------------------------------------------------
# the vault walk and the journalled write
# ---------------------------------------------------------------------------

def _vault(tmp_path, monkeypatch):
    import silica.driver
    from silica.config import CONFIG

    v = tmp_path / "vault"
    (v / "ml").mkdir(parents=True)
    (v / "other").mkdir()
    (v / ".silica").mkdir()
    (v / "ml" / "Margine.md").write_text(NOTE + REL_BLOCK, encoding="utf-8")
    (v / "ml" / "Clean.md").write_text(NOTE, encoding="utf-8")
    (v / "other" / "Prose.md").write_text(NOTE + PROSE_BLOCK, encoding="utf-8")
    (v / ".silica" / "hidden.md").write_text(NOTE + REL_BLOCK, encoding="utf-8")
    monkeypatch.setattr(CONFIG, "vault_path", str(v))
    monkeypatch.setattr(silica.driver, "_driver", None)
    return v


def test_propose_lists_hits_and_scopes_to_a_folder(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    plans = migrate.propose(v)
    assert [(p.path, p.hits, p.skip) for p in plans] == [
        ("ml/Margine.md", 1, None), ("other/Prose.md", 1, None)]
    assert [p.path for p in migrate.propose(v, folder="other")] == ["other/Prose.md"]


def test_propose_skips_a_note_edited_by_hand(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    monkeypatch.setattr(migrate, "edited_since_write", lambda vault: ["ml/Margine.md"])
    plans = {p.path: p for p in migrate.propose(v)}
    assert plans["ml/Margine.md"].skip == "edited by hand since the gate wrote it"
    assert plans["other/Prose.md"].skip is None


def test_propose_names_an_unrecognized_shape(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    (v / "ml" / "Odd.md").write_text(
        NOTE + REL_BLOCK + "\n## Relations\n- x\n\n## Deep\n\nt\n", encoding="utf-8")
    plans = {p.path: p for p in migrate.propose(v)}
    assert plans["ml/Odd.md"].skip and "heading" in plans["ml/Odd.md"].skip


def test_apply_writes_journals_and_revert_restores_the_bytes(tmp_path, monkeypatch):
    from silica.kernel.write.undo_journal import get_undo_journal, revert_run

    v = _vault(tmp_path, monkeypatch)
    paths = ("ml/Margine.md", "other/Prose.md", "ml/Clean.md")
    before = {p: (v / p).read_text(encoding="utf-8") for p in paths}
    n, run_id = migrate.apply(v, migrate.propose(v))
    assert n == 2
    assert "Additional notes" not in (v / "ml" / "Margine.md").read_text(encoding="utf-8")
    assert (v / "ml" / "Clean.md").read_text(encoding="utf-8") == before["ml/Clean.md"]
    assert get_undo_journal().last_active_run(vault=str(v)) == run_id

    res = revert_run(run_id)
    assert sorted(res["reverted"]) == ["ml/Margine.md", "other/Prose.md"] and not res["errors"]
    for p in paths:
        assert (v / p).read_text(encoding="utf-8") == before[p]


def test_apply_leaves_the_skipped_alone(tmp_path, monkeypatch):
    v = _vault(tmp_path, monkeypatch)
    monkeypatch.setattr(migrate, "edited_since_write", lambda vault: ["ml/Margine.md"])
    n, _ = migrate.apply(v, migrate.propose(v))
    assert n == 1
    assert "Additional notes" in (v / "ml" / "Margine.md").read_text(encoding="utf-8")


def test_count_needs_neither_driver_nor_journal(tmp_path):
    # The session hook calls this on every start: a walk and a regex, nothing bound.
    v = tmp_path / "v"
    (v / "ml").mkdir(parents=True)
    (v / "ml" / "Margine.md").write_text(NOTE + REL_BLOCK, encoding="utf-8")
    (v / "ml" / "Clean.md").write_text(NOTE, encoding="utf-8")
    (v / "Prose.md").write_text(NOTE + PROSE_BLOCK, encoding="utf-8")
    assert migrate.count(v) == 2


def test_importing_migrate_stays_inside_the_hooks_budget():
    # The hook imports this module at every session start; pydantic (via ops)
    # and config cost more than the hook itself (0.28 s vs 0.13 s, 2026-09-05).
    import subprocess
    import sys

    r = subprocess.run(
        [sys.executable, "-c", "import sys, silica.kernel.write.migrate; "
         "assert 'pydantic' not in sys.modules, 'pydantic'; "
         "assert 'silica.config' not in sys.modules, 'config'"],
        capture_output=True, text=True, timeout=120,
    )
    assert r.returncode == 0, r.stderr[-400:]

# ---------------------------------------------------------------------------
# the command
# ---------------------------------------------------------------------------

def test_migrate_lists_without_writing_and_writes_on_request(tmp_path, monkeypatch, capsys):
    from silica.cli import _handle_direct_shortcut

    v = _vault(tmp_path, monkeypatch)
    before = (v / "ml" / "Margine.md").read_text(encoding="utf-8")

    assert _handle_direct_shortcut("/migrate", []) is True
    out = capsys.readouterr().out
    assert "ml/Margine.md" in out and "other/Prose.md" in out and "--write" in out
    assert (v / "ml" / "Margine.md").read_text(encoding="utf-8") == before

    assert _handle_direct_shortcut("/migrate ml --write", []) is True
    out = capsys.readouterr().out
    assert "1 note" in out and "/revert" in out
    assert "Additional notes" not in (v / "ml" / "Margine.md").read_text(encoding="utf-8")
    assert "Additional notes" in (v / "other" / "Prose.md").read_text(encoding="utf-8")
