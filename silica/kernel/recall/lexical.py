# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Lexical index — in-memory postings, BM25 over whole documents.

ponytail: a term -> {path: tf} postings map (rebuilt on load, never
persisted) gives O(1) df and O(union) candidates instead of an O(docs)
scan per query; swap for a real index only if the corpus outgrows memory.
Scores are raw BM25: comparable within one call, never a probability.
"""
from __future__ import annotations

import math
import threading
from pathlib import Path
from typing import Any

import orjson

from silica.kernel.recall.paths import DiskSynced

_BM25_K1 = 1.5
_BM25_B = 0.75


def _index_path() -> Path:
    from silica.kernel.recall import paths
    return paths.index_file("lexical")


def _tokens(text: str) -> list[str]:
    """Tokens for lexical matching — reuse the C1 text seam, surface (unstemmed)
    so proper nouns and dates match verbatim."""
    from silica.kernel.text.text import tokens
    from silica.config import CONFIG
    out: list[str] = []
    for sentence in tokens(text, lang=CONFIG.lang, stem=False):
        out.extend(surface for _stem, surface in sentence)
    return out


class LexicalStore(DiskSynced):
    def __init__(self, path: Path | None = None):
        self._path = path if path is not None else _index_path()
        self._docs: dict[str, dict[str, int]] = {}   # path -> {term: tf}
        self._len: dict[str, int] = {}               # path -> doc length
        self._name: dict[str, str] = {}              # path -> title/key for fuzzy
        self._postings: dict[str, dict[str, int]] = {}   # DERIVED: term -> {path: tf}
        self._name_lower: dict[str, str] = {}            # DERIVED: path -> name.lower()
        # DiskSynced bookkeeping; the lock guards only the sync/save skeleton,
        # this store's mutators were never thread-safe and that is unchanged.
        self._lock = threading.RLock()
        self._dirty: set[str] = set()
        self._gone: set[str] = set()

    def __len__(self) -> int:
        return len(self._docs)

    def _unindex(self, path: str) -> None:
        """Drop `path` from every posting list of its current terms."""
        for t in self._docs.get(path, {}):
            d = self._postings.get(t)
            if d:
                d.pop(path, None)
                if not d:
                    self._postings.pop(t, None)

    def _reindex(self) -> None:
        """Rebuild the derived postings/name_lower indexes from _docs/_name."""
        from silica.kernel.recall.paths import build_postings
        self._postings = build_postings(self._docs)
        self._name_lower = {path: name.lower() for path, name in self._name.items()}

    def upsert(self, path: str, name: str, body: str) -> None:
        if path in self._docs:
            self._unindex(path)
        toks = _tokens(f"{name}\n{body}")
        tf: dict[str, int] = {}
        for t in toks:
            tf[t] = tf.get(t, 0) + 1
        self._docs[path] = tf
        self._len[path] = len(toks)
        self._name[path] = name
        for term, f in tf.items():
            self._postings.setdefault(term, {})[path] = f
        self._name_lower[path] = name.lower()
        self._dirty.add(path)
        self._gone.discard(path)

    def remove(self, path: str) -> None:
        self._unindex(path)
        self._docs.pop(path, None)
        self._len.pop(path, None)
        self._name.pop(path, None)
        self._name_lower.pop(path, None)
        self._gone.add(path)
        self._dirty.discard(path)

    def paths(self) -> list[str]:
        return list(self._docs)

    def query_idf(self, terms: set[str]) -> dict[str, float]:
        """BM25 idf per KNOWN term, for the query-density window scan
        (rerank.best_window_spans). Terms with df=0 are omitted, not given
        the formula's maximum: the postings are C1-tokenized (stopwords
        dropped), the window scan is not, so df=0 cannot distinguish "rare
        in this corpus" from "a stopword the index never stores" — and the
        maximum handed 'what' the same weight as 'fundraiser' (probed on
        conv-26, 2026-08-25). Omitted terms keep the scan's neutral 1.0 via
        w.get, which orders known-rare > unknown > known-common. Empty
        store -> {}, the same abstention shape as rank()."""
        n = len(self._docs)
        if not n:
            return {}
        out: dict[str, float] = {}
        for t in terms:
            df = len(self._postings.get(t, {}))
            if df:
                out[t] = math.log(1 + (n - df + 0.5) / (df + 0.5))
        return out

    def idf(self, terms: set[str]) -> dict[str, float | None]:
        """BM25 idf per query term; None when the term occurs nowhere in the
        corpus. That None is the honest signal `terms_absent` is built from."""
        n = len(self._docs)
        out: dict[str, float | None] = {}
        for t in terms:
            df = len(self._postings.get(t, {}))
            out[t] = math.log(1 + (n - df + 0.5) / (df + 0.5)) if df else None
        return out

    def bm25(self, query: str) -> list[tuple[str, float, set[str]]]:
        """Every candidate document, best first: (path, raw score, matched terms).
        Empty store -> []."""
        n = len(self._docs)
        if not n:
            return []
        avgdl = sum(self._len.values()) / n
        score: dict[str, float] = {}
        matched: dict[str, set[str]] = {}
        for term, w in self.idf(set(_tokens(query))).items():
            if w is None:
                continue
            for path, f in self._postings.get(term, {}).items():
                dl = self._len[path] or 1
                score[path] = score.get(path, 0.0) + w * (f * (_BM25_K1 + 1)) / (
                    f + _BM25_K1 * (1 - _BM25_B + _BM25_B * dl / avgdl))
                matched.setdefault(path, set()).add(term)
        ranked = sorted(score.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(p, sc, matched[p]) for p, sc in ranked]

    def _read_disk(self) -> dict[str, Any]:
        try:
            if self._path.is_file():
                return orjson.loads(self._path.read_bytes())
        except Exception:
            # Derived index: quarantine for doctor visibility, then
            # reset to empty (a rebuild repopulates it).
            from silica.kernel.recall.paths import quarantine
            quarantine(self._path)
        return {}

    def _take_disk(self, data: dict[str, Any]) -> None:
        docs = {p: dict(tf) for p, tf in data.get("docs", {}).items()}
        lens = dict(data.get("len", {}))
        names = dict(data.get("name", {}))
        for p in self._gone:
            docs.pop(p, None)
            lens.pop(p, None)
            names.pop(p, None)
        for p in self._dirty:
            if p in self._docs:
                docs[p], lens[p], names[p] = self._docs[p], self._len[p], self._name[p]
        self._docs, self._len, self._name = docs, lens, names
        self._reindex()

    def _snapshot(self) -> tuple[dict, dict, dict]:
        return (dict(self._docs), dict(self._len), dict(self._name))

    def _serialize(self, snapshot) -> bytes:
        docs, lens, names = snapshot
        return orjson.dumps({"docs": docs, "len": lens, "name": names})

    def _dirty_sets(self) -> tuple[set[str], set[str]]:
        return (self._dirty, self._gone)

    @classmethod
    def load(cls, path: Path | None = None) -> "LexicalStore":
        store = cls(path)
        store._load()
        return store


_STORE_CACHE: dict[str, "LexicalStore"] = {}


def get_lexical_store() -> "LexicalStore":
    from silica.kernel.recall.paths import path_keyed_singleton
    store = path_keyed_singleton(_STORE_CACHE, str(_index_path()), LexicalStore.load)
    store.sync_from_disk()
    return store


def clear() -> None:
    """Drop all cached stores (test isolation; frees memory on /vault switch)."""
    _STORE_CACHE.clear()
