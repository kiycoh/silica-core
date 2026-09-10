#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""PROTOTYPE, throwaway: does a cross-encoder over the hybrid pool beat the
hybrid order on the 32 code questions? The test the internal comparison
asked for: hybrid20 -> mxbai-rerank-base-v2 -> top-10, paired with hybrid@10
and hybrid20.

Two processes, because the cross-encoder needs torch and this venv has none:

    .venv/bin/python scripts/bench_ce.py dump   CAND.json          # hybrid k=10 and k=20 hits with their texts
    <venv with sentence-transformers> scripts/bench_ce_score.py CAND.json SCORES.json
    .venv/bin/python scripts/bench_ce.py eval   CAND.json SCORES.json

Documents the cross-encoder reads, two variants: `unit` = path, symbol and
the unit's source (first 1500 chars); `window` = the 600-char window the
caller already receives. Arms per variant:

    hybrid10   the product at k=10
    hybrid20   the product at k=20 (its top-10 at @10)
    ce10       hybrid10 reordered by the cross-encoder: reorder-only, the
               internal's own rule (recall@10 invariant by construction)
    ce20       hybrid20 reordered by the cross-encoder, top-10: the proposed test
    rrf20      RRF of the hybrid20 order and the cross-encoder order, top-10
"""
from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))


def _load(name: str):
    spec = importlib.util.spec_from_file_location(name, REPO / "scripts" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bc = _load("bench_code")
bx = _load("bench_expand")
UNIT_CHARS = 1500


def _search(task: dict, k: int) -> tuple[list[dict], float]:
    from silica.config import CONFIG
    import silica.core as core
    CONFIG.vault_path = task["root"]
    CONFIG.index_code = True
    CONFIG.embedding_base_url = ""
    CONFIG.embedding_model = bc.POTION
    core._section_cache.clear()
    t0 = time.time()
    r = core.search(task["question"], k=k, per_doc=2)
    ms = 1000 * (time.time() - t0)
    hits = []
    for h in r.get("hits", []):
        rng = h.get("span") or [h["line"], h["line"]]
        hits.append({"file": h["path"], "range": rng, "anchor": h["line"], "section": h["section"],
                     "window": h.get("window", ""), "key": f"{h['path']}:{rng[0]}-{rng[1]}"})
    return hits, ms


def _unit_text(root: str, h: dict) -> str:
    try:
        lines = (Path(root) / h["file"]).read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return h["window"]
    a, b = h["range"]
    return "\n".join(lines[a - 1:b])[:UNIT_CHARS]


def dump(out: Path) -> None:
    tasks = bc.load_tasks("all")
    cands = []
    for t in tasks:
        h10, ms10 = _search(t, 10)
        h20, ms20 = _search(t, 20)
        pool: dict[str, dict] = {}
        for h in h10 + h20:
            if h["key"] not in pool:
                pool[h["key"]] = {**h, "unit": f"{h['file']} — {h['section']}\n{_unit_text(t['root'], h)}"}
        cands.append({"id": t["id"], "set": t["set"], "question": t["question"], "root": t["root"],
                      "hybrid10": [h["key"] for h in h10], "hybrid20": [h["key"] for h in h20],
                      "ms10": round(ms10), "ms20": round(ms20), "pool": list(pool.values())})
        print(f"{t['id']:14s} pool {len(pool):2d} (10: {len(h10)}, 20: {len(h20)})", flush=True)
    out.write_text(json.dumps(cands))
    print(f"-> {out}")


def evaluate(cand_path: Path, score_path: Path) -> None:
    cands = json.loads(cand_path.read_text())
    scores = json.loads(score_path.read_text())  # {id: {variant: {key: score}}, "_ms": {id: {variant: ms}}}
    tasks = {t["id"]: t for t in bc.load_tasks("all")}
    variants = list(scores[cands[0]["id"]]) if cands else []
    arms = ["hybrid10", "hybrid20"] + [f"{a}:{v}" for v in variants for a in ("ce10", "ce20", "rrf20")]
    rows: dict[str, list[dict]] = {a: [] for a in arms}
    for c in cands:
        t = tasks[c["id"]]
        by_key = {h["key"]: h for h in c["pool"]}
        h10 = [by_key[k] for k in c["hybrid10"]]
        h20 = [by_key[k] for k in c["hybrid20"]]
        lists = {"hybrid10": (h10, c["ms10"]), "hybrid20": (h20, c["ms20"])}
        for v in variants:
            sc = scores[c["id"]][v]
            ms = c["ms20"] + scores.get("_ms", {}).get(c["id"], {}).get(v, 0)
            ce = lambda hs: sorted(hs, key=lambda h: -sc.get(h["key"], -1e9))  # noqa: E731
            lists[f"ce10:{v}"] = (ce(h10), ms)
            lists[f"ce20:{v}"] = (ce(h20), ms)
            rk_h = {h["key"]: i for i, h in enumerate(h20, 1)}
            rk_c = {h["key"]: i for i, h in enumerate(ce(h20), 1)}
            lists[f"rrf20:{v}"] = (sorted(h20, key=lambda h: -(1 / (60 + rk_h[h["key"]]) + 1 / (60 + rk_c[h["key"]]))), ms)
        for arm, (hs, ms) in lists.items():
            rows[arm].append({"id": c["id"], "set": c["set"], "hits": hs, "chars": 800 * min(len(hs), 10),
                              "ms": ms, "at5": bc.score(t, hs, 5), "at10": bc.score(t, hs, 10), "at20": bc.score(t, hs, 20)})
    out = bc.BENCH / "results"
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for arm, rs in rows.items():
        (out / f"ce-{arm.replace(':', '-')}-{stamp}.json").write_text(
            json.dumps({"label": f"ce-{arm}", "arm": "silica", "mode": arm, "k": 10, "per_doc": 2, "rows": rs}, indent=1))
    print("@10 (chars = 800 per hit, ms = search plus cross-encoder)\n" + bx.table_at(rows, "at10"))
    print("\n@20 (hybrid20, ce20 and rrf20 hold the same 20 hits: only the order differs)\n"
          + bx.table_at({a: r for a, r in rows.items() if a != "hybrid10"}, "at20") + "\n")
    for v in variants:
        for arm in ("ce10", "ce20", "rrf20"):
            bx.paired(rows["hybrid10"], rows[f"{arm}:{v}"], "at10", "hybrid10", f"{arm}:{v}")
        bx.paired(rows["hybrid20"], rows[f"ce20:{v}"], "at10", "hybrid20 top-10", f"ce20:{v}")
        bx.paired(rows["hybrid20"], rows[f"ce20:{v}"], "at20", "hybrid20", f"ce20:{v}")
    ms = scores.get("_ms", {})
    if ms:
        import statistics
        for v in variants:
            print(f"cross-encoder ms per query, {v}: median {statistics.median(m[v] for m in ms.values()):.0f}, "
                  f"max {max(m[v] for m in ms.values()):.0f}")


def main() -> int:
    if len(sys.argv) >= 3 and sys.argv[1] == "dump":
        dump(Path(sys.argv[2]))
    elif len(sys.argv) >= 4 and sys.argv[1] == "eval":
        evaluate(Path(sys.argv[2]), Path(sys.argv[3]))
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
