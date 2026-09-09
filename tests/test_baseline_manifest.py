# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""scripts/baseline.py: the manifest names what a run read, precisely
enough that a change in it is detected."""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _baseline():
    spec = importlib.util.spec_from_file_location("baseline", ROOT / "scripts" / "baseline.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@t", *args], cwd=repo, check=True,
                   capture_output=True)


def test_identity_names_a_dirty_checkout_and_hashes_a_plain_folder(tmp_path):
    b = _baseline()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / "a.md").write_text("one", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "one")
    clean = b.identity(repo)
    assert clean["dirty"] is False and len(clean["head"]) == 40 and clean["files"] == 1

    (repo / "a.md").write_text("two", encoding="utf-8")
    edited = b.identity(repo)
    assert edited["dirty"] and edited["head"] == clean["head"] and edited["changed"] == [" M a.md"]
    assert edited["sha256"] != clean["sha256"]
    (repo / "b.md").write_text("new", encoding="utf-8")
    assert b.identity(repo)["sha256"] != edited["sha256"]  # untracked bytes count too

    plain = tmp_path / "plain"
    plain.mkdir()
    (plain / "x.md").write_text("x", encoding="utf-8")
    (plain / ".hidden").write_text("ignored", encoding="utf-8")
    one = b.identity(plain)
    (plain / "x.md").write_text("y", encoding="utf-8")
    assert one["files"] == 1 and b.identity(plain)["sha256"] != one["sha256"]


def test_manifest_is_appended_with_tasks_corpora_and_the_skill(tmp_path):
    b = _baseline()
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    (corpus / "p.md").write_text("paper", encoding="utf-8")
    plugin = tmp_path / "plugin" / "silica" / "skills" / "silica"
    plugin.mkdir(parents=True)
    (plugin / "SKILL.md").write_text("# skill", encoding="utf-8")
    task = {"id": "T1", "cwd": corpus, "kind": "docs", "prompt": "q", "all_of": ["paper"]}
    args = {"model": "opus", "reps": 1, "plugin_dir": str(tmp_path / "plugin"), "no_skills": False}

    out = tmp_path / "out"
    out.mkdir()
    for _ in range(2):
        b.write_manifest(out, args, [task])
    lines = [json.loads(l) for l in (out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
    m = lines[-1]
    assert m["tasks"] == [{**task, "cwd": str(corpus)}] and m["args"]["model"] == "opus"
    assert m["corpora"][str(corpus)]["files"] == 1
    assert m["skill"]["sha256"] == b._sha256(b"# skill")
    assert m["runner"]["repo"]["head"] and m["runner"]["sha256"]
    assert b.write_manifest(out, {**args, "no_skills": True}, [task]) and \
        json.loads((out / "manifest.jsonl").read_text(encoding="utf-8").splitlines()[-1])["skill"] is None


def test_identity_follows_bytes_git_never_saw(tmp_path):
    """A corpus of papers sits in an ignored folder; a new folder is one
    `?? dir/` line to porcelain. Both change the bytes a task reads, so both
    change the identity, and HEAD/status ride along as annotation only."""
    b = _baseline()
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    (repo / ".gitignore").write_text("papers/\n", encoding="utf-8")
    (repo / "papers").mkdir()
    (repo / "papers" / "a.md").write_text("one", encoding="utf-8")
    _git(repo, "add", ".")
    _git(repo, "commit", "-qm", "init")
    base = b.identity(repo)
    assert base["dirty"] is False and base["files"] == 1 and len(base["head"]) == 40

    (repo / "papers" / "a.md").write_text("two", encoding="utf-8")  # ignored: status stays empty
    ignored = b.identity(repo)
    assert ignored["dirty"] is False and ignored["sha256"] != base["sha256"]

    (repo / "new").mkdir()
    (repo / "new" / "note.md").write_text("n1", encoding="utf-8")  # untracked folder: `?? new/`
    untracked = b.identity(repo)
    assert untracked["changed"] == ["?? new/"] and untracked["sha256"] != ignored["sha256"]
    (repo / "new" / "note.md").write_text("n2", encoding="utf-8")
    assert b.identity(repo)["sha256"] != untracked["sha256"]
