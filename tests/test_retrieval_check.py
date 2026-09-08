"""Acceptance check for `silica_search` (TOOLS.md): BM25 over documents,
sections inside the top documents, a per-document cap, the idf-weighted
window, and the honest signals (`matched_terms`, `coverage`, `terms_absent`).

Runs only with a corpus: `SILICA_BENCH_CORPUS=/path/to/markdown pytest
tests/test_retrieval_check.py -s`. The numbers in TOOLS.md come from the 254
converted papers under silica-internal/docs/research/papers/md.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

CORPUS = os.environ.get("SILICA_BENCH_CORPUS", "")
pytestmark = pytest.mark.skipif(not CORPUS, reason="set SILICA_BENCH_CORPUS to a folder of markdown")


@pytest.fixture(scope="module")
def index_dir(tmp_path_factory):
    """One private index dir for the module: the check must never touch ~/.silica."""
    return tmp_path_factory.mktemp("idx")


@pytest.fixture
def corpus(index_dir, monkeypatch):
    """Per test, because the suite's autouse isolation re-points CONFIG.vault_path
    before every test; the build is incremental, so only the first one pays."""
    from silica.config import CONFIG
    from silica.kernel.recall import paths

    monkeypatch.setenv("SILICA_VAULT", str(Path(CORPUS).resolve()))
    monkeypatch.setattr(CONFIG, "vault_path", str(Path(CORPUS).resolve()))
    monkeypatch.setattr(paths, "index_dir_for", lambda vault, _d=index_dir: _d)
    import silica.core as core

    t0 = time.time()
    built = core.build_index()
    if built["changed"]:
        print(f"\nindex: {built['docs']} docs in {time.time() - t0:.1f}s")
    return core


def test_granularity_question_returns_section_five(corpus):
    r = corpus.search("passage-level versus document-level indexing unit for retrieval over long documents")
    assert "dense-x-retrieval" in r["documents"][0]["path"]
    assert any("dense-x-retrieval" in h["path"] and h["section"].startswith("5 ") for h in r["hits"]), r["hits"]
    assert r["hits"][0]["coverage"] >= 0.8


def test_reranking_question_top_document(corpus):
    r = corpus.search("does a cross-encoder reranker over BM25 first-stage candidates improve recall")
    # BM25 as a token: the BM25-heavy listwise rerankers score close to it
    assert any("rerank-before-you-reason" in d["path"] for d in r["documents"][:5]), r["documents"][:5]
    assert r["hits"][0]["coverage"] >= 0.7
    assert len({h["path"] for h in r["hits"]}) >= 2, "per-document cap must leave room for a second paper"


def test_unanswerable_question_shows_low_coverage(corpus):
    r = corpus.search("rust borrow checker lifetime errors")
    assert r["hits"], "hits are still returned; the signal is in coverage, not in an empty list"
    assert r["hits"][0]["coverage"] < 0.5, r["hits"][0]
    assert not any({"borrow", "checker"} <= set(h["matched_terms"]) for h in r["hits"])


def test_every_hit_is_locatable(corpus):
    r = corpus.search("fixing vocabulary mismatch between query and document without embeddings")
    for h in r["hits"]:
        got = corpus.read(h["path"], start=h["line"], end=h["line"] + 40)
        assert "error" not in got and h["window"].strip().splitlines()[0].strip() in got["text"], h
        assert got["version"] == h["version"], "the hit and the read hash the same bytes"


def test_stale_version_is_refused(corpus):
    path = corpus.search("passage retrieval granularity")["hits"][0]["path"]
    v = corpus.read(path, end=1)["version"]
    assert corpus.read(path, expect_version="0" * 12)["error"]["code"] == "changed"
    assert corpus.read(path, expect_version=v)["version"] == v


def test_absent_terms_pull_the_top_hit_down(corpus):
    r = corpus.search("kubernetes ingress controller nginx annotations")
    assert r["terms_absent"], "the control query needs words the corpus never contains"
    assert r["hits"] and r["hits"][0]["coverage"] < 0.5, r["hits"][0]
