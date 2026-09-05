# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""ADR-0038: detection at session open. The stale lane prunes like every
other vault walk, the snapshot carries the documented map, and the hook
reads drift without ever recomputing."""
import subprocess

from silica.kernel.code import codedocs


def _repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _commit(path, msg="c"):
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=path, check=True)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                          capture_output=True, text=True).stdout.strip()


def _note(path, ref):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\ndocuments:\n  - src/m.py\ncode_ref: {ref}\n---\n\nbody\n",
                    encoding="utf-8")


def test_documenting_walk_prunes_hidden_and_ignored_dirs(tmp_path):
    # Measured 2026-09-04 on the repo vault: rglob entered .silica/ (242
    # residue notes of a killed probe) and .venv/, 17.8 s for 4 real notes.
    vault = tmp_path
    _note(vault / "docs" / "real.md", "abc")
    _note(vault / ".silica" / "probe" / "ghost.md", "abc")
    _note(vault / ".venv" / "pkg" / "readme.md", "abc")
    _note(vault / "node_modules" / "x" / "doc.md", "abc")
    (vault / ".silicaignore").write_text("bench/\n", encoding="utf-8")
    _note(vault / "bench" / "fixture.md", "abc")
    assert [p for p, _, _ in codedocs.iter_documenting_notes(vault)] == ["docs/real.md"]


def _fixture(tmp_path):
    """Repo with two documented sources; the note on m.py is stale, n.py's is not."""
    _repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "m.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    (tmp_path / "src" / "n.py").write_text("def g():\n    return 1\n", encoding="utf-8")
    ref0 = _commit(tmp_path)
    vault = tmp_path / "docs"
    _note(vault / "m.md", ref0)
    (vault / "n.md").write_text(
        f"---\ndocuments:\n  - src/n.py\ncode_ref: {ref0}\n---\n\nbody\n", encoding="utf-8")
    (tmp_path / "src" / "m.py").write_text("def f(x):\n    return x\n", encoding="utf-8")
    ref1 = _commit(tmp_path, "structural change")
    return vault, ref0, ref1


def test_snapshot_file_carries_the_documented_map(tmp_path):
    vault, ref0, _ = _fixture(tmp_path)
    codedocs.snapshot(vault, repo_root=tmp_path)
    import json
    raw = json.loads(codedocs._snapshot_path(vault).read_text(encoding="utf-8"))
    # Every documenting note, stale or not: the fresh one is what a later
    # diff must be joined against.
    assert raw["documented"] == {
        "m.md": {"documents": ["src/m.py"], "code_ref": ref0},
        "n.md": {"documents": ["src/n.py"], "code_ref": ref0},
    }


def test_drift_joins_the_diff_since_the_snapshot_head_without_recomputing(tmp_path, monkeypatch):
    vault, _, ref1 = _fixture(tmp_path)
    codedocs.snapshot(vault, repo_root=tmp_path)
    (tmp_path / "src" / "n.py").write_text("def g(y):\n    return y\n", encoding="utf-8")
    (tmp_path / "src" / "other.py").write_text("x = 1\n", encoding="utf-8")
    ref2 = _commit(tmp_path, "n moves too")

    def boom(*a, **k):
        raise AssertionError("drift must never pay the walk")
    monkeypatch.setattr(codedocs, "stale_docs", boom)

    d = codedocs.drift(vault, repo_root=tmp_path)
    assert d["snap_head"] == ref1 and d["head"] == ref2
    assert d["changed"] == ["src/n.py"]                 # other.py documents nothing
    assert d["affected"] == {"n.md": ["src/n.py"]}
    assert d["stale"] == {"m.md": codedocs.CHANGE_STRUCTURAL}   # as of the snapshot


def test_drift_at_the_snapshot_head_has_nothing_new(tmp_path):
    vault, _, ref1 = _fixture(tmp_path)
    codedocs.snapshot(vault, repo_root=tmp_path)
    d = codedocs.drift(vault, repo_root=tmp_path)
    assert d["snap_head"] == d["head"] == ref1
    assert d["changed"] == [] and d["affected"] == {}
    assert d["stale"] == {"m.md": codedocs.CHANGE_STRUCTURAL}


def test_drift_without_a_snapshot_is_none(tmp_path):
    vault, _, _ = _fixture(tmp_path)
    assert codedocs.drift(vault, repo_root=tmp_path) is None


# --- the session-open brief -------------------------------------------------

import json

from silica import hook as hook_mod


def _vault_marker(vault):
    (vault / "vault.yaml").write_text("write_dir: silica\n", encoding="utf-8")


def test_session_start_reports_drift_and_stale_from_the_snapshot(tmp_path, monkeypatch):
    vault, _, ref1 = _fixture(tmp_path)
    _vault_marker(vault)
    codedocs.snapshot(vault, repo_root=tmp_path)
    (tmp_path / "src" / "n.py").write_text("def g(y):\n    return y\n", encoding="utf-8")
    _commit(tmp_path, "n moves after the snapshot")

    def boom(*a, **k):
        raise AssertionError("the hook must never pay the walk")
    monkeypatch.setattr(codedocs, "stale_docs", boom)

    out = hook_mod.session_start(json.dumps({"cwd": str(vault)}))
    assert str(vault) in out                      # the existing brief survives
    assert "n.md" in out and "src/n.py" in out    # drift since the snapshot
    assert "m.md" in out                          # structural stale at the snapshot
    assert ref1[:8] in out and "/stale" in out


def test_session_start_is_the_plain_brief_without_a_snapshot(tmp_path):
    vault, _, _ = _fixture(tmp_path)
    _vault_marker(vault)
    out = hook_mod.session_start(json.dumps({"cwd": str(vault)}))
    assert str(vault) in out and "/stale" not in out


def test_session_start_is_the_plain_brief_when_nothing_moved(tmp_path):
    vault, _, _ = _fixture(tmp_path)
    _vault_marker(vault)
    (vault / "m.md").unlink()                     # only the fresh note remains
    codedocs.snapshot(vault, repo_root=tmp_path)
    out = hook_mod.session_start(json.dumps({"cwd": str(vault)}))
    assert str(vault) in out and "/stale" not in out


def test_drift_ignores_cached_entries_under_hidden_dirs(tmp_path):
    # A snapshot written before the walk was pruned can still hold residue
    # under .silica/; the brief must not resurrect it (seen live 2026-09-04).
    vault, _, _ = _fixture(tmp_path)
    codedocs.snapshot(vault, repo_root=tmp_path)
    cache = codedocs._snapshot_path(vault)
    raw = json.loads(cache.read_text(encoding="utf-8"))
    ghost = dict(raw["docs"][0], note_path=".silica/probe/ghost.md")
    raw["docs"].append(ghost)
    raw["documented"][".silica/probe/ghost.md"] = {"documents": ["src/n.py"], "code_ref": "x"}
    cache.write_text(json.dumps(raw), encoding="utf-8")
    (tmp_path / "src" / "n.py").write_text("def g(y):\n    return y\n", encoding="utf-8")
    _commit(tmp_path, "n moves")
    d = codedocs.drift(vault, repo_root=tmp_path)
    assert d["stale"] == {"m.md": codedocs.CHANGE_STRUCTURAL}
    assert d["affected"] == {"n.md": ["src/n.py"]}
