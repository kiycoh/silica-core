#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Retrieval bench with a score: nDCG@10, MRR@10, Recall@10 over BEIR's
SciFact and NFCorpus, two corpora of paper abstracts with document-level
relevance judgements, which is the stage where vocabulary mismatch bites
`silica_search`. Runs without a model in seconds; a number comparable with
the BM25 baselines in the BEIR paper (nDCG@10 0.665 SciFact, 0.325 NFCorpus)
and with whatever embedder the hybrid arm is pointed at.

    scripts/bench_beir.py                          # lexical arm, both datasets
    scripts/bench_beir.py --arm hybrid             # needs SILICA_EMBEDDING_BASE_URL
    scripts/bench_beir.py --arm dense              # the vectors alone, no BM25, no fusion: the ablation
    scripts/bench_beir.py --arm zg --bin ~/tools/node_modules/.bin/zg
    scripts/bench_beir.py --arm ck --bin ~/tools/node_modules/.bin/ck
    scripts/bench_beir.py --dataset scifact --pool-factor 10

Each corpus is written once as one `.md` per document (`# title` then the
abstract) under `bench/beir/<dataset>/md`, so every arm indexes the same
folder of files. The Silica arms call `core.search` and read `documents`,
the ranking the agent sees; `pool_recall` is the share of relevant documents
inside the candidate pool the section stage scores, which sizes that pool.
Results land in `bench/beir/results/` as JSON, one file per run, and
`--compare A B` reads two of them back for the paired difference: a gap of
0.01 between two arms is noise, and only the interval says which is which.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BENCH = ROOT / "bench" / "beir"
BEIR_URL = "https://public.ukp.informatik.tu-darmstadt.de/thakur/BEIR/datasets/{name}.zip"
DATASETS = ("scifact", "nfcorpus")


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def fetch(name: str) -> Path:
    d = BENCH / name
    if (d / "corpus.jsonl").is_file():
        return d
    import httpx
    BENCH.mkdir(parents=True, exist_ok=True)
    print(f"downloading {name} …", file=sys.stderr)
    with httpx.Client(follow_redirects=True, timeout=120.0) as c:
        data = c.get(BEIR_URL.format(name=name)).raise_for_status().content
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        z.extractall(BENCH)
    return d


