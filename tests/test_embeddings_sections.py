# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""The dense leg, section by section: a paraphrase lands on the passage, a
changed file's vectors are ignored, text leaves the machine only with
consent; and `queries`, several groups fused by rank."""
from __future__ import annotations

import os
import re

import pytest

# A bag-of-words embedder over a synonym-folded vocabulary: cosine is high
# between texts that say the same thing in different words, which is the
# one property of a real embedder the search relies on.
_SYN = {"car": "auto", "cars": "auto", "automobile": "auto", "vehicle": "auto", "dog": "canine", "hound": "canine"}
_VOCAB = ["auto", "canine", "engine", "wheels", "compaction", "memtable", "barks"]


def fake_embed(texts):
    out = []
    for t in texts:
        toks = [_SYN.get(w, w) for w in re.findall(r"\w+", t.lower())]
        out.append([float(toks.count(w)) for w in _VOCAB])
    return out


@pytest.fixture
def root(tmp_path, monkeypatch):
    from silica_core.config import CONFIG
    from silica_core.kernel.recall import paths
    from silica_core import embeddings

    (tmp_path / "a.md").write_text("# Cars\n\n## Engines\n\nAn automobile has an engine.\n\n## Wheels\n\nA vehicle rolls on wheels.\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("# Dogs\n\n## Breeds\n\nA hound barks at night.\n", encoding="utf-8")
    (tmp_path / "c.md").write_text("# LSM\n\n## Compaction\n\ncompaction merges memtable runs\n", encoding="utf-8")
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "embedding_base_url", "http://localhost:9/v1")  # loopback: nothing leaves
    monkeypatch.setattr(CONFIG, "embedding_model", "fake")
    monkeypatch.setattr(paths, "index_dir_for", lambda vault, _d=tmp_path / ".idx": _d)
    monkeypatch.setattr(embeddings, "embed_texts", fake_embed)
    monkeypatch.setattr(embeddings, "_CACHE", None)
    import silica_core.core as core
    core._section_cache.clear()
    return core


def test_a_paraphrase_lands_on_the_section_not_the_opening(root):
    assert root.search("car")["dense"]["state"] == "no_vectors"
    built = root.build_index(embed=True)
    assert built["embeddings"] == {"docs": 3, "sections": 4, "embedded": 4, "model": "fake"}  # the `# Cars` preamble is too short to embed
    r = root.search("car")
    assert r["terms_absent"] == ["car"], "the lexical leg has nothing"
    assert r["dense"] == {"state": "ready", "docs": 3}
    top = r["hits"][0]
    assert top["path"] == "a.md" and top["section"] in {"Engines", "Wheels"} and top["matched_terms"] == []
    assert top["coverage"] == 0 and top["dense"] > 0.5 and top["score"] == 0
    assert r["documents"][0]["path"] == "a.md" and r["documents"][0]["dense"] == top["dense"]
    both = root.search("compaction")["hits"][0]
    assert both["path"] == "c.md" and both["matched_terms"] == ["compaction"] and both["dense"] > 0.5


def test_a_changed_file_keeps_no_vectors(root, tmp_path):
    root.build_index(embed=True)
    a = tmp_path / "a.md"
    a.write_text("# Cars\n\n## Engines\n\nRewritten without the word.\n", encoding="utf-8")
    os.utime(a, (a.stat().st_atime, a.stat().st_mtime + 10))
    r = root.search("car")
    assert r["index"]["state"] == "ready", "the lexical index refreshed inline"
    assert r["dense"] == {"state": "ready", "docs": 2}, "the changed document is not among the covered"
    assert not any(h["path"] == "a.md" for h in r["hits"]), "stale vectors describe text that is gone"
    assert root.build_index(embed=True)["embeddings"]["embedded"] == 1, "only the changed document is re-embedded"


def test_text_leaves_the_machine_only_with_consent(root, monkeypatch):
    from silica_core.config import CONFIG
    monkeypatch.setattr(CONFIG, "embedding_base_url", "https://api.example.com/v1")
    r = root.build_index(embed=True)
    assert r["error"]["code"] == "consent_required" and "api.example.com" in r["error"]["hint"]
    assert r["docs"] == 3, "the lexical index was still built"
    assert root.build_index(embed=True, allow_remote=True)["embeddings"]["docs"] == 3
    assert "error" not in root.build_index(embed=True), "granted once, remembered"
    assert root.search("car")["hits"][0]["path"] == "a.md"
    monkeypatch.setattr(CONFIG, "embedding_base_url", "https://other.example.net/v1")
    r = root.search("car")
    assert r["dense"]["state"] == "consent_required" and "other.example.net" in r["dense"]["hint"]
    assert r["hits"] == [], "lexical only, and `car` is a word the corpus never contains"


def test_a_failing_endpoint_leaves_the_search_lexical(root, monkeypatch):
    from silica_core import embeddings
    root.build_index(embed=True)

    def refused(texts):
        raise RuntimeError("connection refused")
    monkeypatch.setattr(embeddings, "embed_texts", refused)
    r = root.search("compaction")
    assert r["dense"]["state"] == "failed" and "connection refused" in r["dense"]["hint"]
    assert r["hits"][0]["path"] == "c.md" and "dense" not in r["hits"][0], "the lexical hit, on its own"


def test_query_groups_fuse_by_rank(root):
    r = root.search("memtable", queries=["hound"])
    assert r["queries"] == ["hound"] and r["candidates"] == 2
    assert {h["path"] for h in r["hits"]} == {"b.md", "c.md"}
    assert sorted(r["hits"], key=lambda h: h["path"])[0]["matched_terms"] == ["hound"]
    assert r["terms_absent"] == []
    one = root.search("memtable hound")
    assert {h["path"] for h in one["hits"]} == {"b.md", "c.md"}, "one group with both words finds both too"
    assert "queries" not in one


def test_the_store_is_read_once_while_unchanged(root):
    from silica_core import embeddings
    root.build_index(embed=True)
    first = embeddings.load()
    assert first is not None and embeddings.load()[1] is first[1]


def test_pure_python_and_numpy_dot_products_agree(root, monkeypatch):
    import sys
    from silica_core import embeddings
    root.build_index(embed=True)
    with_numpy = root.search("car")["hits"]
    monkeypatch.setitem(sys.modules, "numpy", None)  # `import numpy` now raises ImportError
    monkeypatch.setattr(embeddings, "_CACHE", None)
    without = root.search("car")["hits"]
    assert [(h["path"], h["section"], h["dense"]) for h in with_numpy] == [(h["path"], h["section"], h["dense"]) for h in without]


def test_cli_also_adds_a_query_group(root, tmp_path, capsys):
    import json
    from silica_core.cli import main
    assert main(["--vault", str(tmp_path), "search", "memtable", "--also", "hound", "-k", "5"]) == 0
    r = json.loads(capsys.readouterr().out)
    assert r["queries"] == ["hound"] and {h["path"] for h in r["hits"]} == {"b.md", "c.md"}


def test_prefixes_reach_the_embedder_on_both_sides(root, monkeypatch):
    """nomic-embed-text wants `search_document: ` on what is indexed and
    `search_query: ` on the question (its card says the prefix must be
    there); potion and qwen3 want neither, so both are empty by default and
    named per model. A changed document prefix means the stored vectors
    describe other text: they are rebuilt."""
    from silica_core.config import CONFIG
    from silica_core import embeddings

    seen: list[str] = []

    def spy(texts):
        seen.extend(texts)
        return fake_embed(texts)

    monkeypatch.setattr(embeddings, "embed_texts", spy)
    monkeypatch.setattr(CONFIG, "embedding_doc_prefix", "search_document: ")
    monkeypatch.setattr(CONFIG, "embedding_query_prefix", "search_query: ")
    assert root.build_index(embed=True)["embeddings"]["embedded"] == 4
    assert seen and all(t.startswith("search_document: ") for t in seen)
    assert sorted(t.split(" › ")[0] for t in seen) == ["search_document: a", "search_document: a", "search_document: b", "search_document: c"]
    seen.clear()
    assert root.search("car")["hits"][0]["path"] == "a.md"
    assert seen == ["search_query: car"]
    # the same files under another document prefix are embedded again
    monkeypatch.setattr(CONFIG, "embedding_doc_prefix", "")
    assert root.build_index(embed=True)["embeddings"]["embedded"] == 4
    # and until then, a store built under another model or prefix is not served
    monkeypatch.setattr(CONFIG, "embedding_doc_prefix", "search_document: ")
    assert root.search("car")["dense"] == {"state": "no_vectors", "hint": "run `silica-core index --embed`"}
    monkeypatch.setattr(CONFIG, "embedding_doc_prefix", "")
    monkeypatch.setattr(CONFIG, "embedding_model", "other")
    assert root.search("car")["dense"]["state"] == "no_vectors"


def test_an_unchanged_build_rewrites_nothing(root, tmp_path):
    root.build_index(embed=True)
    files = [tmp_path / ".idx" / "embed.q8", tmp_path / ".idx" / "embed.json"]
    before = [f.stat().st_mtime_ns for f in files]
    assert root.build_index(embed=True)["embeddings"] == {"docs": 3, "sections": 4, "embedded": 0, "model": "fake"}
    assert [f.stat().st_mtime_ns for f in files] == before


def test_rows_are_int8_and_a_float32_store_converts_without_re_embedding(root, tmp_path, monkeypatch):
    """embed.q8 is one float32 scale and dim int8 codes per row. A store
    written before it (float32 rows in embed.f32) still serves, and the next
    build rewrites it as int8 from the stored rows, not from the embedder."""
    from array import array
    import orjson
    from silica_core import embeddings
    root.build_index(embed=True)
    idx = tmp_path / ".idx"
    meta = orjson.loads((idx / "embed.json").read_bytes())
    n, dim = meta["rows"], meta["dim"]
    assert (idx / "embed.q8").stat().st_size == n * (4 + dim)
    hits = [(h["path"], h["section"], h["dense"]) for h in root.search("car")["hits"]]
    _meta, rows = embeddings.load()
    legacy = array("f", [rows.scales[i] * c for i in range(n) for c in rows.codes[i * dim:(i + 1) * dim]])
    (idx / "embed.f32").write_bytes(legacy.tobytes())
    (idx / "embed.q8").unlink()
    meta.pop("codes")
    (idx / "embed.json").write_bytes(orjson.dumps(meta))
    monkeypatch.setattr(embeddings, "_CACHE", None)
    assert [(h["path"], h["section"], h["dense"]) for h in root.search("car")["hits"]] == hits
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: pytest.fail(f"re-embedded {len(texts)} sections"))
    assert root.build_index(embed=True)["embeddings"]["embedded"] == 0
    assert (idx / "embed.q8").exists() and not (idx / "embed.f32").exists()


def test_numpy_and_pure_python_quantize_to_the_same_bytes(monkeypatch):
    import random
    import sys
    from silica_core import embeddings
    rng = random.Random(0)
    vecs = [[rng.gauss(0, 1) for _ in range(64)] for _ in range(50)] + [[0.0] * 64, [0.5] * 64]
    with_numpy = embeddings._quantize(vecs)
    monkeypatch.setitem(sys.modules, "numpy", None)
    assert embeddings._quantize(vecs) == with_numpy


@pytest.mark.parametrize("save_s, saved_before_failure", [(0.0, 1), (3600.0, 0)])
def test_a_build_the_embedder_stops_keeps_what_it_embedded_and_the_next_does_the_rest(
        root, tmp_path, monkeypatch, save_s, saved_before_failure):
    """A library's first build is hours: one that saved only at its end lost
    them all to a closed app or one refused request (2026-11-08). Saved on
    the way (save_s 0), and on the failure itself when no save came due."""
    import orjson
    from silica_core import embeddings
    monkeypatch.setattr(embeddings, "_BATCH", 1)  # one request per document
    monkeypatch.setattr(embeddings, "_SAVE_S", save_s)
    meta = tmp_path / ".idx" / "embed.json"
    calls: list[int] = []
    on_disk_at_failure: list[int] = []

    def flaky(texts):
        calls.append(len(texts))
        if len(calls) == 2:
            on_disk_at_failure.append(len(orjson.loads(meta.read_bytes())["docs"]) if meta.exists() else 0)
            raise RuntimeError("Context size has been exceeded")
        return fake_embed(texts)

    monkeypatch.setattr(embeddings, "embed_texts", flaky)
    assert root.build_index(embed=True)["error"]["code"] == "bad_argument"
    assert on_disk_at_failure == [saved_before_failure]
    assert len(orjson.loads(meta.read_bytes())["docs"]) == 1, "the failure itself saved the document before it"
    first = calls[0]
    calls.clear()
    monkeypatch.setattr(embeddings, "embed_texts", lambda texts: calls.append(len(texts)) or fake_embed(texts))
    assert root.build_index(embed=True)["embeddings"] == {"docs": 3, "sections": 4, "embedded": 4 - first, "model": "fake"}
    assert sum(calls) == 4 - first
    assert root.search("car")["hits"][0]["path"] == "a.md"


def test_a_refresh_reads_only_the_documents_it_embeds(root, tmp_path, monkeypatch):
    """Reading every section to learn nothing changed cost 68 s and 957 MB on
    131 books at each start (2026-11-08)."""
    root.build_index(embed=True)
    read: list[str] = []
    real = root._sections_of
    monkeypatch.setattr(root, "_sections_of", lambda rel: read.append(rel) or real(rel))
    assert root.build_index(embed=True)["embeddings"]["embedded"] == 0 and read == []
    b = tmp_path / "b.md"
    b.write_text("# Dogs\n\n## Breeds\n\nA hound barks at dawn.\n", encoding="utf-8")
    os.utime(b, (b.stat().st_atime, b.stat().st_mtime + 10))
    assert root.build_index(embed=True)["embeddings"]["embedded"] == 1 and read == ["b.md"]


def test_a_build_reports_its_progress_reading_then_embedding(root):
    seen: list[tuple[str, int, int]] = []
    root.build_index(embed=True, progress=lambda *a: seen.append(a))
    index = [s for s in seen if s[0] == "index"]
    vectors = [s for s in seen if s[0] == "vectors"]
    assert index[0] == ("index", 0, 3) and index[-1] == ("index", 3, 3)
    assert vectors == [("vectors", 0, 3), ("vectors", 1, 3), ("vectors", 2, 3), ("vectors", 3, 3)]
    assert seen.index(index[-1]) < seen.index(vectors[0])


def test_a_build_stays_on_its_root_when_the_app_switches_vault_under_it(root, tmp_path, tmp_path_factory, monkeypatch):
    """An app that switches vaults in its own process moves CONFIG under a
    build of hours; every save of it read CONFIG again (2026-11-08)."""
    from pathlib import Path
    from silica_core.config import CONFIG
    from silica_core.kernel.recall import paths
    other = tmp_path_factory.mktemp("other-vault")
    (other / "z.md").write_text("# Z\n\n## Zed\n\nnothing about cars here at all\n", encoding="utf-8")
    monkeypatch.setattr(paths, "index_dir_for", lambda vault: tmp_path.parent / f"idx-{Path(vault).name}")

    def switch(stage, done, total):
        if (stage, done) == ("vectors", 1):
            CONFIG.vault_path = str(other)

    built = root.build_index(embed=True, progress=switch)

    assert built["embeddings"]["docs"] == 3
    assert (tmp_path.parent / f"idx-{tmp_path.name}" / "embed.json").exists()
    assert not (tmp_path.parent / f"idx-{other.name}").exists(), "nothing of the old vault in the new one's folder"
    assert root.search("car")["dense"]["state"] == "no_vectors", "the new vault is served as its own"
