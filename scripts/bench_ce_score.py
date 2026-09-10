#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""PROTOTYPE, throwaway: score the candidates of scripts/bench_ce.py with a
cross-encoder. Runs in any venv with sentence-transformers and torch (this
repository's has neither; silica-internal's does):

    <venv>/bin/python scripts/bench_ce_score.py CAND.json SCORES.json [model]

Same call as silica-internal's local reranker: `CrossEncoder(model).predict`.
Writes {task_id: {variant: {candidate key: score}}, "_ms": {task_id: {variant: ms}}}.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

MODEL = "mixedbread-ai/mxbai-rerank-base-v2"
VARIANTS = ("unit", "window")


def main() -> int:
    cand_path, out_path = Path(sys.argv[1]), Path(sys.argv[2])
    model = sys.argv[3] if len(sys.argv) > 3 else MODEL
    from sentence_transformers import CrossEncoder
    t0 = time.time()
    ce = CrossEncoder(model)
    print(f"{model} loaded in {time.time() - t0:.1f}s on {ce.model.device}", flush=True)
    cands = json.loads(cand_path.read_text())
    out: dict = {"_ms": {}}
    for c in cands:
        out[c["id"]], out["_ms"][c["id"]] = {}, {}
        for v in VARIANTS:
            pairs = [(c["question"], h[v] or h["unit"]) for h in c["pool"]]
            t0 = time.time()
            scores = ce.predict(pairs, batch_size=16)
            ms = 1000 * (time.time() - t0)
            out[c["id"]][v] = {h["key"]: float(s) for h, s in zip(c["pool"], scores)}
            out["_ms"][c["id"]][v] = round(ms)
        print(f"{c['id']:14s} {len(c['pool']):2d} candidates  unit {out['_ms'][c['id']]['unit']} ms  "
              f"window {out['_ms'][c['id']]['window']} ms", flush=True)
    out_path.write_text(json.dumps(out))
    print(f"-> {out_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
