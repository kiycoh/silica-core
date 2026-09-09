# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""What a search does with an index that is behind, what a build leaves on
disk when it is interrupted, and what `.silicaignore` hides from the index."""
from __future__ import annotations

import orjson
import pytest


@pytest.fixture
def root(tmp_path, monkeypatch):
    from silica.config import CONFIG
    from silica.kernel.recall import paths

    for i in range(3):
        (tmp_path / f"n{i}.md").write_text(f"# Note {i}\n\ncompaction merges runs, note {i}\n", encoding="utf-8")
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(paths, "index_dir_for", lambda vault, _d=tmp_path / ".idx": _d)
    import silica.core as core
    core._section_cache.clear()
    return core


def test_state_counts_pending_and_a_small_drift_is_refreshed_inline(root, tmp_path):
    assert root.build_index()["index"] == {"state": "ready", "docs": 3, "built_at": pytest.approx(root._load_meta()["built_at"]), "pending": 0}
    (tmp_path / "n3.md").write_text("# Note 3\n\ncompaction again\n", encoding="utf-8")
    (tmp_path / "n0.md").unlink()
    assert root.index_state()["pending"] == 2  # one new, one gone
    r = root.search("compaction")
    assert r["index"]["state"] == "ready" and r["index"]["docs"] == 3
    assert {h["path"] for h in r["hits"]} <= {"n1.md", "n2.md", "n3.md"}


def test_a_drift_past_the_budget_is_served_stale(root, tmp_path, monkeypatch):
    root.build_index()
    monkeypatch.setattr(root, "STALE_BUDGET", 1)
    for i in range(3, 6):
        (tmp_path / f"n{i}.md").write_text(f"# Note {i}\n\nzebra {i}\n", encoding="utf-8")
    r = root.search("zebra")
    assert r["index"]["state"] == "stale" and r["index"]["pending"] == 3
    assert r["terms_absent"] == ["zebra"] and r["index"]["docs"] == 3, "served from the index as it was"
    assert root.build_index()["changed"] == 3
    assert root.search("zebra")["index"] == {**root.search("zebra")["index"], "state": "ready", "pending": 0}


def test_an_interrupted_build_resumes_where_it_stopped(root, tmp_path, monkeypatch):
    monkeypatch.setattr(root, "_FLUSH_S", 0.0)  # flush after every document
    real_read = root._read
    seen = []

    def read_then_die(full):
        seen.append(full.name)
        if len(seen) == 3:
            raise KeyboardInterrupt
        return real_read(full)

    monkeypatch.setattr(root, "_read", read_then_die)
    with pytest.raises(KeyboardInterrupt):
        root.build_index()
    meta = orjson.loads((tmp_path / ".idx" / "stamps.json").read_bytes())
    assert set(meta["stamps"]) == {"n0.md", "n1.md"} and meta["built_at"]
    assert root.index_state()["pending"] == 1
    monkeypatch.setattr(root, "_read", real_read)
    assert root.build_index()["changed"] == 1  # only the one it had not reached


def test_silicaignore_hides_files_by_name_and_by_path(root, tmp_path):
    (tmp_path / "templates").mkdir()
    (tmp_path / "templates" / "daily.md").write_text("# daily\n\ncompaction template\n", encoding="utf-8")
    (tmp_path / "templates" / "keep.txt").write_text("x", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n\ncompaction readme\n", encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("# docs readme\n\ncompaction nested\n", encoding="utf-8")
    (tmp_path / ".silicaignore").write_text("templates/*.md\n/README.md\n*.draft.md\n", encoding="utf-8")
    (tmp_path / "n1.draft.md").write_text("# draft\n\ncompaction draft\n", encoding="utf-8")
    root.build_index()
    hits = {h["path"] for h in root.search("compaction", k=10)["hits"]}
    assert "templates/daily.md" not in hits and "README.md" not in hits and "n1.draft.md" not in hits
    assert "docs/README.md" in hits, "an anchored pattern names the root file only"
    rows = {f["path"]: f for f in root.files(status="excluded")["files"]}
    assert rows["templates/daily.md"]["reason"] == "ignore rule"
    assert rows["README.md"]["reason"] == "ignore rule" and rows["n1.draft.md"]["reason"] == "ignore rule"
    assert "templates/keep.txt" in rows and rows["templates/keep.txt"]["reason"].startswith("not indexed")
