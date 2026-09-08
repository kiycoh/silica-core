# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Cross-encoder rerank pass over a fused candidate pool.

The relatedness facade fuses embeddings + co-occurrence by RANK (RRF); neither leg
ever reads the query and a candidate *together*. A cross-encoder does exactly that,
scoring query x document jointly — the strongest precision lever after first-stage
recall. This module applies that pass to an already-retrieved pool: it reorders,
never retrieves, and abstains (leaves the pool's order untouched) whenever the
reranker is absent or errors, mirroring a down leg in the facade.

The reranker CLIENT lives in agent/providers.py (Reranker/get_reranker); this module
holds only the note-aware reorder, so the client stays a plain HTTP provider.
"""
import re
from typing import Callable

_WINDOW_CHARS = 800  # cross-encoder document budget (chars): excerpt window + gate unit
# Frozen by the phase-0 run (2026-07-17, bench/phase0_gates.json): the spec's
# clean-gap expectation was REFUTED — real-vault notes are long (ratio p50 3.6,
# p90 10.8, max 20.8) and overlap LME chat sessions (9.2–21.7), so no corpus-level
# all-or-nothing threshold exists. 8 sits just under the measured-damage floor
# (LME min 9.2): every query where rerank damage was measured still fires, with
# margin, while ~81% of vault queries keep the reranker; the ~19% vault tail that
# fires holds >6.4k-char bodies, unreadable for the same reason chat sessions are.
_RERANK_WINDOW_FACTOR = 8
# Calibration hook: harnesses set it to capture {"median_len", "min_len",
# "window", "fired"} per query; production leaves it None. The factor above was
# frozen against the MEDIAN; the gate now votes on `min_len` (see rerank_related),
# which keeps every all-long pool firing — the LME chat sessions the factor was
# calibrated on are uniformly 9.2k+ chars, so their min fires too — while a pool
# holding even one window-sized note keeps its cross-encoder scores.
RERANK_GATE_PROBE: Callable[[dict], None] | None = None


def _query_terms(query: str) -> set[str]:
    return {t for t in re.findall(r"\w+", query.lower()) if len(t) > 3}


def window_weights(query: str) -> dict[str, float]:
    """Per-term BM25 idf from the vault's lexical index, for weighting the
    window-density scan. The unweighted scan counts 'what' and 'gradient'
    the same, so on long notes the window can centre on function-word-dense
    prose instead of the discriminative passage (offline-signals-map §3,
    graft G3). Reading the index is the only coupling to the lexical lane:
    no leg is fused (ADR-0019).
    {} — the unweighted scan — when the vault has no lexical index, it is
    empty, or the store is unreadable: the lever must never make windowing
    worse than it was without an index."""
    terms = _query_terms(query)
    if not terms:
        return {}
    try:
        from silica.kernel.recall.lexical import get_lexical_store

        return get_lexical_store().query_idf(terms)
    except Exception:
        return {}  # tolerated: no index dir / driver down -> unweighted scan


def best_window_spans(text: str, query: str, width: int, n: int = 1,
                      weights: dict[str, float] | None = None,
                      *, snap: bool = False) -> list[tuple[int, str]]:
    """Up to `n` non-overlapping (offset, slice) windows of `text` densest in
    query terms, in document order (multi-window spec 2026-07-15; offsets
    added for the section chain, graft G1).

    A cross-encoder sees ~512 tokens (~2k chars); on a long note the naive
    head slice `text[:width]` can miss the passage the query is actually about
    entirely, so the reranker scores irrelevant opening text and demotes a true
    match (measured: on LongMemEval's multi-turn chat sessions the head slice
    evicts gold sessions whose relevant turn sits past char 800). Anchoring
    windows on query-term density fixes that with no extra model call; on
    9-21k-char chat bodies a single window still cuts gold spans (gic 0.533 on
    the raw arm), so perception can ask for several.

    `weights` (term -> idf, from `window_weights`) recentres density on
    discriminative terms; None/{} keeps the historical unweighted count
    bit-identical (w.get default 1.0, and float==int ties break the same).

    Greedy top-N with masking: hits per position never change (masking removes
    candidate positions, not text), so one density scan feeds every pick. The
    first window is always taken even at zero hits (n=1 stays bit-identical to
    the historical single-window behavior); each later window needs hits > 0 —
    never pad with irrelevant text, returning fewer than n windows is normal.
    Document order preserves chat chronology for temporal questions.

    `snap` pulls each offset back to the start of its line (at most one
    stride, so density is unchanged): the rendered excerpt then opens on a
    word instead of "matic gate does not", and the section chain reads a real
    line offset. Off by default because the reranker's scored docs are
    benchmarked bit-identical; only the render asks for it.
    """
    if len(text) <= n * width:
        return [(0, text)]
    terms = _query_terms(query)
    if not terms:
        return [(0, text[:width])]
    w = weights or {}
    low = text.lower()
    step = max(1, width // 4)
    candidates = [(pos, sum(low.count(t, pos, pos + width) * w.get(t, 1.0)
                            for t in terms))
                  for pos in range(0, max(1, len(text) - width) + step, step)]
    chosen: list[int] = []
    while candidates and len(chosen) < n:
        pos, hits = max(candidates, key=lambda c: c[1])  # earliest max, as before
        if chosen and hits == 0:
            break
        chosen.append(pos)
        candidates = [c for c in candidates
                      if c[0] + width <= pos or c[0] >= pos + width]
    if snap:
        # The end stays put: a hit in the window's last `step` chars would
        # otherwise fall off when the start moves back. A body with no newline
        # in reach (one-line chat turns) keeps its offset instead of jumping
        # to 0, which is what lost the yoga class in the tests.
        out = []
        for p in sorted(chosen):
            nl = text.rfind("\n", max(0, p - step), p)
            start = nl + 1 if nl >= 0 else p
            out.append((start, text[start:p + width]))
        return out
    return [(p, text[p:p + width]) for p in sorted(chosen)]


def best_windows(text: str, query: str, width: int, n: int = 1,
                 weights: dict[str, float] | None = None) -> list[str]:
    """The slices of `best_window_spans`, for callers that never need the
    offsets (the rationale lives there)."""
    return [s for _p, s in best_window_spans(text, query, width, n, weights)]


def best_window(text: str, query: str, width: int,
                weights: dict[str, float] | None = None) -> str:
    """The single `width`-char slice of `text` densest in query terms
    (see `best_window_spans`; this is its n=1 case, bit-identical)."""
    return best_windows(text, query, width, 1, weights)[0]

