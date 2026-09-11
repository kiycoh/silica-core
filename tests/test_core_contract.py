"""The five tools of TOOLS.md on a tiny root: shapes, statuses, errors."""
from __future__ import annotations

import json
from pathlib import Path

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
    assert by["docs/scan.pdf"]["status"] == "changed"  # unread by the index, not yet judged
    assert "mtime" not in by["docs/lsm.md"]
    assert "node_modules/" not in by and r["excluded_dirs"] == 1 and r["counts"]["excluded"] == 1
    only = {f["path"]: f for f in root.files(status="excluded")["files"]}
    assert only["node_modules/"]["reason"] == "ignore rule" and len(only) == 1
    root.build_index()
    assert root.files(status="indexed")["counts"]["indexed"] == 2
    scan = {f["path"]: f for f in root.files()["files"]}["docs/scan.pdf"]
    assert scan["status"] == "unconverted" and scan["reason"].startswith("unreadable")
    assert root.files(folder="../etc")["error"]["code"] == "out_of_root"


def test_search_locates_the_section_and_names_absent_terms(root):
    r = root.search("leveled compaction read amplification zebra")
    assert r["index"]["state"] == "ready" and r["terms_absent"] == ["zebra"]
    top = r["hits"][0]
    assert top["path"] == "docs/lsm.md" and top["section"].startswith("2 ") and top["line"] == 7
    assert "compaction" in top["matched_terms"] and 0 < top["coverage"] <= 1
    assert r["documents"][0]["path"] == "docs/lsm.md"
    assert root.search("anything")["dense"] == {"state": "off"}, "no embedder named: the reply says so, no error"


def test_read_by_section_and_version(root):
    r = root.read("docs/lsm.md", section="2 Comp")
    assert r["start"] == 7 and r["text"].startswith("## 2 Compaction") and r["outline"][2]["line"] == 7
    assert root.read("docs/lsm.md", expect_version="0" * 12)["error"]["code"] == "changed"
    assert root.read("docs/lsm.md", expect_version=r["version"])["version"] == r["version"]
    assert root.read("docs/scan.pdf")["error"]["code"] == "unconverted"
    assert root.read("docs/nope.md")["error"]["code"] == "not_found"
    assert root.read("docs/lsm.md", section="9 nothing")["error"]["code"] == "not_found"


def test_pdf_is_indexed_by_page_from_its_text_layer(root, tmp_path):
    from tests.doc_factory import pdf_bytes

    (tmp_path / "docs" / "wisckey.pdf").write_bytes(pdf_bytes([
        "WiscKey separates keys from values.",
        "Garbage collection reclaims the value log.",
    ]))
    root.build_index()
    assert root.files(status="indexed")["counts"]["indexed"] == 3

    top = root.search("garbage collection value log")["hits"][0]
    assert top["path"] == "docs/wisckey.pdf" and top["section"] == "p. 2"
    assert "garbage" in top["matched_terms"]

    r = root.read("docs/wisckey.pdf", section="p. 2")
    assert r["path"] == "docs/wisckey.pdf" and r["source"] == "docs/wisckey.pdf"
    assert r["text"].startswith("Garbage collection") and r["pages"] == 2 and r["outline"] == []
    assert r["page_map"] == [{"page": 2, "line": r["start"]}]  # the slice, not the whole book
    assert Path(r["extract_path"]).read_text(encoding="utf-8").startswith("WiscKey")
    assert root.read("docs/wisckey.pdf")["page_map"] == [{"page": 1, "line": 1}, {"page": 2, "line": r["start"]}]
    assert root.read("docs/wisckey.pdf", section="p. 9")["error"]["code"] == "not_found"
    assert root.read("docs/wisckey.pdf", expect_version="0" * 12)["error"]["code"] == "changed"

    # a `.md` beside the PDF is the note: the sidecar is indexed, the PDF is not
    (tmp_path / "docs" / "wisckey.md").write_text("# WiscKey\n\nSee [[lsm]].\n", encoding="utf-8")
    root.build_index()
    by = {f["path"]: f for f in root.files()["files"]}
    assert by["docs/wisckey.pdf"]["status"] == "excluded"
    assert by["docs/wisckey.pdf"]["reason"] == "converted: docs/wisckey.md"
    assert root.read("docs/wisckey.pdf")["path"] == "docs/wisckey.md"


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


