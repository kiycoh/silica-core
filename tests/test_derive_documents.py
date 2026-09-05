# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""ADR-0038 point 2 and 3: `documents:` derived from the working tree,
`code_ref` from the note's date, never HEAD, never a model."""
import os
import subprocess

from silica.kernel.code import codedocs, derive_documents as dd


def _repo(path):
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)


def _commit(path, msg, date):
    env = {**os.environ, "GIT_AUTHOR_DATE": f"{date}T12:00:00", "GIT_COMMITTER_DATE": f"{date}T12:00:00"}
    subprocess.run(["git", "add", "-A"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "-m", msg], cwd=path, check=True, env=env)
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=path,
                          capture_output=True, text=True).stdout.strip()


def _fixture(tmp_path):
    """Two commits a month apart; a.py cites ADR-0907, b.py cites nothing."""
    _repo(tmp_path)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.py").write_text("# why: ADR-0907\nx = 1\n", encoding="utf-8")
    (tmp_path / "src" / "b.py").write_text("y = 1\n", encoding="utf-8")
    ref_jan = _commit(tmp_path, "january", "2026-01-10")
    (tmp_path / "src" / "a.py").write_text("# why: ADR-0907\nx = 2\n", encoding="utf-8")
    ref_feb = _commit(tmp_path, "february", "2026-02-01")
    vault = tmp_path / "docs"
    (vault / "adr").mkdir(parents=True)
    (vault / "adr" / "0907-a-thing.md").write_text(
        "# ADR-0907: a thing\n\n- **Status:** Accepted (2026-01-15)\n\n## Context\n\nprose\n",
        encoding="utf-8")
    return vault, ref_jan, ref_feb


def _by_note(props):
    return {p.note_path: p for p in props}


def test_adr_regex_is_the_link_lane_twin():
    from silica.kernel.link.ast import ADR_REF_RE
    assert dd.ADR_REF_RE.pattern == ADR_REF_RE.pattern


def test_cited_lane_binds_an_adr_to_the_files_citing_it_at_its_own_date(tmp_path):
    vault, ref_jan, _ = _fixture(tmp_path)
    p = _by_note(dd.propose(vault, repo_root=tmp_path))["adr/0907-a-thing.md"]
    assert p.documents == ["src/a.py"]        # b.py cites nothing
    assert p.code_ref == ref_jan              # last commit before 2026-01-15, not HEAD
    assert p.lanes == ("cited",)


def test_mentioned_lane_binds_source_paths_spelled_in_the_body(tmp_path):
    vault, _, ref_feb = _fixture(tmp_path)
    (vault / "spec.md").write_text(
        "---\ncreated: 2026-02-05\n---\n\nThe gate lives in `src/b.py`, is explained "
        "in docs/adr/0907-a-thing.md, and src/missing.py is gone.\n", encoding="utf-8")
    p = _by_note(dd.propose(vault, repo_root=tmp_path))["spec.md"]
    assert p.documents == ["src/b.py"]        # a note is not source; a missing path is nothing
    assert p.code_ref == ref_feb
    assert p.lanes == ("mentioned",)


def test_a_note_already_bound_is_left_alone(tmp_path):
    vault, ref_jan, _ = _fixture(tmp_path)
    (vault / "bound.md").write_text(
        f"---\ndocuments:\n  - src/b.py\ncode_ref: {ref_jan}\n---\n\nsee src/a.py\n",
        encoding="utf-8")
    assert "bound.md" not in _by_note(dd.propose(vault, repo_root=tmp_path))


def test_a_note_older_than_the_repo_gets_the_first_commit(tmp_path):
    vault, ref_jan, _ = _fixture(tmp_path)
    (vault / "old.md").write_text("---\ncreated: 2020-01-01\n---\n\nsee src/b.py\n",
                                  encoding="utf-8")
    assert _by_note(dd.propose(vault, repo_root=tmp_path))["old.md"].code_ref == ref_jan


def test_mtime_is_the_date_of_last_resort(tmp_path):
    vault, ref_jan, _ = _fixture(tmp_path)
    undated = vault / "undated.md"
    undated.write_text("# no date anywhere\n\nsee src/b.py\n", encoding="utf-8")
    jan12 = 1768219200  # 2026-01-12T12:00:00Z
    os.utime(undated, (jan12, jan12))
    assert _by_note(dd.propose(vault, repo_root=tmp_path))["undated.md"].code_ref == ref_jan


def test_apply_stamps_frontmatter_and_a_second_pass_proposes_nothing(tmp_path):
    vault, ref_jan, _ = _fixture(tmp_path)
    props = dd.propose(vault, repo_root=tmp_path)
    assert dd.apply(vault, props) == 1
    text = (vault / "adr" / "0907-a-thing.md").read_text(encoding="utf-8")
    assert text.startswith("---\n") and "\n# ADR-0907: a thing\n" in text
    bound = {n: d for n, d, _ in codedocs.iter_documenting_notes(vault)}
    assert bound["adr/0907-a-thing.md"]["documents"] == ["src/a.py"]
    assert bound["adr/0907-a-thing.md"]["code_ref"] == ref_jan
    assert dd.propose(vault, repo_root=tmp_path) == []


# --- /stale --stamp ----------------------------------------------------------

from silica.cli import _handle_direct_shortcut
from silica.config import CONFIG


def test_stale_stamp_lists_without_writing_and_writes_on_request(tmp_path, monkeypatch, capsys):
    vault, ref_jan, _ = _fixture(tmp_path)
    monkeypatch.setattr(CONFIG, "vault_path", str(vault))
    adr = vault / "adr" / "0907-a-thing.md"
    before = adr.read_text(encoding="utf-8")

    assert _handle_direct_shortcut("/stale --stamp", []) is True
    out = capsys.readouterr().out
    assert "adr/0907-a-thing.md" in out and "src/a.py" in out and ref_jan[:8] in out
    assert adr.read_text(encoding="utf-8") == before      # a list, not a write

    assert _handle_direct_shortcut("/stale --stamp --write", []) is True
    assert "1 note" in capsys.readouterr().out
    assert "documents:" in adr.read_text(encoding="utf-8")


def test_propose_scopes_to_a_folder(tmp_path):
    vault, _, _ = _fixture(tmp_path)
    (vault / "spec.md").write_text("---\ncreated: 2026-02-05\n---\n\nsee src/b.py\n", encoding="utf-8")
    assert sorted(_by_note(dd.propose(vault, repo_root=tmp_path))) == ["adr/0907-a-thing.md", "spec.md"]
    assert list(_by_note(dd.propose(vault, repo_root=tmp_path, folder="adr"))) == ["adr/0907-a-thing.md"]


def test_stale_stamp_takes_a_folder(tmp_path, monkeypatch, capsys):
    vault, _, _ = _fixture(tmp_path)
    (vault / "spec.md").write_text("---\ncreated: 2026-02-05\n---\n\nsee src/b.py\n", encoding="utf-8")
    monkeypatch.setattr(CONFIG, "vault_path", str(vault))
    assert _handle_direct_shortcut("/stale --stamp adr --write", []) is True
    assert "1 note" in capsys.readouterr().out
    assert "documents:" not in (vault / "spec.md").read_text(encoding="utf-8")
