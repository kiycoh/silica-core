#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""PROTOTYPE, throwaway (handoff 2026-09-10): does query-time expansion of the
hybrid ranking along the code graph bring gold into the pool, and does a
deterministic typed rerank turn that into a better top-10?

One hybrid search per task (k=10, per_doc=2, potion), then three arms from
the same hits, so the comparison is paired on identical anchors:

    hybrid   the product's ranking as is
    expand   pool = hits + neighbours of the top-A hits along `contains` and
             resolved `calls` (both directions, <= HOPS); every edge weighs
             the same, no hub penalty: the naive graph arm
    rerank   same pool; edge weight by type and direction, hub symbols
             damped by their fan-in, a small bonus when the neighbour's name
             shares a token with the query

Score of a node = 1/(60+rank) if it is a hybrid hit, plus lambda * sum over
the anchors that reach it of w(edge) * 1/(60+anchor rank) * decay^(hop-1).
The pool table is the ceiling any reranker has: gold files and gold symbol
targets that the neighbours locate and no hybrid hit did, and which edge
brought them.

    scripts/bench_expand.py                 # all 32 tasks, three arms, paired compare
    scripts/bench_expand.py --anchors 10 --hops 1 --per-type 4 --lam 0.5

`chars` is the hybrid hits' JSON share for the hits kept and 800 per
structural node (a 600-char window plus metadata); `ms` is search plus
expansion and rerank, the graph load once per root is reported apart.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import math
import sys
import time
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
_spec = importlib.util.spec_from_file_location("bench_code", REPO / "scripts" / "bench_code.py")
bc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bc)

RRF_K = 60
STRUCT_CHARS = 800
# typed weights: SpIDER finds `invokes` the useful relation, `contains` second
W_TYPED = {"calls_out": 1.0, "calls_in": 0.8, "contains_up": 0.5, "contains_down": 0.4}
W_FLAT = {k: 1.0 for k in W_TYPED}
ORDER = ("calls_out", "calls_in", "contains_up", "contains_down")

Node = tuple[str, str, int, int]  # (file, qualified title, line, end_line); title "" = module level


class Adj:
    """Once per graph: symbols by file and name, reverse call index, hubness."""

    def __init__(self, graph):
        self.g = graph
        self.by_q: dict[str, dict[str, dict]] = {}
        self.by_name: dict[str, dict[str, dict]] = {}
        self.children: dict[str, dict[str, list[dict]]] = {}
        self.rev: dict[tuple[str, str], list[tuple[str, str, int]]] = defaultdict(list)
        callers: dict[tuple[str, str], set[str]] = defaultdict(set)
        for f, e in graph.files.items():
            q, n, ch = {}, {}, defaultdict(list)
            for s in e.get("symbols", []):
                s = {**s, "q": f"{s['parent']}.{s['name']}" if s.get("parent") else s["name"]}
                q.setdefault(s["q"], s)
                n.setdefault(s["name"], s)
                if s.get("parent"):
                    ch[s["parent"]].append(s)
            self.by_q[f], self.by_name[f], self.children[f] = q, n, ch
            for c in e.get("calls", []):
                self.rev[(c["target"], c["callee"])].append((f, c["caller"], c["line"]))
                callers[(c["target"], c["callee"])].add(f)
        self.indeg = {k: len(v) for k, v in callers.items()}  # distinct caller files per callee

    def resolve(self, file: str, callee: str, depth: int = 0) -> Node | None:
        s = self.by_q.get(file, {}).get(callee) or self.by_name.get(file, {}).get(callee.rsplit(".", 1)[-1])
        if s:
            return (file, s["q"], s["line"], s["end_line"])
        if depth == 0:
            for r in self.g.files.get(file, {}).get("reexports", []):
                if r["name"] == callee.rsplit(".", 1)[-1]:
                    return self.resolve(r["from"], callee, 1)
        return None

    def node(self, file: str, title: str) -> Node | None:
        s = self.by_q.get(file, {}).get(title)
        return (file, s["q"], s["line"], s["end_line"]) if s else None

    def neighbours(self, n: Node) -> list[tuple[str, Node]]:
        file, title, line, _end = n
        top = title.split(".")[0]
        out: list[tuple[str, Node]] = []
        for c in self.g.files.get(file, {}).get("calls", []):
            if c["caller"] == top:
                t = self.resolve(c["target"], c["callee"])
                if t and t != n:
                    out.append(("calls_out", t))
        if title:
            # a method's callers are calls spelled with its name; calls to the
            # class (its constructor) are the class's, not every method's
            names = {title, title.rsplit(".", 1)[-1]}
            for name in names:
                for src, caller, ln in self.rev.get((file, name), []):
                    if caller:
                        s = self.by_q.get(src, {}).get(caller)
                        t = (src, s["q"], s["line"], s["end_line"]) if s else None
                    else:
                        t = (src, "", ln, ln)  # a module-level call site
                    if t and t != n:
                        out.append(("calls_in", t))
            if "." in title:
                p = self.node(file, title.split(".", 1)[0])
                if p:
                    out.append(("contains_up", p))
            for s in self.children.get(file, {}).get(title, []):
                out.append(("contains_down", (file, s["q"], s["line"], s["end_line"])))
        out.sort(key=lambda x: ORDER.index(x[0]))
        seen: set[Node] = set()
        return [(k, t) for k, t in out if not (t in seen or seen.add(t))]

    def hub(self, n: Node) -> int:
        file, title, *_ = n
        return max(self.indeg.get((file, title), 0), self.indeg.get((file, title.rsplit(".", 1)[-1]), 0))


