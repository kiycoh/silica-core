"""M3 (docs/specs/epistemic-state.md): a note whose lifecycle is not active is
served and labelled, never rank 1 and never the only base of an answer. An
agent's own flag does not count, or the model would hold a veto over its
memory.
"""
from __future__ import annotations

from silica.kernel.write.contested import contested_by_agents_only

LONG = ("filler sentence about nothing in particular. " * 12
        + "yoga class is on Tuesday at six. "
        + "more filler about the weather and the garden. " * 12
        + "the yoga teacher is called Maya. "
        + "closing filler lines that pad the note to several windows. " * 12)


def test_agent_only_contest_is_recognised():
    assert contested_by_agents_only(["flagged: wrong (by coder-7, 2026-09-04)"]) is True
    assert contested_by_agents_only(["flagged: wrong (by user, 2026-09-04)"]) is False
    assert contested_by_agents_only(["flagged: wrong (by human:kiycoh, 2026-09-04)"]) is False
    assert contested_by_agents_only(["lez-03.md"]) is False  # a judge ref names the source
    assert contested_by_agents_only(["lez-03.md", "flagged: x (by coder-7, 2026-09-04)"]) is False
    assert contested_by_agents_only([]) is False


def _bind(vault, monkeypatch):
    import silica.config
    import silica.driver
    vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(silica.config.CONFIG, "vault_path", str(vault))
    silica.driver._driver = None


def _seed(monkeypatch, tmp_path):
    _bind(tmp_path / "v", monkeypatch)
    from silica.driver import DRIVER
    DRIVER.create("c/review.md", f'---\nAI: true\nreview: "near_title X"\n---\n\n{LONG}')
    DRIVER.create("c/old.md", '---\nAI: true\nsuperseded_by: "[[c/active]]"\n---\n\nyoga on Sunday\n')
    DRIVER.create("c/bad.md", '---\ncontested: true\ncontradictions:\n'
                              '  - "flagged: wrong day (by user, 2026-05-01)"\n---\n\nyoga on Monday\n')
    DRIVER.create("c/active.md", f"---\ndate: 2026-01-01\n---\n\n{LONG}")


def test_non_active_blocks_are_demoted_behind_active_ones_stably(tmp_path, monkeypatch):
    _seed(monkeypatch, tmp_path)
    from silica.kernel.recall.perception import perceive

    p = perceive("yoga?", now="2026-05-01", paths=["c/review", "c/old", "c/bad", "c/active"],
                 use_embedder=False)
    assert [b.path for b in p.blocks] == ["c/active", "c/review", "c/old", "c/bad"]
    assert p.all_non_active is False


def test_non_active_block_gets_a_single_window(tmp_path, monkeypatch):
    _seed(monkeypatch, tmp_path)
    from silica.kernel.recall.perception import perceive

    p = perceive("yoga Tuesday Maya", now="2026-05-01", paths=["c/review", "c/active"],
                 use_embedder=False, windows=3, window_chars=120)
    by = {b.path: b for b in p.blocks}
    assert "[…]" in by["c/active"].excerpt      # rank 1, active: the multi-window head
    assert "[…]" not in by["c/review"].excerpt  # non-active: one window, however it ranked


def test_all_non_active_evidence_renders_the_abstain_rule(tmp_path, monkeypatch):
    _seed(monkeypatch, tmp_path)
    from silica.kernel.recall.perception import perceive

    p = perceive("yoga?", now="2026-05-01", paths=["c/review", "c/old", "c/bad"], use_embedder=False)
    assert p.all_non_active is True
    ctx = p.render()
    assert ctx.startswith("[every recalled note is contested, under review, or superseded")
    assert "abstain" in ctx.splitlines()[0]


def test_agent_flag_alone_neither_demotes_nor_abstains(tmp_path, monkeypatch):
    _bind(tmp_path / "v", monkeypatch)
    from silica.driver import DRIVER
    DRIVER.create("c/agentflag.md", '---\ncontested: true\ncontradictions:\n'
                                    '  - "flagged: dubious (by coder-7, 2026-05-01)"\n---\n\nyoga on Monday\n')
    DRIVER.create("c/plain.md", "---\ndate: 2026-01-01\n---\n\nyoga on Tuesday\n")
    from silica.kernel.recall.perception import perceive

    p = perceive("yoga?", now="2026-05-01", paths=["c/agentflag", "c/plain"], use_embedder=False)
    assert [b.path for b in p.blocks] == ["c/agentflag", "c/plain"]  # input order kept
    assert p.all_non_active is False
    assert "contested: flagged: dubious" in p.render()  # still labelled, never hidden
    p2 = perceive("yoga?", now="2026-05-01", paths=["c/agentflag"], use_embedder=False)
    assert p2.all_non_active is False


def test_recall_tool_reports_all_non_active(monkeypatch):
    from silica.kernel.recall.perception import NoteBlock, Perception

    canned = Perception(query="q", blocks=[NoteBlock(path="c/bad", date="", evidence="",
                                                     body="b", excerpt="b", lifecycle="contested")],
                        all_non_active=True)
    monkeypatch.setattr("silica.kernel.recall.perception.perceive", lambda *a, **k: canned)
    from silica.tools.graph import silica_recall

    out = silica_recall("q", k=5)
    assert out["all_non_active"] is True
