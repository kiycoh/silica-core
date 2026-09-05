"""M2 (docs/specs/epistemic-state.md): trust and lifecycle are functions of
the frontmatter the vault already carries, derived on read and never written
by the gate. The invariants pin them to the signals they summarise: lifecycle
to `contested`/`superseded_by`/`review`, trust to `reliability_tier`.
"""
from __future__ import annotations

import pytest

from silica.kernel.write.contested import (
    lifecycle,
    mark_contested,
    mark_superseded_by,
    reliability_tier,
    trust,
)
from silica.kernel.write import frontmatter


# --- lifecycle ---------------------------------------------------------------

@pytest.mark.parametrize("data, expected", [
    (None, "active"),
    ({}, "active"),
    ({"review": "near_title candidate='X'"}, "review"),
    ({"contested": True}, "contested"),
    ({"contested": True, "review": "x"}, "contested"),
    ({"superseded_by": "[[Winner]]", "contested": True, "review": "x"}, "superseded"),
    ({"contested": False, "review": ""}, "active"),
])
def test_lifecycle_precedence(data, expected):
    assert lifecycle(data) == expected


def test_lifecycle_agrees_with_the_contested_writer():
    content = "---\ndate: 2026-01-01\n---\n\nbody\n"
    data, _raw, _body = frontmatter.split(mark_contested(content, "flagged: x (by user, 2026-09-04)"))
    assert lifecycle(data) == "contested"


def test_lifecycle_agrees_with_the_supersede_writer():
    content = "---\ndate: 2026-01-01\n---\n\nbody\n"
    data, _raw, _body = frontmatter.split(mark_superseded_by(content, "Winner"))
    assert lifecycle(data) == "superseded"


# --- trust -------------------------------------------------------------------

@pytest.mark.parametrize("content, expected", [
    ("# Hand written\n\nno frontmatter at all\n", "human"),
    ("---\nAI: true\n---\n\nbody\n\n## Sources\n\n- [[src]]\n", "grounded"),
    ("---\nAI: true\n---\n\nbody\n", "distilled"),
    ("---\nAI: true\nverified:\n  by: human:kiycoh\n  at: 2026-09-04\n---\n\nbody\n", "human"),
    ("---\nAI: partial\n---\n\nbody\n", "human"),
    ("---\nsource: web\ntags: [inbox, web]\n---\n\nbody\n\n## Sources\n\n- <http://x>\n", "kept"),
    ("---\nsource: web\nverified:\n  by: human:me\n  at: 2026-09-04\n---\n\nbody\n", "human"),
])
def test_trust_names_the_tier(content, expected):
    assert trust(content) == expected


def test_trust_is_reliability_tier_spelled_out():
    from silica.kernel.write.contested import TIER_DISTILLED, TIER_GROUNDED, TIER_HUMAN
    for content in ("no fm", "---\nAI: true\n---\n\nb\n## Sources\n- x\n", "---\nAI: true\n---\n\nb\n"):
        assert trust(content) == {TIER_HUMAN: "human", TIER_GROUNDED: "grounded",
                                  TIER_DISTILLED: "distilled"}[reliability_tier(content)]


# --- recall header and filters ----------------------------------------------

def _bind(vault, monkeypatch):
    import silica.config
    import silica.driver
    vault.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(silica.config.CONFIG, "vault_path", str(vault))
    silica.driver._driver = None


def _seed(monkeypatch, tmp_path):
    _bind(tmp_path / "v", monkeypatch)
    from silica.driver import DRIVER
    DRIVER.create("c/human.md", "# Human\n\nyoga on Monday\n")
    DRIVER.create("c/review.md", '---\nAI: true\nreview: "near_title candidate=X"\n---\n\nyoga on Tuesday\n')
    DRIVER.create("c/old.md", '---\nAI: true\nsuperseded_by: "[[c/human]]"\n---\n\nyoga on Sunday\n')
    return ["c/human", "c/review", "c/old"]


def test_perceive_header_shows_trust_and_non_active_lifecycle(tmp_path, monkeypatch):
    paths = _seed(monkeypatch, tmp_path)
    from silica.kernel.recall.perception import perceive

    ctx = perceive("yoga?", now="2026-05-01", paths=paths, use_embedder=False).render()
    # A person's active note is the silent default: the header names only
    # what deviates from it, so hand-written vaults keep their exact prompt.
    assert "[#1 | c/human]" in ctx
    assert "[#2 | c/review | trust: distilled | lifecycle: review]" in ctx
    assert "[#3 | c/old | trust: distilled | lifecycle: superseded]" in ctx


def test_perceive_min_trust_filters_and_counts(tmp_path, monkeypatch):
    paths = _seed(monkeypatch, tmp_path)
    from silica.kernel.recall.perception import perceive

    p = perceive("yoga?", now="2026-05-01", paths=paths, use_embedder=False, min_trust="human")
    assert [b.path for b in p.blocks] == ["c/human"]
    assert p.filtered == 2


def test_perceive_lifecycle_filter(tmp_path, monkeypatch):
    paths = _seed(monkeypatch, tmp_path)
    from silica.kernel.recall.perception import perceive

    p = perceive("yoga?", now="2026-05-01", paths=paths, use_embedder=False, lifecycle="active")
    assert [b.path for b in p.blocks] == ["c/human"]
    assert p.filtered == 2


def test_perceive_default_filters_nothing(tmp_path, monkeypatch):
    paths = _seed(monkeypatch, tmp_path)
    from silica.kernel.recall.perception import perceive

    p = perceive("yoga?", now="2026-05-01", paths=paths, use_embedder=False)
    assert len(p.blocks) == 3 and p.filtered == 0


def test_perceive_writes_nothing_on_the_read_path(tmp_path, monkeypatch):
    paths = _seed(monkeypatch, tmp_path)
    before = {p: (tmp_path / "v" / f"{p}.md").read_bytes() for p in paths}
    from silica.kernel.recall.perception import perceive

    perceive("yoga?", now="2026-05-01", paths=paths, use_embedder=False, min_trust="human")
    assert {p: (tmp_path / "v" / f"{p}.md").read_bytes() for p in paths} == before


def test_recall_tool_forwards_filters_and_reports_filtered(monkeypatch):
    from silica.kernel.recall.perception import Perception

    captured: dict = {}

    def fake_perceive(query, **kwargs):
        captured.update(kwargs)
        return Perception(query=query, filtered=2)

    monkeypatch.setattr("silica.kernel.recall.perception.perceive", fake_perceive)
    from silica.tools.graph import silica_recall

    out = silica_recall("q", k=5, min_trust="grounded", lifecycle="active")
    assert captured["min_trust"] == "grounded" and captured["lifecycle"] == "active"
    assert out["filtered"] == 2


def test_recall_tool_rejects_unknown_trust(monkeypatch):
    from silica.tools.graph import silica_recall

    out = silica_recall("q", k=5, min_trust="verified")
    assert "error" in out and "human" in out["error"]


# --- /find flags ---------------------------------------------------------------

def test_find_filter_drops_below_floor_and_counts(tmp_path, monkeypatch):
    _seed(monkeypatch, tmp_path)
    from silica.cli import _epistemic_filter

    rows = [{"path": "c/human", "score": 0.9}, {"path": "c/review", "score": 0.8},
            {"path": "c/old", "score": 0.7}, {"path": "c/missing", "score": 0.6}]
    kept, dropped = _epistemic_filter(rows, "human", "")
    assert [r["path"] for r in kept] == ["c/human"] and dropped == 3
    kept, dropped = _epistemic_filter(rows, "", "review")
    assert [r["path"] for r in kept] == ["c/review"] and dropped == 3
