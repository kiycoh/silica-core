# tests/test_code_lane_guards.py
"""Containment and degrade guards on the code lane's untrusted inputs:
`target` (model-authored, silica_code_pack), a source file's AST depth, and
`code_ref` read back out of note frontmatter."""
import subprocess
import sys
from pathlib import Path

import pytest

from silica.kernel.code import codeast, codegraph, codepack, gitstate


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _commit(path: Path, rel: str, text: str, msg: str) -> str:
    f = path / rel
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(text, encoding="utf-8")
    subprocess.run(["git", "add", "--", rel], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg, "--", rel], cwd=path, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, capture_output=True, text=True
    ).stdout.strip()


# ---------------------------------------------------------------------------
# codepack: the target never leaves the repo
# ---------------------------------------------------------------------------

def _repo_with_secret(tmp_path: Path) -> tuple[Path, Path]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit(repo, "m.py", "def f():\n    return 1\n", "c1")
    secret = tmp_path / "secret.txt"
    secret.write_text("PRIVATE KEY\n", encoding="utf-8")
    return repo, secret


def test_code_pack_rejects_relative_escape(tmp_path):
    repo, secret = _repo_with_secret(tmp_path)
    with pytest.raises(ValueError) as e:
        codepack.code_pack(repo, "../secret.txt")
    assert "escapes the repository" in str(e.value)


def test_code_pack_rejects_absolute_outside_target(tmp_path):
    repo, secret = _repo_with_secret(tmp_path)
    with pytest.raises(ValueError) as e:
        codepack.code_pack(repo, str(secret))
    assert "escapes the repository" in str(e.value)


def test_code_pack_rejects_symlink_leaving_the_repo(tmp_path):
    repo, secret = _repo_with_secret(tmp_path)
    (repo / "link.py").symlink_to(secret)
    with pytest.raises(ValueError):
        codepack.code_pack(repo, "link.py")


def test_code_pack_still_serves_an_in_repo_target(tmp_path):
    repo, _ = _repo_with_secret(tmp_path)
    pack = codepack.code_pack(repo, "m.py")
    assert "def f()" in pack["text"]
    # an absolute path UNDER the repo is relativized, not rejected
    assert codepack.code_pack(repo, str(repo / "m.py"))["sections"]["target"] == ["m.py"]


# ---------------------------------------------------------------------------
# codeast: a deep AST degrades to parse_error, it does not kill the lane
# ---------------------------------------------------------------------------

def _deep_source() -> str:
    # A left-nested binary chain: tree-sitter parses it iteratively, but every
    # walker descends one Python frame per level.
    return "x = 1" + " + 1" * (sys.getrecursionlimit() * 4) + "\n"


def test_extract_skeleton_degrades_on_recursion_instead_of_raising():
    sk = codeast.extract_skeleton(_deep_source(), "python", path="deep.py")
    assert sk.parse_error is True
    assert sk.symbols == []


def test_deep_file_does_not_break_a_codegraph_build(tmp_path):
    from silica.kernel.code import codegraph

    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit(repo, "ok.py", "def f():\n    return 1\n", "c1")
    _commit(repo, "deep.py", _deep_source(), "c2")
    graph = codegraph.build_codegraph(repo)
    assert graph.files["deep.py"]["parse_error"] is True
    assert [s["name"] for s in graph.files["ok.py"]["symbols"]] == ["f"]


# ---------------------------------------------------------------------------
# gitstate: a recorded ref never reaches git as an option
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("ref", ["--output=/tmp/x", "-n1", "--exec=touch /tmp/x",
                                 "a b", "HEAD;rm", ""])
def test_unsafe_refs_degrade_without_running_git(tmp_path, ref):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit(repo, "m.py", "v1\n", "c1")
    assert gitstate.show_file(repo, ref, "m.py") is None
    assert gitstate.commits_since(repo, ref, "m.py") == []
    assert gitstate.paths_touched_since(repo, ref, ["m.py"]) is None


def test_flag_shaped_ref_creates_no_file(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit(repo, "m.py", "v1\n", "c1")
    victim = tmp_path / "victim.txt"
    ref = f"--output={victim}"
    gitstate.show_file(repo, ref, "m.py")
    gitstate.commits_since(repo, ref, "m.py")
    gitstate.paths_touched_since(repo, ref, ["m.py"])
    assert not victim.exists()
    assert not list(tmp_path.glob("victim.txt*"))


def test_a_trailing_newline_is_not_a_ref(tmp_path):
    # `$` in the allowlist would accept one: it matches before a final newline,
    # so a regex naming no newline at all would still let one through.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    ref0 = _commit(repo, "m.py", "v1\n", "c1")
    assert not gitstate._is_rev(ref0 + "\n")


def test_changed_paths_refuses_an_option_shaped_range(tmp_path):
    # `git diff --output=FILE` truncates FILE, so the range is a rev like any
    # other even though this one is typed at the REPL rather than by a model.
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    _commit(repo, "m.py", "v1\n", "c1")
    victim = tmp_path / "victim.txt"
    assert gitstate.changed_paths(repo, f"--output={victim}") is None
    assert not victim.exists()


def test_real_refs_still_work(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    ref0 = _commit(repo, "m.py", "v1\n", "c1")
    _commit(repo, "m.py", "v2\n", "c2")
    assert gitstate.show_file(repo, ref0, "m.py") == "v1\n"
    assert gitstate.paths_touched_since(repo, ref0, ["m.py"]) == {"m.py"}
    assert [c.subject for c in gitstate.commits_since(repo, ref0, "m.py")] == ["c2"]


# ---------------------------------------------------------------------------
# codewiki: the digest's budget never writes into the store
# ---------------------------------------------------------------------------



def test_code_pack_names_a_parse_error_instead_of_emitting_nothing(tmp_path, monkeypatch):
    """An entry with parse_error yields no outline and no neighbourhood; the
    pack must say so in `dropped`, or the caller reads a silent degrade as
    "this file has no structure"."""
    repo = tmp_path
    _init_repo(repo)
    _commit(repo, "pkg/mod.py", "import os\n\ndef f():\n    return os.sep\n", "seed")
    monkeypatch.setattr(codegraph, "store_path", lambda: repo / "cg.json")
    graph = codegraph.load_codegraph(repo)
    broken = {p: {**e, "symbols": [], "parse_error": True} for p, e in graph.files.items()}
    monkeypatch.setattr(codegraph, "load_codegraph",
                        lambda vault: codegraph.CodeGraph(head_ref=graph.head_ref, files=broken))
    pack = codepack.code_pack(repo, "pkg/mod.py")
    assert any(d.startswith("note: pkg/mod.py did not parse") for d in pack["dropped"]), pack["dropped"]