def test_write_note_decodes_a_body_escaped_twice(root, tmp_path):
    """A model that escapes its tool arguments twice sends `\\n` and no real
    newline; anything with one real newline is written byte for byte."""
    root.write_note("esc/twice", "## Head\\n\\nline one\\n\\nline two\\n")
    assert (tmp_path / "esc" / "twice.md").read_text(encoding="utf-8") == "## Head\n\nline one\n\nline two\n"
    kept = "# Regex\n\n`split on \\n` never becomes a newline here.\n"
    root.write_note("esc/kept", kept)
    assert (tmp_path / "esc" / "kept.md").read_text(encoding="utf-8") == kept
    root.write_note("esc/plain", "one line, no escapes")
    assert (tmp_path / "esc" / "plain.md").read_text(encoding="utf-8") == "one line, no escapes"


def test_cli_replies_in_utf8_whatever_the_console_encoding(root, tmp_path, monkeypatch):
    # A piped stdout on Windows carries the console code page (cp1252), which
    # has no arrow: without the reconfigure in main() the search dies at print.
    import io
    import sys
    from silica.cli import main
    root.write_note("enc/arrow", "# Arrow\n\nleft → right\n")
    root.build_index()
    out = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", out)
    assert main(["--vault", str(tmp_path), "read", "enc/arrow.md"]) == 0
    out.flush()
    assert "left → right" in out.buffer.getvalue().decode("utf-8")