def expand(adj: Adj, anchors: list[Node], hops: int, per_type: int) -> dict[Node, list[tuple[int, str, int]]]:
    """node -> [(anchor rank, edge type, hop)] over a BFS per anchor with a
    budget per edge type per hop: a class's forty callers or a caller's
    forty callees are truncated instead of starving the other relations."""
    reach: dict[Node, list[tuple[int, str, int]]] = defaultdict(list)
    for rank, a in enumerate(anchors, 1):
        if a is None:
            continue
        frontier, seen = [a], {a}
        for hop in range(1, hops + 1):
            nxt: list[Node] = []
            spent: dict[str, int] = defaultdict(int)
            for cur in frontier:
                for kind, t in adj.neighbours(cur):
                    if t in seen or spent[kind] >= per_type:
                        continue
                    seen.add(t)
                    spent[kind] += 1
                    reach[t].append((rank, kind, hop))
                    nxt.append(t)
            frontier = nxt
    return reach


def _tokens(s: str) -> set[str]:
    import silica.core as core
    return set(core._tokens(s))


def rerank(hits: list[dict], reach: dict, typed: bool, lam: float, decay: float, adj: Adj, query: str) -> list[dict]:
    w = W_TYPED if typed else W_FLAT
    qt = _tokens(query) if typed else set()
    nodes: dict[Node, dict] = {}
    score: dict[Node, float] = defaultdict(float)
    for r, h in enumerate(hits, 1):
        n = (h["file"], h["section"], h["range"][0], h["range"][1])
        nodes[n] = {**h, "via": f"hybrid#{r}"}
        score[n] += 1 / (RRF_K + r)
    for n, ways in reach.items():
        s = sum(w[k] / (RRF_K + r) * decay ** (hop - 1) for r, k, hop in ways)
        if typed:
            s /= 1 + math.log2(1 + adj.hub(n))
            if qt & _tokens(n[1]):
                s *= 1.5
        # a hybrid hit that the graph also reaches keeps its own node
        key = n if n in nodes else next((m for m in nodes if m[0] == n[0] and m[1] == n[1] and n[1]), n)
        score[key] += lam * s
        if key not in nodes:
            nodes[key] = {"file": n[0], "range": [n[2], n[3]], "anchor": n[2], "section": n[1],
                          "via": ",".join(f"{k}<#{r}@{hop}" for r, k, hop in ways[:3])}
    return [nodes[n] for n in sorted(score, key=lambda n: (-score[n], n))]


def pool_ceiling(task: dict, hits: list[dict], reach: dict) -> dict:
    """What the neighbours locate that no hybrid hit did, and which edge brought it."""
    gold_files = {g["file"] for g in task["gold"]}
    hit_files = {h["file"] for h in hits}
    new_files = sorted(f for f in gold_files if f not in hit_files and any(n[0] == f for n in reach))
    targets = [(g["file"], s) for g in task["gold"] for s in bc.spans(task["root"], g["file"], g.get("lines", []))]

    def loc(n: Node, file: str, span: tuple[int, int]) -> bool:
        if n[0] != file:
            return False
        a, b, (s, e) = n[2], n[3], span
        return s <= a <= e or (not (b < s or a > e) and (b - a + 1) <= 3 * (e - s + 1) + 10)

    by_hits = [t for t in targets if any(loc((h["file"], "", h["anchor"], h["range"][1]), *t) for h in hits)]
    new_syms, new_nodes, edges = [], [], []
    for t in targets:
        if t in by_hits:
            continue
        via = [n for n in reach if loc(n, *t)]
        if via:
            new_syms.append(f"{t[0]}:{t[1][0]}")
            new_nodes.append(via)
            edges += [k for n in via for _r, k, _h in reach[n]]
    return {"neighbours": len(reach), "gold_files": len(gold_files), "new_files": new_files,
            "targets": len(targets), "located_by_hits": len(by_hits), "new_syms": new_syms,
            "new_nodes": new_nodes, "edges": sorted(set(edges))}


