#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Offline replay of the code questions: the raw question as the query,
the ranked hits scored against the evidence the reference answer rests on.
No model in the loop, so a run over the 32 questions costs seconds and can
be repeated per change; what it cannot say is what an agent does with the
hits (that is scripts/baseline.py's job).

    scripts/bench_code.py --arm silica --mode lexical --label lex
    scripts/bench_code.py --arm silica --mode hybrid --label potion       # SILICA_EMBEDDING_MODEL set by the script
    scripts/bench_code.py --arm zg --mode hybrid --bin ~/tools/node_modules/.bin/zg
    scripts/bench_code.py --compare bench/swe-qa/results/lex-*.json bench/swe-qa/results/zg-hybrid-*.json

Tasks: the 20 SWE-QA questions zvec-grep publishes (bench/swe-qa/tasks.json,
pinned checkouts under ~/Documents/dev/swe-qa, gold evidence annotated from
the judge's reference answers in bench/swe-qa/gold/) and the 12 local
questions of scripts/baseline.py with their evidence lines
(bench/swe-qa/local-tasks.json). Gold is a file and, where the answer
names one, a symbol: the target span is the innermost symbol around the
recorded line (codeunits.outline), else 20 lines either side.

Per query and cutoff k: `file_hit` (a gold file among the top-k files),
`file_recall`, `file_rr` (reciprocal rank of the first gold file),
`sym_hit` and `sym_recall` (a hit locates a target when its anchor line is
inside the span, or its range overlaps the span and is not more than three
times longer: a whole-class hit does not get credit for a method inside).
`chars` is what the caller receives for the k hits (the hits' JSON, or
zg's agent markdown), `ms` the query time (zg in direct mode loads its
model per query, so its ms measure a process, not a ranking).
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import random
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
BENCH = REPO / "bench" / "swe-qa"
POTION = "model2vec/minishlab/potion-retrieval-32M"
HIT = re.compile(r"^#\d+ matchedBy=(\S+) (.+?):(\d+)-(\d+)$")
ANCHOR = re.compile(r"^(\d+)\t")


def load_tasks(which: str) -> list[dict]:
    out = []
    if which in ("zvec", "all"):
        for t in json.loads((BENCH / "tasks.json").read_text()):
            g = json.loads((BENCH / "gold" / (t["task_id"].replace(":", "-") + ".json")).read_text())
            gold = [{"file": e["file"], "lines": sorted(set(e.get("lines", {}).values()))} for e in g["gold"]]
            out.append({"id": t["task_id"], "set": "zvec", "root": t["root"], "question": t["question"], "gold": gold})
    if which in ("local", "all"):
        for t in json.loads((BENCH / "local-tasks.json").read_text()):
            out.append({"id": t["task_id"], "set": "local", "root": t["root"], "question": t["question"], "gold": t["gold"]})
    return out


_outline_cache: dict[tuple[str, str], tuple[list[dict], int]] = {}


def spans(root: str, rel: str, lines: list[int]) -> list[tuple[int, int]]:
    """The symbol around each gold line (the innermost; the one opening within
    five lines below when the line is a comment above it), else 20 lines
    either side."""
    from silica.kernel.code.codeunits import outline
    key = (root, rel)
    if key not in _outline_cache:
        text = (Path(root) / rel).read_text(encoding="utf-8", errors="replace")
        _outline_cache[key] = (outline(rel, text), text.count("\n") + 1)
    ol, n = _outline_cache[key]
    out = []
    for ln in lines:
        inner = [h for h in ol if h["line"] <= ln <= h["end_line"]]
        h = min(inner, key=lambda h: h["end_line"] - h["line"]) if inner else \
            next((h for h in ol if ln < h["line"] <= ln + 5), None)
        out.append((h["line"], h["end_line"]) if h else (max(1, ln - 20), min(n, ln + 20)))
    return out


def score(task: dict, hits: list[dict], k: int) -> dict:
    files: list[str] = []
    for h in hits[:k]:
        if h["file"] not in files:
            files.append(h["file"])
    gold_files = [g["file"] for g in task["gold"]]
    found = [f for f in gold_files if f in files]
    rr = next((1 / (i + 1) for i, f in enumerate(files) if f in gold_files), 0.0)
    targets = [(g["file"], s) for g in task["gold"] for s in spans(task["root"], g["file"], g.get("lines", []))]

    def locates(h: dict, file: str, span: tuple[int, int]) -> bool:
        if h["file"] != file:
            return False
        a, b = h["range"]
        s, e = span
        if s <= h["anchor"] <= e:
            return True
        return not (b < s or a > e) and (b - a + 1) <= 3 * (e - s + 1) + 10

    located = [t for t in targets if any(locates(h, *t) for h in hits[:k])]
    return {"file_hit": int(bool(found)), "file_recall": len(found) / len(gold_files), "file_rr": rr,
            "sym_hit": int(bool(located)) if targets else None,
            "sym_recall": len(located) / len(targets) if targets else None}


def silica_arm(task: dict, mode: str, k: int, per_doc: int) -> tuple[list[dict], int, float, dict]:
    from silica.config import CONFIG
    import silica.core as core
    CONFIG.vault_path = task["root"]
    CONFIG.index_code = True
    CONFIG.embedding_base_url = ""
    CONFIG.embedding_model = POTION if mode in ("hybrid", "dense") else "off"
    core._section_cache.clear()
    t0 = time.time()
    if mode == "dense":  # the vectors alone, a diagnostic: what the fusion adds or costs
        from silica import embeddings
        live = core._notes()
        _docs, secs = embeddings.rank(task["question"], live)
        ms = 1000 * (time.time() - t0)
        hits, seen = [], {}
        for (rel, off), c in sorted(secs.items(), key=lambda kv: (-kv[1], kv[0])):
            if seen.get(rel, 0) >= per_doc:
                continue
            seen[rel] = seen.get(rel, 0) + 1
            text, parts = core._sections_of(f"{rel}#{off}" if core._is_code(rel) else rel)
            part = next((pt for o, pt, *_r in parts if o == off), "")
            first = text.count("\n", 0, off) + 1
            hits.append({"file": rel, "range": [first, first + part.rstrip("\n").count("\n")], "anchor": first,
                         "section": next((t for o, _pt, _tf, _dl, t in parts if o == off), "")})
            if len(hits) == k:
                break
        return hits, sum(len(h.get("section", "")) + 600 for h in hits), ms, {"dense": "alone"}
    r = core.search(task["question"], k=k, per_doc=per_doc)
    ms = 1000 * (time.time() - t0)
    if "error" in r:
        return [], 0, ms, {"error": r["error"]}
    hits = [{"file": h["path"], "range": h.get("span") or [h["line"], h["line"]], "anchor": h["line"],
             "section": h["section"]} for h in r["hits"]]
    return hits, len(json.dumps(r["hits"])), ms, {"index": r["index"]["state"], "dense": r["dense"].get("state"),
                                                 "absent": r["terms_absent"]}


def zg_arm(task: dict, mode: str, k: int, binary: str, preview: str = "none") -> tuple[list[dict], int, float, dict]:
    q = task["question"]
    args = [binary, "query", "--mode", "direct", "--limit", str(k), "--preview", preview]
    args += ["--fts", q] if mode == "fts" else ["--vector", q] if mode == "vector" else [q]
    t0 = time.time()
    p = subprocess.run(args, cwd=task["root"], capture_output=True, text=True, env={**os.environ, "NO_COLOR": "1"})
    ms = 1000 * (time.time() - t0)
    hits: list[dict] = []
    cur: dict | None = None
    for line in p.stdout.splitlines():
        m = HIT.match(line)
        if m:
            cur = {"file": m.group(2), "range": [int(m.group(3)), int(m.group(4))], "anchor": int(m.group(3)),
                   "by": m.group(1)}
            hits.append(cur)
            continue
        a = ANCHOR.match(line)
        if a and cur is not None and "anchored" not in cur:
            cur["anchor"], cur["anchored"] = int(a.group(1)), True
    info = {"rc": p.returncode}
    if p.returncode or not hits:
        info["stderr"] = (p.stderr or p.stdout)[-300:]
    return hits, len(p.stdout), ms, info


def mean(rows, key, k):
    vals = [r[f"at{k}"][key] for r in rows if r[f"at{k}"].get(key) is not None]
    return statistics.mean(vals) if vals else float("nan")


def table(rows: list[dict], label: str) -> str:
    lines = ["| set | n | file_hit@5 | file_hit@10 | file_rr@10 | file_recall@10 | sym_hit@5 | sym_hit@10 | sym_recall@10 | chars@10 | ms |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for s in ("zvec", "local", "all"):
        rs = [r for r in rows if s == "all" or r["set"] == s]
        if not rs:
            continue
        lines.append(f"| {s} {label} | {len(rs)} | {mean(rs, 'file_hit', 5):.2f} | {mean(rs, 'file_hit', 10):.2f} | "
                     f"{mean(rs, 'file_rr', 10):.2f} | {mean(rs, 'file_recall', 10):.2f} | {mean(rs, 'sym_hit', 5):.2f} | "
                     f"{mean(rs, 'sym_hit', 10):.2f} | {mean(rs, 'sym_recall', 10):.2f} | "
                     f"{statistics.mean(r['chars'] for r in rs):,.0f} | {statistics.mean(r['ms'] for r in rs):,.0f} |")
    return "\n".join(lines)


def compare(a_glob: str, b_glob: str, boots: int = 4000) -> None:
    a = json.loads(Path(sorted(glob.glob(a_glob))[-1]).read_text())
    b = json.loads(Path(sorted(glob.glob(b_glob))[-1]).read_text())
    ra = {r["id"]: r for r in a["rows"]}
    rb = {r["id"]: r for r in b["rows"]}
    ids = sorted(set(ra) & set(rb))
    print(f"{a['label']} vs {b['label']}, n={len(ids)} paired")
    rng = random.Random(0)
    for key in ("file_hit", "file_rr", "file_recall", "sym_hit", "sym_recall"):
        d = [(rb[i]["at10"][key] or 0) - (ra[i]["at10"][key] or 0) for i in ids
             if ra[i]["at10"].get(key) is not None and rb[i]["at10"].get(key) is not None]
        if not d:
            continue
        means = sorted(statistics.mean(rng.choices(d, k=len(d))) for _ in range(boots))
        lo, hi = means[int(0.025 * boots)], means[int(0.975 * boots)]
        wins = sum(1 for x in d if x > 0)
        losses = sum(1 for x in d if x < 0)
        verdict = "noise" if lo <= 0 <= hi else ("B better" if lo > 0 else "A better")
        print(f"  {key:12s}@10 diff {statistics.mean(d):+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]  "
              f"wins {wins} losses {losses} ties {len(d) - wins - losses} -> {verdict}")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--arm", choices=("silica", "zg"), default="silica")
    ap.add_argument("--mode", default="", help="silica: lexical|hybrid|dense (default lexical); zg: hybrid|fts|vector (default hybrid)")
    ap.add_argument("--bin", default=str(Path.home() / "tools/node_modules/.bin/zg"))
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--preview", default="none", help="zg: none|short|full; `chars` counts what the caller receives")
    ap.add_argument("--per-doc", type=int, default=2, help="silica: hits per file")
    ap.add_argument("--tasks", choices=("zvec", "local", "all"), default="all")
    ap.add_argument("--only", default="", help="comma-separated task ids")
    ap.add_argument("--label", default="")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    a = ap.parse_args()
    if a.compare:
        compare(*a.compare)
        return 0
    mode = a.mode or ("lexical" if a.arm == "silica" else "hybrid")
    label = a.label or f"{a.arm}-{mode}" + (f"-pd{a.per_doc}" if a.arm == "silica" and a.per_doc != 2 else "") + (
        f"-{a.preview}" if a.arm == "zg" and a.preview != "none" else "") + (f"-k{a.k}" if a.k != 10 else "")
    tasks = load_tasks(a.tasks)
    if a.only:
        tasks = [t for t in tasks if t["id"] in a.only.split(",")]
    rows = []
    for t in tasks:
        if a.arm == "silica":
            hits, chars, ms, info = silica_arm(t, mode, a.k, a.per_doc)
        else:
            hits, chars, ms, info = zg_arm(t, mode, a.k, a.bin, a.preview)
        row = {"id": t["id"], "set": t["set"], "hits": hits[:a.k], "chars": chars, "ms": round(ms),
               "info": info, "at5": score(t, hits, 5), "at10": score(t, hits, 10)}
        rows.append(row)
        print(f"{t['id']:14s} file_rr {row['at10']['file_rr']:.2f} sym_hit {row['at10']['sym_hit']} "
              f"{row['at10']['sym_recall'] if row['at10']['sym_recall'] is None else round(row['at10']['sym_recall'], 2)} "
              f"{ms:,.0f} ms {info if 'error' in info or info.get('rc') else ''}", flush=True)
    out = BENCH / "results"
    out.mkdir(exist_ok=True)
    path = out / f"{label}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps({"label": label, "arm": a.arm, "mode": mode, "k": a.k, "per_doc": a.per_doc,
                                "rows": rows}, indent=1))
    print(table(rows, label))
    print(f"-> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
