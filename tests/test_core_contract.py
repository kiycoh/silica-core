"""The five tools of TOOLS.md on a tiny root: shapes, statuses, errors."""
from __future__ import annotations

import json

import pytest


@pytest.fixture
def root(tmp_path, monkeypatch):
    from silica.config import CONFIG
    from silica.kernel.recall import paths

    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "lsm.md").write_text(
        "# LSM trees\n\n## 1 Write path\n\nWrites land in a memtable.\n\n"
        "## 2 Compaction\n\nCompaction merges sorted runs; leveled compaction bounds read amplification.\n",
        encoding="utf-8")
    (tmp_path / "docs" / "btree.md").write_text("# B-trees\n\nA B-tree keeps pages sorted; see [[lsm]].\n", encoding="utf-8")
    (tmp_path / "docs" / "scan.pdf").write_bytes(b"%PDF-1.4\0binary")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "x.md").write_text("noise", encoding="utf-8")
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(paths, "index_dir_for", lambda vault, _d=tmp_path / ".idx": _d)
    import silica.core as core
    return core


def test_default_mcp_list_is_exactly_five():
    from silica.ui.mcp import CORE_TOOLS, exposed_tools
    assert list(exposed_tools()) == list(CORE_TOOLS) and len(CORE_TOOLS) == 5


def test_files_reports_every_status(root):
    r = root.files()
    by = {f["path"]: f for f in r["files"]}
    assert r["index"]["state"] == "cold" and by["docs/lsm.md"]["status"] == "changed"
    assert by["docs/scan.pdf"]["status"] == "unconverted"
    assert by["node_modules/"]["status"] == "excluded"
    root.build_index()
    assert root.files(status="indexed")["counts"]["indexed"] == 2
    assert root.files(folder="../etc")["error"]["code"] == "out_of_root"


def test_search_locates_the_section_and_names_absent_terms(root):
    r = root.search("leveled compaction read amplification zebra")
    assert r["index"]["state"] == "ready" and r["terms_absent"] == ["zebra"]
    top = r["hits"][0]
    assert top["path"] == "docs/lsm.md" and top["section"].startswith("2 ") and top["line"] == 7
    assert "compaction" in top["matched_terms"] and 0 < top["coverage"] <= 1
    assert r["documents"][0]["path"] == "docs/lsm.md"
    assert root.search("anything", hybrid=True)["error"]["code"] == "bad_argument"


def test_read_by_section_and_version(root):
    r = root.read("docs/lsm.md", section="2 Comp")
    assert r["start"] == 7 and r["text"].startswith("## 2 Compaction") and r["outline"][2]["line"] == 7
    assert root.read("docs/lsm.md", expect_version="0" * 12)["error"]["code"] == "changed"
    assert root.read("docs/lsm.md", expect_version=r["version"])["version"] == r["version"]
    assert root.read("docs/scan.pdf")["error"]["code"] == "unconverted"
    assert root.read("docs/nope.md")["error"]["code"] == "not_found"
    assert root.read("docs/lsm.md", section="9 nothing")["error"]["code"] == "not_found"


def test_write_note_is_guarded_and_linted(root, tmp_path):
    r = root.write_note("notes/decision", "# Decision\n\nSee [[lsm]] and [[ghost]].\n\n```py\nopen", frontmatter={"type": "decision"})
    assert r["created"] and (tmp_path / "notes" / "decision.md").read_text(encoding="utf-8").startswith("---\ntype: decision\n---\n")
    kinds = {(l["kind"], l["target"]) for l in r["lint"]}
    assert ("unresolved_link", "ghost") in kinds and ("structure", "unclosed code fence") in kinds
    assert ("unresolved_link", "lsm") not in kinds
    assert root.write_note("notes/decision.md", "x", create_only=True)["error"]["code"] == "bad_argument"
    assert root.write_note("notes/decision.md", "x", expect_version="0" * 12)["error"]["code"] == "changed"
    assert root.write_note("../escape.md", "x")["error"]["code"] == "out_of_root"
    assert root.write_note("notes/decision.md", "# v2", expect_version=r["version"])["version"] != r["version"]


def test_cli_prints_the_tool_reply(root, tmp_path, capsys):
    from silica.cli import main
    assert main(["--vault", str(tmp_path), "files", "--status", "unconverted"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [f["path"] for f in out["files"]] == ["docs/scan.pdf"]
    assert main(["--vault", str(tmp_path), "read", "docs/nope.md"]) == 1


def test_hybrid_adds_a_dense_only_document(root, monkeypatch):
    from silica import embeddings
    from silica.config import CONFIG

    assert root.search("x", hybrid=True)["error"]["code"] == "bad_argument"
    monkeypatch.setattr(CONFIG, "embedding_base_url", "http://fake")
    fake = {"lsm": [1.0, 0.0], "btree": [0.0, 1.0]}  # a query embeds next to btree
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [fake.get(t.split("\n")[0], [0.1, 0.9]) for t in texts])
    assert root.search("x", hybrid=True)["error"]["code"] == "index_cold"
    built = root.build_index(embed=True)
    assert built["embeddings"]["docs"] == 2
    r = root.search("storage structure", hybrid=True)  # no lexical overlap with either note
    assert r["terms_absent"] == ["storage", "structure"]
    dense_only = [h for h in r["hits"] if h["path"] == "docs/btree.md"]
    assert dense_only and dense_only[0]["dense"] > 0.9 and dense_only[0]["matched_terms"] == []
    assert dense_only[0]["coverage"] == 0 and dense_only[0]["line"] == 1