def paired(ra: list[dict], rb: list[dict], at: str, la: str, lb: str, boots: int = 4000) -> None:
    import random
    import statistics
    A = {r["id"]: r for r in ra}
    B = {r["id"]: r for r in rb}
    ids = sorted(set(A) & set(B))
    print(f"{la} vs {lb} @{at[2:]}, n={len(ids)} paired")
    rng = random.Random(0)
    for key in ("file_hit", "file_rr", "file_recall", "sym_hit", "sym_recall"):
        d = [(B[i][at][key] or 0) - (A[i][at][key] or 0) for i in ids
             if A[i][at].get(key) is not None and B[i][at].get(key) is not None]
        means = sorted(statistics.mean(rng.choices(d, k=len(d))) for _ in range(boots))
        lo, hi = means[int(0.025 * boots)], means[int(0.975 * boots)]
        wins, losses = sum(1 for x in d if x > 0), sum(1 for x in d if x < 0)
        verdict = "noise" if lo <= 0 <= hi else ("B better" if lo > 0 else "A better")
        print(f"  {key:12s} diff {statistics.mean(d):+.3f}  95% CI [{lo:+.3f}, {hi:+.3f}]  "
              f"wins {wins} losses {losses} ties {len(d) - wins - losses} -> {verdict}")