def test_cli_prints_the_tool_reply(root, tmp_path, capsys):
    from silica.cli import main
    root.build_index()
    assert main(["--vault", str(tmp_path), "files", "--status", "unconverted"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert [f["path"] for f in out["files"]] == ["docs/scan.pdf"]
    assert main(["--vault", str(tmp_path), "read", "docs/nope.md"]) == 1


def test_the_dense_leg_runs_by_itself_and_adds_a_dense_only_document(root, monkeypatch):
    from silica import embeddings
    from silica.config import CONFIG

    assert root.search("x")["dense"] == {"state": "off"}
    monkeypatch.setattr(CONFIG, "embedding_base_url", "http://127.0.0.1:9/v1")  # loopback: no consent to ask
    monkeypatch.setattr(embeddings, "_CACHE", None)
    fake = {"lsm": [1.0, 0.0], "btree": [0.0, 1.0]}  # a query embeds next to btree
    stem = lambda t: t.split("\n", 1)[0].split(" \u203a ")[0]  # the unit text opens with `<stem> › <heading>`
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: [fake.get(stem(t), [0.1, 0.9]) for t in texts])
    assert root.search("x")["dense"]["state"] == "no_vectors", "named but never built: lexical, and the reply says why"
    built = root.build_index(embed=True)
    assert built["embeddings"]["docs"] == 2 and built["embeddings"]["sections"] == 3  # `# LSM trees` alone is too short
    r = root.search("storage structure")  # no lexical overlap with either note; no parameter: the vectors are there
    assert r["terms_absent"] == ["storage", "structure"] and r["dense"] == {"state": "ready", "docs": 2}
    dense_only = [h for h in r["hits"] if h["path"] == "docs/btree.md"]
    assert dense_only and dense_only[0]["dense"] > 0.9 and dense_only[0]["matched_terms"] == []
    assert dense_only[0]["coverage"] == 0 and dense_only[0]["line"] == 1
    assert r["hits"][0]["path"] == "docs/btree.md", "the section the vector points at, first"


def test_absent_terms_weigh_on_coverage(root):
    """Three rare words the corpus never contains and one common word it
    does: the hit still comes back, and coverage says how little it covers."""
    r = root.search("zebra giraffe okapi compaction")
    assert r["terms_absent"] == ["giraffe", "okapi", "zebra"]
    assert r["hits"] and r["hits"][0]["matched_terms"] == ["compaction"]
    assert r["hits"][0]["coverage"] < 0.5, r["hits"][0]


def test_hit_version_guards_the_read(root):
    """A hit carries the hash the read checks: the file edited in between is
    refused instead of served under the hit's line."""
    hit = root.search("leveled compaction")["hits"][0]
    got = root.read(hit["path"], start=hit["line"], end=hit["line"], expect_version=hit["version"])
    assert got["version"] == hit["version"] and "compaction" in got["text"].lower()
    (root._root() / hit["path"]).write_text("# LSM trees\n\nrewritten\n", encoding="utf-8")
    assert root.read(hit["path"], expect_version=hit["version"])["error"]["code"] == "changed"


def test_scope_reports_what_the_search_could_see(root):
    """The scan the index could not read counts under the folder it sits in,
    and a term the corpus holds elsewhere is absent from the folder only."""
    r = root.search("compaction memtable", folder="docs")
    assert r["scope"] == {"folder": "docs", "docs": 2, "unconverted": 1, "failed": 0}
    assert r["terms_absent"] == [] and r["terms_absent_in_scope"] == []
    (root._root() / "notes").mkdir()
    (root._root() / "notes" / "x.md").write_text("# Notes\n\nabout pages\n", encoding="utf-8")
    r = root.search("compaction pages", folder="notes")
    assert r["scope"]["docs"] == 1 and r["scope"]["unconverted"] == 0
    assert r["terms_absent"] == [] and r["terms_absent_in_scope"] == ["compaction"]
    whole = root.search("compaction pages")
    assert "terms_absent_in_scope" not in whole and whole["scope"]["docs"] == 3


def test_documents_are_the_head_of_the_ranking_with_coverage(root):
    r = root.search("compaction memtable pages", k=1)
    assert r["candidates"] == 2 and len(r["documents"]) == 1, r["documents"]
    assert {h["path"] for h in r["hits"]} <= {d["path"] for d in r["documents"]}
    assert all(0 < d["coverage"] <= 1 for d in r["documents"])


def test_a_replaced_original_reads_as_stale_under_the_same_text_version(root, tmp_path, monkeypatch, capsys):
    """Import a PDF, replace its bytes, keep the sidecar: the served text has
    not changed, so its `version` must hold, and the reply must say the
    source did — the two are different facts, and only the hash the
    conversion recorded can tell the second."""
    import silica.driver
    from silica.cli import main
    from silica.config import CONFIG
    from tests.doc_factory import pdf_bytes

    monkeypatch.setattr(silica.driver, "_driver", None)
    monkeypatch.setattr(CONFIG, "pdf_provider", "pdfium")  # the machine's SILICA_PDF_PROVIDER may say mineru: 33 s of OCR
    (tmp_path / "docs" / "paper.pdf").write_bytes(pdf_bytes(["Compaction merges sorted runs."]))
    assert main(["--vault", str(tmp_path), "import", "docs/paper.pdf"]) == 0
    assert json.loads(capsys.readouterr().out)["notes"] == ["docs/paper.md"]
    r = root.read("docs/paper.pdf")
    assert r["path"] == "docs/paper.md" and r["source"] == "docs/paper.pdf" and r["source_state"] == "current"
    assert "Compaction merges sorted runs." in r["text"]

    (tmp_path / "docs" / "paper.pdf").write_bytes(pdf_bytes(["Compaction was rewritten."]))
    again = root.read("docs/paper.pdf", expect_version=r["version"])
    assert again["version"] == r["version"] and again["source_state"] == "stale"
    row = {f["path"]: f for f in root.files(status="excluded")["files"]}["docs/paper.pdf"]
    assert row["reason"] == "converted: docs/paper.md" and row["source_state"] == "stale"

    # a `.md` nobody converted records no hash: freshness is not knowable, and
    # a note served as itself has no source to be fresh against
    (tmp_path / "docs" / "scan.md").write_text("# By hand\n", encoding="utf-8")
    assert root.read("docs/scan.pdf")["source_state"] == "unverifiable"
    assert root.read("docs/lsm.md")["source_state"] is None


def test_the_search_to_read_path_reports_a_stale_conversion(root, tmp_path, monkeypatch):
    """Search returns the note's path, not the PDF's; reading that path is
    the ordinary route, and it must carry the same verdict as reading the
    PDF. Import A, index, replace the PDF with B, search a phrase of A."""
    import silica.driver
    from silica.config import CONFIG
    from silica.sources.convert import convert
    from tests.doc_factory import pdf_bytes

    monkeypatch.setattr(silica.driver, "_driver", None)
    monkeypatch.setattr(CONFIG, "pdf_provider", "pdfium")
    pdf = tmp_path / "docs" / "paper.pdf"
    pdf.write_bytes(pdf_bytes(["Bloom filters skip absent keys."]))
    assert convert(str(pdf), str(pdf.parent), beside=True) == ["docs/paper.md"]
    root.build_index()
    hit = root.search("bloom filters absent keys")["hits"][0]
    assert hit["path"] == "docs/paper.md"
    r = root.read(hit["path"], expect_version=hit["version"])
    assert r["source"] == "docs/paper.pdf" and r["source_state"] == "current"

    pdf.write_bytes(pdf_bytes(["Rewritten under the note."]))
    again = root.read(hit["path"], expect_version=hit["version"])
    assert again["version"] == hit["version"] and again["source_state"] == "stale"
    assert again["source"] == "docs/paper.pdf"
    rows = {f["path"]: f for f in root.files()["files"]}
    assert rows["docs/paper.md"]["source_state"] == "stale" and rows["docs/paper.md"]["source"] == "docs/paper.pdf"
    assert rows["docs/paper.pdf"]["source_state"] == "stale"

    pdf.unlink()  # the original gone: the note keeps its hash, nothing to check it against
    assert root.read("docs/paper.md")["source_state"] == "unverifiable"
    assert "source_state" not in rows["docs/lsm.md"]