def write_md(d: Path) -> Path:
    """One `.md` per document; title as the heading, the abstract as the body."""
    md = d / "md"
    docs = [json.loads(l) for l in (d / "corpus.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    if md.is_dir() and sum(1 for _ in md.glob("*.md")) == len(docs):
        return md
    shutil.rmtree(md, ignore_errors=True)
    md.mkdir()
    for doc in docs:
        title = (doc.get("title") or "").strip() or doc["_id"]
        (md / f"{doc['_id']}.md").write_text(f"# {title}\n\n{doc.get('text', '').strip()}\n", encoding="utf-8")
    return md


def load_queries(d: Path, split: str = "test") -> list[tuple[str, str, dict[str, int]]]:
    """(qid, text, {docid: rel}) for every query with a judgement in `split`."""
    qrels: dict[str, dict[str, int]] = {}
    for line in (d / "qrels" / f"{split}.tsv").read_text(encoding="utf-8").splitlines()[1:]:
        qid, did, rel = line.split("\t")
        if int(rel) > 0:
            qrels.setdefault(qid, {})[did] = int(rel)
    out = []
    for line in (d / "queries.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        q = json.loads(line)
        if q["_id"] in qrels:
            out.append((q["_id"], q["text"], qrels[q["_id"]]))
    return out


# ---------------------------------------------------------------------------
# metrics (linear gain, like pytrec_eval's ndcg_cut, which BEIR reports)
# ---------------------------------------------------------------------------

def ndcg(ranked: list[str], rel: dict[str, int], k: int) -> float:
    dcg = sum(rel.get(d, 0) / math.log2(i + 2) for i, d in enumerate(ranked[:k]))
    ideal = sum(g / math.log2(i + 2) for i, g in enumerate(sorted(rel.values(), reverse=True)[:k]))
    return dcg / ideal if ideal else 0.0


def mrr(ranked: list[str], rel: dict[str, int], k: int) -> float:
    for i, d in enumerate(ranked[:k]):
        if d in rel:
            return 1.0 / (i + 1)
    return 0.0


def recall(ranked: list[str], rel: dict[str, int], k: int) -> float:
    return sum(1 for d in ranked[:k] if d in rel) / len(rel)


# ---------------------------------------------------------------------------
# arms
# ---------------------------------------------------------------------------

def silica_arm(md: Path, idx: Path, queries, *, hybrid: bool, pool: int, k: int, dense_only: bool = False):
    """`core.search` over the folder; the ranking is `documents`, the pool is
    what the section stage scored. `pool` overrides the pool multiplier for
    the experiment on its width. `dense_only` ranks by the vectors alone
    (`embeddings.rank`, best section per document), the ablation that says
    what the fusion adds to the embedder."""
    from silica.config import CONFIG
    from silica.kernel.recall import lexical, paths
    from silica import embeddings
    import silica.core as core

    CONFIG.vault_path = str(md)
    if not hybrid:  # the dense leg runs by itself once an embedder is named: the lexical arm must not see one
        CONFIG.embedding_base_url = CONFIG.embedding_model = ""
    paths.index_dir_for = lambda vault, _d=idx: _d  # type: ignore[assignment]
    lexical._STORE_CACHE.clear()
    core._section_cache.clear()
    if pool:
        core.POOL_FACTOR = pool
    t0 = time.time()
    built = core.build_index(embed=hybrid)
    if "error" in built:
        sys.exit(f"index: {built['error']}")
    t_index = time.time() - t0
    store = lexical.get_store(core.INDEX)
    per_query, t_q = [], 0.0
    live = core._notes()
    for qid, text, rel in queries:
        t1 = time.time()
        if dense_only:
            ranked = [Path(p).stem for p, _c in embeddings.rank(text, live)[0][:k]]  # (documents, sections): the documents
            t_q += time.time() - t1
            per_query.append({"qid": qid, "ranked": ranked, "rel": rel})
            continue
        r = core.search(text, k=k)
        t_q += time.time() - t1
        if "error" in r:
            sys.exit(f"search: {r['error']}")
        ranked = [Path(d["path"]).stem for d in r["documents"]][:k]
        pool_ids = [Path(p).stem for p, _s, _m in store.bm25(text)[: core._pool(k)]]
        per_query.append({"qid": qid, "ranked": ranked, "rel": rel, "pool": pool_ids,
                          "coverage": r["hits"][0]["coverage"] if r["hits"] else 0.0})
    return per_query, {"index_s": round(t_index, 2), "query_ms": round(1000 * t_q / max(len(queries), 1), 1),
                       "docs": built["docs"], "changed": built["changed"]}


def zg_arm(md: Path, queries, *, binary: str, mode: str, k: int, embedding: str, transport: str = "direct"):
    """zvec-grep over the same folder: `zg index` once, then the agent-markdown
    output of one query at a time, one `#n matchedBy=… path:lines` per hit."""
    env = {**os.environ, "NO_COLOR": "1"}
    t0 = time.time()
    idx = subprocess.run([binary, "index", str(md), "--embedding", embedding, "--mode", transport],
                         capture_output=True, text=True, env=env, cwd=md)
    if idx.returncode:
        sys.exit(f"zg index: {idx.stderr[-2000:]}")
    t_index = time.time() - t0
    per_query, t_q = [], 0.0
    hit_line = re.compile(r"^#\d+ matchedBy=\S+ (\S+?\.md):\d+")  # `#1 matchedBy=fts+vector 27049238.md:1-4`
    for qid, text, rel in queries:
        args = [binary, "query", "--mode", transport, "--limit", str(k), "--preview", "none"]
        if mode == "fts":
            args += ["--fts", text]
        elif mode == "vector":
            args += ["--vector", text]
        else:
            args += [text]
        t1 = time.time()
        p = subprocess.run(args, capture_output=True, text=True, env=env, cwd=md)
        t_q += time.time() - t1
        ranked = []
        for line in p.stdout.splitlines():
            m = hit_line.match(line)
            if m and Path(m.group(1)).stem not in ranked:
                ranked.append(Path(m.group(1)).stem)
        per_query.append({"qid": qid, "ranked": ranked[:k], "rel": rel})
    return per_query, {"index_s": round(t_index, 2), "query_ms": round(1000 * t_q / max(len(queries), 1), 1)}


def ck_arm(md: Path, queries, *, binary: str, mode: str, k: int, model: str):
    """ck over the same folder: `ck --index` once, then `--jsonl` results, one
    `path` per line, deduplicated in rank order."""
    env = {**os.environ, "NO_COLOR": "1"}
    t0 = time.time()
    idx = subprocess.run([binary, "--index", "--model", model, "."], capture_output=True, text=True, env=env, cwd=md)
    if idx.returncode:
        sys.exit(f"ck index: {idx.stderr[-2000:]}")
    t_index = time.time() - t0
    per_query, t_q = [], 0.0
    flag = {"sem": "--sem", "hybrid": "--hybrid", "lex": "--lex"}[mode]
    for qid, text, rel in queries:
        t1 = time.time()
        p = subprocess.run([binary, flag, "--jsonl", "--no-snippet", "--topk", str(k), "--threshold", "0", text, "."],
                           capture_output=True, text=True, env=env, cwd=md)
        t_q += time.time() - t1
        ranked = []
        for line in p.stdout.splitlines():
            try:
                stem = Path(json.loads(line)["path"]).stem
            except (ValueError, KeyError):
                continue
            if stem not in ranked:
                ranked.append(stem)
        per_query.append({"qid": qid, "ranked": ranked[:k], "rel": rel})
    return per_query, {"index_s": round(t_index, 2), "query_ms": round(1000 * t_q / max(len(queries), 1), 1)}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def compare(a_glob: str, b_glob: str, k: int, boots: int = 4000) -> None:
    """Per-query paired difference between two saved runs, with a 95%
    bootstrap interval. Two arms differing by 0.01 on a 300-query corpus is
    noise; this is what says so, and what the README's ✓/~ column reports.

        scripts/bench_beir.py --compare 'scifact-hybrid-sections-nomic-*' 'scifact-lexical-*'
    """
    import random
    random.seed(0)

    def load(pattern: str) -> dict[str, float]:
        # An ambiguous pattern is refused rather than resolved: `scifact-lexical-*`
        # also matches the stemmed run, and picking the last one silently
        # compared against an arm nobody asked for (2026-09-09).
        files = sorted((BENCH / "results").glob(pattern + ".json"))
        if not files:
            sys.exit(f"no results match {pattern}")
        if len(files) > 1:
            listed = "\n  ".join(f.stem for f in files)
            sys.exit(f"{pattern} matches {len(files)} runs; name one:\n  {listed}")
        print(f"  {files[0].name}", file=sys.stderr)
        run = json.loads(files[0].read_text())
        return {q["qid"]: ndcg(q["ranked"], q["rel"], k) for q in run["per_query"]}

    A, B = load(a_glob), load(b_glob)
    qids = sorted(set(A) & set(B))
    if not qids:
        sys.exit("the two runs share no query")
    diffs = [A[q] - B[q] for q in qids]
    n = len(diffs)
    mean = sum(diffs) / n
    draws = sorted(sum(random.choice(diffs) for _ in range(n)) / n for _ in range(boots))
    lo, hi = draws[boots // 40], draws[boots - boots // 40 - 1]
    wins = sum(1 for d in diffs if d > 1e-9)
    losses = sum(1 for d in diffs if d < -1e-9)
    verdict = "clear" if lo > 0 or hi < 0 else "noise (the interval covers zero)"
    print(f"n={n}  diff nDCG@{k} = {mean:+.4f}  95% CI [{lo:+.4f}, {hi:+.4f}]  "
          f"won/lost/tied {wins}/{losses}/{n - wins - losses}  -> {verdict}")


def summarise(per_query, k: int) -> dict:
    n = len(per_query)
    out = {"n": n,
           f"ndcg@{k}": round(sum(ndcg(q["ranked"], q["rel"], k) for q in per_query) / n, 4),
           f"mrr@{k}": round(sum(mrr(q["ranked"], q["rel"], k) for q in per_query) / n, 4),
           f"recall@{k}": round(sum(recall(q["ranked"], q["rel"], k) for q in per_query) / n, 4)}
    if per_query and "pool" in per_query[0]:
        out["pool_recall"] = round(sum(recall(q["pool"], q["rel"], 10**6) for q in per_query) / n, 4)
        out["pool_size"] = len(per_query[0]["pool"])
        out["coverage_mean"] = round(sum(q["coverage"] for q in per_query) / n, 3)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dataset", choices=DATASETS + ("all",), default="all")
    ap.add_argument("--arm", choices=("lexical", "hybrid", "dense", "zg", "ck"), default="lexical")
    ap.add_argument("--mode", default="", help="zg: hybrid|fts|vector; ck: hybrid|sem|lex")
    ap.add_argument("--bin", default="", help="path to the zg or ck binary")
    ap.add_argument("--embedding", default="local/potion-retrieval-32m", help="zg model")
    ap.add_argument("--transport", default="direct", choices=("direct", "server"), help="zg: one process per query, or its daemon with the model resident")
    ap.add_argument("--model", default="bge-small", help="ck model")
    ap.add_argument("--k", type=int, default=10)
    ap.add_argument("--pool-factor", type=int, default=0, help="Silica: the section stage scores k*factor documents (default 3)")
    ap.add_argument("--limit", type=int, default=0, help="first N queries only (a smoke run)")
    ap.add_argument("--label", default="", help="tag for the results file")
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"),
                    help="two result-file globs: print A - B per query with a 95%% bootstrap interval, and run nothing")
    a = ap.parse_args()

    if a.compare:
        compare(a.compare[0], a.compare[1], a.k)
        return 0

    names = DATASETS if a.dataset == "all" else (a.dataset,)
    rows = []
    for name in names:
        d = fetch(name)
        md = write_md(d)
        queries = load_queries(d)
        if a.limit:
            queries = queries[: a.limit]
        if a.arm in ("lexical", "hybrid", "dense"):
            per_query, timing = silica_arm(md, d / f"idx-{a.label or a.arm}", queries,
                                           hybrid=a.arm != "lexical", pool=a.pool_factor, k=a.k, dense_only=a.arm == "dense")
        elif a.arm == "zg":
            per_query, timing = zg_arm(md, queries, binary=a.bin or "zg", mode=a.mode or "hybrid", k=a.k, embedding=a.embedding, transport=a.transport)
        else:
            per_query, timing = ck_arm(md, queries, binary=a.bin or "ck", mode=a.mode or "hybrid", k=a.k, model=a.model)
        s = summarise(per_query, a.k)
        row = {"dataset": name, "arm": a.arm, "mode": a.mode, "label": a.label, **s, **timing,
               "tokenizer": __import__("silica.kernel.recall.lexical", fromlist=["TOKENIZER_VERSION"]).TOKENIZER_VERSION,
               "env": {k: v for k, v in os.environ.items() if k.startswith("SILICA_") and "KEY" not in k}}
        rows.append(row)
        (BENCH / "results").mkdir(exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        (BENCH / "results" / f"{name}-{a.arm}{'-' + a.mode if a.mode else ''}{'-' + a.label if a.label else ''}-{stamp}.json").write_text(
            json.dumps({"summary": row, "per_query": per_query}, indent=1), encoding="utf-8")
    print(f"| dataset | arm | n | nDCG@{a.k} | MRR@{a.k} | R@{a.k} | pool R | index s | query ms |")
    print("|---|---|---|---|---|---|---|---|---|")
    for r in rows:
        print(f"| {r['dataset']} | {r['arm']}{('/' + r['mode']) if r['mode'] else ''}{(' ' + r['label']) if r['label'] else ''} | {r['n']} "
              f"| {r[f'ndcg@{a.k}']:.3f} | {r[f'mrr@{a.k}']:.3f} | {r[f'recall@{a.k}']:.3f} "
              f"| {r.get('pool_recall', float('nan')):.3f} | {r['index_s']} | {r['query_ms']} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
