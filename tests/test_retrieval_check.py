"""Reference implementation and acceptance check for the `silica_search`
recipe in TOOLS.md: BM25 over documents, BM25 over heading sections inside
the top documents, a per-document cap, the idf-weighted window, and the
honest signals (`matched_terms`, `coverage`, `terms_absent`).

Runs only with a corpus: `SILICA_BENCH_CORPUS=/path/to/markdown pytest
tests/test_retrieval_check.py -s`. The numbers in TOOLS.md come from the 254
converted papers under silica-internal/docs/research/papers/md. Once
`silica.core.search` exists, `search()` below is replaced by a call to it and
the asserts stay.
"""
from __future__ import annotations

import math
import os
import re
import time
from pathlib import Path

import pytest

from silica.kernel.recall.lexical import LexicalStore, _tokens
from silica.kernel.recall.rerank import _query_terms, best_window_spans

CORPUS = os.environ.get("SILICA_BENCH_CORPUS", "")
pytestmark = pytest.mark.skipif(not CORPUS, reason="set SILICA_BENCH_CORPUS to a folder of markdown")

K1, B = 1.5, 0.75


def _bm25(store: LexicalStore, query: str) -> tuple[list[tuple[str, float, set[str]]], dict[str, float | None]]:
    """Raw BM25 per path plus the matched terms; idf per query term (None = absent)."""
    n = len(store._docs)
    avg = sum(store._len.values()) / n
    idf: dict[str, float | None] = {}
    score: dict[str, float] = {}
    matched: dict[str, set[str]] = {}
    for t in set(_tokens(query)):
        post = store._postings.get(t, {})
        df = len(post)
        idf[t] = math.log(1 + (n - df + 0.5) / (df + 0.5)) if df else None
        if not df:
            continue
        for p, f in post.items():
            dl = store._len[p] or 1
            score[p] = score.get(p, 0.0) + idf[t] * (f * (K1 + 1)) / (f + K1 * (1 - B + B * dl / avg))
            matched.setdefault(p, set()).add(t)
    ranked = sorted(score.items(), key=lambda kv: -kv[1])
    return [(p, s, matched[p]) for p, s in ranked], idf


class Corpus:
    def __init__(self, root: Path, tmp: Path):
        self.texts = {str(f.relative_to(root)): f.read_text(errors="replace") for f in sorted(root.rglob("*.md"))}
        t0 = time.time()
        self.docs = LexicalStore(tmp / "docs.json")
        self.secs = LexicalStore(tmp / "secs.json")
        self.secmap: dict[str, tuple[str, int, str, str]] = {}
        for p, body in self.texts.items():
            self.docs.upsert(p, Path(p).stem, body)
            off = 0
            for i, part in enumerate(re.split(r"(?m)^(?=#{1,3} )", body)):
                if part.strip():
                    title = part.splitlines()[0].lstrip("# ").strip()
                    key = f"{p}#{i}"
                    self.secmap[key] = (p, off, title, part)
                    self.secs.upsert(key, title, part)
                off += len(part)
        self.build_s = time.time() - t0

    def search(self, query: str, k: int = 5, per_doc: int = 2, width: int = 600) -> dict:
        doc_rank, idf = self._ranked_docs(query)
        top_docs = [p for p, _s, _m in doc_rank[: max(k, 3)]]
        sec_rank, _ = _bm25(self.secs, query)
        known = {t: v for t, v in idf.items() if v is not None}
        mass = sum(known.values()) or 1.0
        weights = self.secs.query_idf(_query_terms(query))
        hits, seen = [], {}
        for key, score, matched in sec_rank:
            p, off, title, part = self.secmap[key]
            if p not in top_docs or seen.get(p, 0) >= per_doc:
                continue
            seen[p] = seen.get(p, 0) + 1
            o, window = best_window_spans(part, query, width, 1, weights, snap=True)[0]
            hits.append({"path": p, "section": title, "line": self.texts[p].count("\n", 0, off + o) + 1,
                         "score": round(score, 2), "matched_terms": sorted(matched),
                         "coverage": round(sum(known[t] for t in matched) / mass, 2), "window": window})
            if len(hits) == k:
                break
        return {"query": query, "hits": hits, "top_document": top_docs[0] if top_docs else None,
                "candidates": len(doc_rank), "terms_absent": sorted(t for t, v in idf.items() if v is None)}

    def _ranked_docs(self, query: str):
        return _bm25(self.docs, query)


@pytest.fixture(scope="module")
def corpus(tmp_path_factory) -> Corpus:
    c = Corpus(Path(CORPUS), tmp_path_factory.mktemp("idx"))
    print(f"\nindex: {len(c.docs)} docs, {len(c.secs)} sections in {c.build_s:.1f}s")
    return c


def test_granularity_question_returns_section_five(corpus: Corpus):
    r = corpus.search("passage-level versus document-level indexing unit for retrieval over long documents")
    assert "dense-x-retrieval" in r["top_document"]
    assert any("dense-x-retrieval" in h["path"] and h["section"].startswith("5 ") for h in r["hits"]), r["hits"]
    assert r["hits"][0]["coverage"] >= 0.9


def test_reranking_question_top_document(corpus: Corpus):
    r = corpus.search("does a cross-encoder reranker over BM25 first-stage candidates improve recall")
    assert "rerank-before-you-reason" in r["top_document"]
    assert len({h["path"] for h in r["hits"]}) >= 2, "per-document cap must leave room for a second paper"


def test_unanswerable_question_shows_low_coverage(corpus: Corpus):
    r = corpus.search("rust borrow checker lifetime errors")
    assert r["hits"], "hits are still returned; the signal is in coverage, not in an empty list"
    assert r["hits"][0]["coverage"] < 0.5, r["hits"][0]
    assert not any({"borrow", "checker"} <= set(h["matched_terms"]) for h in r["hits"])


def test_every_hit_is_locatable(corpus: Corpus):
    r = corpus.search("fixing vocabulary mismatch between query and document without embeddings")
    for h in r["hits"]:
        assert h["line"] >= 1 and h["window"] and h["path"] in corpus.texts
        assert h["window"].strip() in corpus.texts[h["path"]]