def table_at(rows: dict[str, list[dict]], at: str) -> str:
    import statistics
    k = at[2:]
    lines = [f"| arm | set | n | file_hit@{k} | file_rr@{k} | file_recall@{k} | sym_hit@{k} | sym_recall@{k} | chars@{k} |",
             "|---|---|---|---|---|---|---|---|---|"]
    for arm, rs_all in rows.items():
        for s in ("zvec", "local", "all"):
            rs = [r for r in rs_all if s == "all" or r["set"] == s]
            m = lambda key: statistics.mean(v for v in (r[at][key] for r in rs) if v is not None)  # noqa: E731
            lines.append(f"| {arm} | {s} | {len(rs)} | {m('file_hit'):.2f} | {m('file_rr'):.2f} | {m('file_recall'):.2f} | "
                         f"{m('sym_hit'):.2f} | {m('sym_recall'):.2f} | {statistics.mean(r['chars'] for r in rs):,.0f} |")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--anchors", type=int, default=5)
    ap.add_argument("--hops", type=int, default=2)
    ap.add_argument("--per-type", type=int, default=6, help="neighbours per edge type per hop per anchor")
    ap.add_argument("--lam", type=float, default=1.0)
    ap.add_argument("--decay", type=float, default=0.5)
    ap.add_argument("--k", type=int, default=10, help="hybrid hits, and anchors come from their top")
    ap.add_argument("--k-out", type=int, default=20, help="length of the expanded lists; hybridK is the rival with this k")
    ap.add_argument("--tasks", choices=("zvec", "local", "all"), default="all")
    ap.add_argument("--only", default="")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    from silica.config import CONFIG
    from silica.kernel.code.codegraph import load_codegraph
    tasks = bc.load_tasks(a.tasks)
    if a.only:
        tasks = [t for t in tasks if t["id"] in a.only.split(",")]
    graphs: dict[str, tuple[Adj | None, float]] = {}
    K = f"hybrid{a.k_out}"
    rows = {"hybrid": [], K: [], "expand": [], "rerank": []}
    pools = []
    for t in tasks:
        hits, chars, ms, info = bc.silica_arm(t, "hybrid", a.k, 2)
        hits_k, chars_k, ms_k, _ = bc.silica_arm(t, "hybrid", a.k_out, 2)  # just ask for more: the rival of a bigger pool
        if t["root"] not in graphs:
            CONFIG.vault_path = t["root"]
            t0 = time.time()
            g = load_codegraph(t["root"])
            graphs[t["root"]] = (Adj(g) if g else None, time.time() - t0)
        adj, _ = graphs[t["root"]]
        t0 = time.time()
        reach = {}
        if adj:
            anchors = [adj.node(h["file"], h["section"]) if h["section"] else (h["file"], "", h["range"][0], h["range"][1])
                       for h in hits[:a.anchors]]
            reach = expand(adj, anchors, a.hops, a.per_type)  # hybrid hits stay in reach: they gain structural score
        per_hit = chars / len(hits) if hits else 0
        arms = {"hybrid": hits, K: hits_k,
                "expand": rerank(hits, reach, False, a.lam, a.decay, adj, t["question"]) if adj else hits,
                "rerank": rerank(hits, reach, True, a.lam, a.decay, adj, t["question"]) if adj else hits}
        xms = 1000 * (time.time() - t0)
        for arm, hs in arms.items():
            hs = hs[:a.k_out]
            ch = chars_k if arm == K else sum(per_hit if h.get("via", "hybrid").startswith("hybrid") else STRUCT_CHARS for h in hs)
            rows[arm].append({"id": t["id"], "set": t["set"], "hits": hs, "chars": round(ch),
                              "ms": round(ms_k if arm == K else ms + (0 if arm == "hybrid" else xms)), "info": info,
                              "at5": bc.score(t, hs, 5), "at10": bc.score(t, hs, 10),
                              f"at{a.k_out}": bc.score(t, hs, a.k_out)})
        pc = pool_ceiling(t, hits, reach)
        # where the typed rerank puts each gold node the pool recovered
        order = [(h["file"], h["range"][0]) for h in arms["rerank"]]
        titles = [(h["file"], h["section"]) for h in arms["rerank"]]
        pc["conv"] = [min((order.index((n[0], n[2])) + 1 if (n[0], n[2]) in order else titles.index((n[0], n[1])) + 1
                           for n in via if (n[0], n[2]) in order or (n[0], n[1]) in titles), default=None)
                      for via in pc.pop("new_nodes")]
        pools.append({"id": t["id"], "set": t["set"], **pc})
        print(f"{t['id']:14s} rr h/e/r {rows['hybrid'][-1]['at10']['file_rr']:.2f}/{rows['expand'][-1]['at10']['file_rr']:.2f}/"
              f"{rows['rerank'][-1]['at10']['file_rr']:.2f}  symrec {rows['hybrid'][-1]['at10']['sym_recall']}/"
              f"{rows['rerank'][-1]['at10']['sym_recall']}  pool {pc['neighbours']:3d} new files {pc['new_files']} "
              f"new syms {len(pc['new_syms'])} via {pc['edges']} rerank puts them at {pc['conv']}  {xms:,.0f} ms", flush=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    tag = a.tag or f"a{a.anchors}h{a.hops}t{a.per_type}l{a.lam:g}"
    out = bc.BENCH / "results"
    paths = {}
    for arm, rs in rows.items():
        p = out / f"xh-{arm}-{tag}-{stamp}.json"
        p.write_text(json.dumps({"label": f"xh-{arm}-{tag}", "arm": "silica", "mode": arm, "k": a.k, "per_doc": 2,
                                 "params": vars(a), "rows": rs}, indent=1))
        paths[arm] = p
        print(bc.table(rs, f"xh-{arm}"))
    (out / f"xh-pool-{tag}-{stamp}.json").write_text(json.dumps(pools, indent=1))
    print("\ngraph load per root (s):", {Path(r).name: round(s, 1) for r, (_, s) in graphs.items()})
    print("\npool ceiling (gold the neighbours locate and no hybrid hit did):")
    print("| set | n | neighbours/task | tasks with new gold file | new gold files | tasks with new gold symbol | new gold symbols | edges that brought them |")
    print("|---|---|---|---|---|---|---|---|")
    from collections import Counter
    for s in ("zvec", "local", "all"):
        ps = [p for p in pools if s == "all" or p["set"] == s]
        if not ps:
            continue
        ec = Counter(e for p in ps for e in p["edges"])
        print(f"| {s} | {len(ps)} | {sum(p['neighbours'] for p in ps) / len(ps):.0f} | "
              f"{sum(1 for p in ps if p['new_files'])} | {sum(len(p['new_files']) for p in ps)} of "
              f"{sum(p['gold_files'] for p in ps)} | {sum(1 for p in ps if p['new_syms'])} | "
              f"{sum(len(p['new_syms']) for p in ps)} of {sum(p['targets'] - p['located_by_hits'] for p in ps)} missed | "
              f"{dict(ec)} |")
    conv = [r for p in pools for r in p["conv"] if r is not None]
    print(f"\nrecovered gold symbols: {len(conv)}; rank under the typed rerank: {sorted(conv)}")
    print(f"\n@{a.k_out}: the expanded lists against the hybrid tail\n" + table_at(rows, f"at{a.k_out}") + "\n")
    paired(rows["hybrid"], rows["expand"], "at10", "hybrid", "expand")
    paired(rows["hybrid"], rows["rerank"], "at10", "hybrid", "rerank")
    paired(rows["expand"], rows["rerank"], "at10", "expand", "rerank")
    at = f"at{a.k_out}"
    paired(rows["hybrid"], rows["rerank"], at, "hybrid (10 hits)", "rerank")
    paired(rows[K], rows["expand"], at, K, "expand")
    paired(rows[K], rows["rerank"], at, K, "rerank")
    return 0


if __name__ == "__main__":
    sys.exit(main())
