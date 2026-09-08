# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Lexical index — in-memory postings, BM25 over whole documents.

ponytail: a term -> {path: tf} postings map (rebuilt on load, never
persisted) gives O(1) df and O(union) candidates instead of an O(docs)
scan per query; swap for a real index only if the corpus outgrows memory.
Scores are raw BM25: comparable within one call, never a probability.
"""
from __future__ import annotations

import math
import re
import threading
import unicodedata
from pathlib import Path
from typing import Any

import orjson

from silica.kernel.recall.paths import DiskSynced

_BM25_K1 = 1.5
_BM25_B = 0.75


def _index_path() -> Path:
    from silica.kernel.recall import paths
    return paths.index_file("lexical")


# Function words only, the size of NLTK's lists (179 English, ~150 Italian):
# articles, pronouns, prepositions, conjunctions, auxiliaries, a few adverbs.
# The stop-words package lists 1333 English words and dropped "world",
# "system", "number" and "first" from the index; Lucene's 33 keep "does",
# "over" and "versus", which BM25 then matches in every paper. Neither is
# the right size for a corpus index.
#
# One list serves both languages, so it may hold only what is a function word
# in BOTH. `state` and `era` are Italian verb forms and English nouns; keeping
# them cost 2487 occurrences of "state" across 214 of the 254 bench papers —
# a core term of this literature, deleted from the index to save a participle.
# They stay out. `come` stays in: a function word in Italian and a near-empty
# verb in English, so dropping it loses nothing either way.
STOPWORDS = frozenset("""
i me my myself we our ours ourselves you your yours yourself yourselves he him
his himself she her hers herself it its itself they them their theirs themselves
what which who whom this that these those am is are was were be been being have
has had having do does did doing a an the and but if or because as until while
of at by for with about against between into through during before after above
below to from up down in out on off over under again further then once here
there when where why how all any both each few more most other some such no nor
not only own same so than too very s t can will just don should now d ll m o re
ve y ain aren couldn didn doesn hadn hasn haven isn ma mightn mustn needn shan
shouldn wasn weren won wouldn versus via etc
io me mi mio mia miei mie tu te ti tuo tua tuoi tue lui lei sé si suo sua suoi
sue noi ci nostro nostra nostri nostre voi vi vostro vostra vostri vostre loro
essi esse esso essa questo questa questi queste quello quella quelli quelle ciò
che chi cui il lo la i gli le un uno una di a da in con su per tra fra del
dello della dei degli delle al allo alla ai agli alle dal dallo dalla dai dagli
dalle nel nello nella nei negli nelle sul sullo sulla sui sugli sulle e ed o ma
se anche non né come dove quando perché mentre oppure sia ancora già più meno
molto poco tutto tutti tutta tutte ogni qualche alcuni alcune altro altra altri
altre stesso stessa stessi stesse sono sei è siamo siete eri eravamo erano
essere stato stata stati ho hai ha abbiamo avete hanno aveva avevano avere
avuto fa fanno fare fatto può possono qui qua lì là così poi
""".split())
def _fold(word: str) -> str:
    """Drop combining marks: `références` and `references` become one term.

    Applied symmetrically at index and query time, so folding never creates a
    term one side can produce and the other cannot.
    """
    return "".join(c for c in unicodedata.normalize("NFD", word)
                   if not unicodedata.combining(c))


# STOPWORDS holds its Italian function words accented (`perché`, `più`, `né`).
# Tokens arrive folded, so the membership test must run against the folded
# set or every Italian note indexes its own function words.
STOPWORDS_FOLDED = frozenset(_fold(w) for w in STOPWORDS)

TOKENIZER_VERSION = 7  # bumped when the token stream changes; the index rebuilds


_WORD = re.compile(r"[^\W_]+")
# PDFium marks a hyphen it removed at a line break with U+FFFE, so a PDF's text
# layer reads `us\ufffeing` (a syllable break) as often as `open\ufffedomain`
# (a real compound): measured 25 to 198 per paper under docs/research. The
# marker is `\W`, so `_WORD` already yields the two halves; the joined form is
# emitted alongside them, never instead, and both readings stay searchable.
_SOFT_BREAK = re.compile(r"([^\W_]+)\ufffe([^\W_]+)")
# Structured shapes `_WORD` would shred into fragments the whole corpus
# shares: a date becomes three common numbers, a path four common words.
# Matched on the original text and emitted lowercased ALONGSIDE those
# fragments, never instead of them — the atom is rare and carries the idf,
# the fragments keep the recall a bare year or filename still needs.
# A two-segment slash pair only counts as a path when it carries an
# extension, and every segment needs two characters, so the prose `and/or`,
# `w/o` and `b/c` stay prose.
#
# Both shapes are narrow on purpose, measured on the 254 bench papers. A
# version needs a leading `v` or three components: `\d+\.\d+` alone made
# every section number (3.1, 4.2) and every decimal in a results table
# (0.025) a term, 84% of all atoms, near-zero idf, and the inflated document
# lengths moved the right paper off the top of two acceptance questions.
# Path segments cap at 32 characters: converted papers carry MinerU asset
# names like `images/<64 hex>.jpg`, unique per document and therefore
# maximally rare — the worst kind of noise a lexical index can hold.
_ATOM = re.compile(r"""
    (?<![A-Za-z0-9._/-])(?:                                             # never mid-token
      \d{4}-\d{2}-\d{2}(?:[Tt ]\d{2}:\d{2}(?::\d{2})?)?                 # 2026-09-08, 2026-09-08T14:30
    | v\d+(?:\.\d+)+(?:-[A-Za-z0-9.]+)?                                 # v0.4.0-rc1, v1.2
    | \d+(?:\.\d+)?[A-Za-z]{1,4}(?![A-Za-z0-9])                         # 100ms, 5GB, 0.3s
    | [A-Za-z0-9_-]{2,32}(?:/[A-Za-z0-9._-]{1,32}){2,}                  # docs/research/papers
    | [A-Za-z0-9_-]{2,32}/[A-Za-z0-9_-]{1,32}\.[A-Za-z]{1,5}(?![A-Za-z])  # docs/file.md
    )
""", re.X)
# No camelCase segmentation here, and the omission is measured, not an
# oversight. Splitting `OrderStateMachine` into order/state/machine lets a
# prose query reach a name, which is the right trade in a code vault. On the
# 254-paper bench corpus it is the wrong one: an abstract and an
# introduction name every concept in the paper, so segments inflate the
# front matter above the section that answers. With `per_doc` at 2, Abstract
# and Introduction filled the quota and the granularity question lost the
# section titled "How Does Granularity Influence Performance" — a hit at
# coverage 0.97 that says less than the one at 0.65 it displaced. Bring it
# back per-vault, if at all.


def _tokens(text: str) -> list[str]:
    """Lowercased, accent-folded words and alphanumerics ("bm25", "gpt4",
    "2026" stay whole), at least two characters, function words removed. No
    stemming: proper nouns and dates match verbatim; no language detection:
    nothing here depends on it.

    Structured shapes (`_ATOM`) are emitted whole in addition to the
    fragments `_WORD` finds inside them, so `2026-09-08` is both one rare
    term and the year it contains.
    """
    out = [m.group(0).lower() for m in _ATOM.finditer(text)]
    out.extend(_fold((m.group(1) + m.group(2)).lower()) for m in _SOFT_BREAK.finditer(text))
    for m in _WORD.finditer(text):
        word = _fold(m.group(0).lower())
        if len(word) >= 2 and word not in STOPWORDS_FOLDED:
            out.append(word)
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
