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


@pytest.mark.skipif(not os.environ.get("SILICA_EMBEDDING_BASE_URL") and not os.environ.get("SILICA_EMBEDDING_MODEL", "").startswith("model2vec/"),
                    reason="the dense leg needs an embedder (SILICA_EMBEDDING_BASE_URL or SILICA_EMBEDDING_MODEL=model2vec/…)")
def test_dense_leg_lifts_the_paraphrase_ceiling(corpus, monkeypatch):
    """A paraphrase with none of the paper's rare words: BM25 ranks the
    granularity paper fourth; with the vectors built it is first and one hit is its
    own section 5, the passage, not the abstract. Measured 2026-09-09 with
    nomic-embed-text through Ollama. The same question in Italian stayed
    unanswered with that English model (top cosines 0.53 on OCR papers):
    another language needs a multilingual embedder, not a bigger one."""
    from silica import embeddings
    from silica.config import CONFIG
    # the suite's isolation switches the extension off for every test; the env names the embedder here
    for field, var in (("embedding_base_url", "SILICA_EMBEDDING_BASE_URL"), ("embedding_model", "SILICA_EMBEDDING_MODEL"),
                       ("embedding_api_key", "SILICA_EMBEDDING_API_KEY")):
        if os.environ.get(var):
            monkeypatch.setattr(CONFIG, field, os.environ[var])
    monkeypatch.setattr(embeddings, "_CACHE", None)
    q = "should the unit that gets indexed be a passage or the whole document when the texts are long"
    lex = corpus.search(q)
    lex_rank = next((i for i, d in enumerate(lex["documents"]) if "dense-x-retrieval" in d["path"]), None)
    assert lex_rank is None or lex_rank >= 1, "the lexical ceiling this test is about is gone; pick a harder paraphrase"
    t0 = time.time()
    built = corpus.build_index(embed=True)
    assert "error" not in built, built.get("error")
    print(f"\nvectors: {built['embeddings']['sections']} sections, {built['embeddings']['embedded']} embedded in {time.time() - t0:.1f}s")
    r = corpus.search(q)
    assert r["dense"]["state"] == "ready" and r["dense"]["docs"] > 200, r["dense"]
    assert "dense-x-retrieval" in r["documents"][0]["path"], r["documents"][:3]
    hit = next((h for h in r["hits"] if "dense-x-retrieval" in h["path"] and h["section"].startswith("5 ")), None)
    assert hit is not None and hit["dense"] > 0.6, r["hits"][:3]
