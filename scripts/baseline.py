#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Whole-task baseline: `claude -p` with and without the Silica plugin on the
same tasks, same model, same tool allowlist, repeated. Counts what the
proposals in docs/2026-09-08-mcp-utility-proposals.md ask to count: the
whole task (schema, searches, reads, retries, the answer), not one reply.

    scripts/baseline.py --model opus --reps 3 --jobs 3
    scripts/baseline.py --summary docs/baseline/<stamp>

Arm A: the plugin as installed, tools and skill. Arm B: the plugin
disabled through a settings override (`enabledPlugins`), everything else
identical, so the only difference between the arms is Silica itself.
`--no-skills` turns every skill off in both arms and measures the tools
alone: in a smoke run with skills off Sonnet never called Silica and
answered both tasks with Grep (2026-09-09). Tasks run in the folder they
are about: the code tasks in this repository, the document tasks in the
254-paper bench corpus (SILICA_BENCH_CORPUS).

Scoring is a rubric of substrings the answer must contain (paths, names,
a digit where a number was asked), checked case-insensitively. It measures
whether the located evidence reached the answer, not prose quality. The
absence task (D3) is scored on an absence phrase and must be read by hand:
a rubric cannot tell a fabricated citation from a real one.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import json
import os
import re
import statistics
import subprocess
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PAPERS = Path(os.environ.get(
    "SILICA_BENCH_CORPUS",
    "/home/kiycoh/Documents/dev/silica-internal/docs/research/papers/md"))
PLUGIN_KEY = "silica-core@silica-core"
SILICA_TOOLS = [f"mcp__plugin_silica-core_silica-core__silica_{t}"
                for t in ("files", "search", "read", "code_pack")]
NATIVE_TOOLS = ["Read", "Grep", "Glob", "Skill", "ToolSearch", "Bash(rg:*)", "Bash(grep:*)",
                "Bash(cat:*)", "Bash(sed:*)", "Bash(head:*)", "Bash(tail:*)", "Bash(ls:*)",
                "Bash(find:*)", "Bash(wc:*)"]
PREAMBLE = ("Work only from the files in the current folder. Cite file paths with line "
            "numbers, or the section or page. If the folder does not contain the answer, "
            "say so plainly instead of guessing. Keep the answer under 150 words.\n\n")

# any_of: every group needs one of its alternatives; all_of: every string;
# digits: at least one digit in the answer. Expectations were read at HEAD
# 910e94f (v0.7.3) for the code tasks; a later edit moves the lines.
TASKS = [
    {"id": "C1", "cwd": REPO, "kind": "code",
     "prompt": "Which function computes the `coverage` value of a search hit, and how does a "
               "query term that occurs nowhere in the corpus enter that number?",
     "all_of": ["silica/core.py"],
     "any_of": [["idf_absent", "df=0", "df = 0", "document frequency zero", "found nowhere",
                 "occurs nowhere", "absent term"]]},
    {"id": "C2", "cwd": REPO, "kind": "code",
     "prompt": "List every call site of the function `best_window_spans` as path:line.",
     "all_of": ["core.py:518", "rerank.py:132", "test_recall_windows.py:25",
                "test_recall_windows.py:30"]},
    {"id": "C3", "cwd": REPO, "kind": "code",
     "prompt": "What does `silica_code_pack` do when a `#L<line>` target points at a line "
               "outside every declaration? Cite the code that decides it.",
     "all_of": ["codepack.py"],
     "any_of": [["file-level", "no symbol", "whole file", "whole-file"]]},
    {"id": "C4", "cwd": REPO, "kind": "code",
     "prompt": "Which modules outside tests/ import `silica.core`, and what does each one "
               "use it for?",
     "all_of": ["cli.py", "mcp.py", "checks.py"]},
    {"id": "D1", "cwd": PAPERS, "kind": "docs",
     "prompt": "Which paper studies what retrieval granularity to use (passage versus "
               "document versus finer units), and what unit does it recommend?",
     "any_of": [["dense-x", "2312.06648"]], "all_of": ["proposition"]},
    {"id": "D2", "cwd": PAPERS, "kind": "docs",
     "prompt": "Is there a paper that measures whether reranking BM25 first-stage candidates "
               "with a cross-encoder or an LLM reranker improves retrieval? Name it and give "
               "one number it reports.",
     "any_of": [["rerank-before-you-reason", "2601.14224", "rankzephyr", "2312.02724",
                 "rankvicuna", "2309.15088", "2003.06713", "monot5", "mono-t5",
                 "document-ranking-with-a-pretrained"]],
     "digits": True},
    {"id": "D3", "cwd": PAPERS, "kind": "docs",
     "prompt": "What do these papers say about Rust's borrow checker and lifetime errors?",
     "any_of": [["does not", "do not", "doesn't", "don't", "no paper", "none of", "nothing",
                 "not found", "not covered", "not discuss", "not address", "not mention",
                 "no discussion", "no mention", "absent", "not contain"]],
     "review": "read by hand: a fabricated citation passes the rubric"},
    {"id": "D4", "cwd": PAPERS, "kind": "docs",
     "prompt": "Which paper tackles the vocabulary mismatch between queries and documents by "
               "expanding documents without dense embeddings, and what is the technique?",
     "any_of": [["1904.08375", "document-expansion", "doc2query", "doct5query",
                 "query prediction", "splade", "2109.10086"]]},
]


def command(arm: str, model: str, max_turns: int, budget: float,
            skills: bool = True) -> list[str]:
    base = ["claude", "-p", "--model", model, "--max-turns", str(max_turns),
            "--max-budget-usd", str(budget), "--output-format", "stream-json", "--verbose",
            "--no-session-persistence"] + ([] if skills else ["--disable-slash-commands"])
    if arm == "A":
        return base + ["--allowedTools", ",".join(NATIVE_TOOLS + SILICA_TOOLS)]
    return base + ["--settings", json.dumps({"enabledPlugins": {PLUGIN_KEY: False}}),
                   "--allowedTools", ",".join(NATIVE_TOOLS)]


def score(task: dict, answer: str) -> tuple[int, list[str]]:
    low = answer.lower()
    missing: list[str] = []
    for s in task.get("all_of", []):
        if s.lower() not in low:
            missing.append(s)
    for group in task.get("any_of", []):
        if not any(s.lower() in low for s in group):
            missing.append(" | ".join(group))
    if task.get("digits") and not re.search(r"\d", answer):
        missing.append("<a number>")
    return (0 if missing else 1), missing


def run_one(task: dict, arm: str, rep: int, model: str, max_turns: int, budget: float,
            timeout: int, skills: bool = True) -> dict:
    # The prompt goes on stdin: `--allowedTools` is variadic and would swallow
    # a trailing positional prompt (measured 2026-09-09: "Input must be provided").
    t0 = time.time()
    proc = subprocess.run(command(arm, model, max_turns, budget, skills), cwd=task["cwd"],
                          input=PREAMBLE + task["prompt"], capture_output=True, text=True,
                          timeout=timeout)
    tools: collections.Counter = collections.Counter()
    result: dict = {}
    for line in proc.stdout.splitlines():
        try:
            d = json.loads(line)
        except ValueError:
            continue
        if d.get("type") == "assistant":
            for c in d.get("message", {}).get("content", []):
                if c.get("type") == "tool_use":
                    tools[c["name"]] += 1
        elif d.get("type") == "result":
            result = d
    answer = result.get("result", "") or ""
    usage = result.get("usage", {}) or {}
    ok, missing = score(task, answer)
    return {"task": task["id"], "kind": task["kind"], "arm": arm, "rep": rep, "model": model,
            "exit": proc.returncode, "subtype": result.get("subtype"),
            "is_error": bool(result.get("is_error")),
            "cost_usd": result.get("total_cost_usd"), "duration_s": round(time.time() - t0, 1),
            "turns": result.get("num_turns"), "tools": dict(tools),
            "tool_calls": sum(tools.values()),
            "silica_calls": sum(v for k, v in tools.items() if "silica" in k),
            "input_tokens": usage.get("input_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_write": usage.get("cache_creation_input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "score": ok, "missing": missing, "answer": answer,
            "stderr_tail": proc.stderr[-400:] if proc.returncode else ""}


def summarize(out: Path) -> str:
    rows = [json.loads(l) for l in (out / "runs.jsonl").read_text().splitlines() if l.strip()]
    by: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in rows:
        by[(r["task"], r["arm"])].append(r)

    def mean(rs, key):
        vals = [r[key] for r in rs if isinstance(r.get(key), (int, float))]
        return statistics.mean(vals) if vals else float("nan")

    lines = ["| task | arm | n | score | cost $ | turns | tool calls | silica calls | "
             "context tok (in+cache) | out tok | s |", "|---|---|---|---|---|---|---|---|---|---|---|"]
    for task in TASKS:
        for arm in ("A", "B"):
            rs = by.get((task["id"], arm), [])
            if not rs:
                continue
            ctx = statistics.mean([(r.get("input_tokens") or 0) + (r.get("cache_read") or 0)
                                   + (r.get("cache_write") or 0) for r in rs])
            lines.append(f"| {task['id']} | {arm} | {len(rs)} | {mean(rs, 'score'):.2f} | "
                         f"{mean(rs, 'cost_usd'):.3f} | {mean(rs, 'turns'):.1f} | "
                         f"{mean(rs, 'tool_calls'):.1f} | {mean(rs, 'silica_calls'):.1f} | "
                         f"{ctx:,.0f} | {mean(rs, 'output_tokens'):,.0f} | {mean(rs, 'duration_s'):.0f} |")
    lines.append("")
    for kind in ("code", "docs", "all"):
        for arm in ("A", "B"):
            rs = [r for r in rows if r["arm"] == arm and (kind == "all" or r["kind"] == kind)]
            if rs:
                lines.append(f"{kind:4s} {arm}: n={len(rs)} score={mean(rs, 'score'):.2f} "
                             f"cost=${mean(rs, 'cost_usd'):.3f} turns={mean(rs, 'turns'):.1f} "
                             f"tools={mean(rs, 'tool_calls'):.1f}")
    errors = [r for r in rows if r["is_error"] or r["exit"]]
    if errors:
        lines.append(f"\n{len(errors)} run(s) errored or hit a cap: "
                     + ", ".join(f"{r['task']}/{r['arm']}/{r['rep']}:{r['subtype']}" for r in errors))
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--model", default="opus")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--max-turns", type=int, default=40)
    ap.add_argument("--budget", type=float, default=3.0, help="max USD per run")
    ap.add_argument("--timeout", type=int, default=900, help="seconds per run")
    ap.add_argument("--tasks", default="", help="comma-separated ids; empty = all")
    ap.add_argument("--arms", default="A,B")
    ap.add_argument("--out", default="", help="results folder; default docs/baseline/<stamp>")
    ap.add_argument("--no-skills", action="store_true", help="disable every skill in both arms")
    ap.add_argument("--summary", default="", help="only summarise this results folder")
    a = ap.parse_args()
    if a.summary:
        print(summarize(Path(a.summary)))
        return 0
    out = Path(a.out) if a.out else REPO / "docs" / "baseline" / time.strftime("%Y-%m-%d-%H%M")
    out.mkdir(parents=True, exist_ok=True)
    runs = out / "runs.jsonl"
    done = set()
    if runs.exists():
        for l in runs.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                done.add((r["task"], r["arm"], r["rep"]))
    wanted = [t for t in TASKS if not a.tasks or t["id"] in a.tasks.split(",")]
    grid = [(t, arm, rep) for t in wanted for rep in range(1, a.reps + 1)
            for arm in a.arms.split(",") if (t["id"], arm, rep) not in done]
    print(f"{len(grid)} runs -> {runs}  (model {a.model}, jobs {a.jobs})", flush=True)
    lock = threading.Lock()

    def job(t, arm, rep):
        try:
            r = run_one(t, arm, rep, a.model, a.max_turns, a.budget, a.timeout, not a.no_skills)
        except subprocess.TimeoutExpired:
            r = {"task": t["id"], "kind": t["kind"], "arm": arm, "rep": rep, "model": a.model,
                 "exit": -1, "subtype": "timeout", "is_error": True, "cost_usd": None,
                 "duration_s": a.timeout, "turns": None, "tools": {}, "tool_calls": 0,
                 "silica_calls": 0, "input_tokens": None, "cache_read": None,
                 "cache_write": None, "output_tokens": None, "score": 0, "missing": ["timeout"],
                 "answer": "", "stderr_tail": ""}
        with lock:
            with runs.open("a") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"{r['task']} {r['arm']} rep{r['rep']}: score={r['score']} cost={r['cost_usd']} "
                  f"turns={r['turns']} tools={r['tool_calls']} silica={r['silica_calls']} "
                  f"{r['duration_s']}s {r['subtype'] or ''}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        list(pool.map(lambda g: job(*g), grid))
    (out / "summary.md").write_text(summarize(out) + "\n")
    print(summarize(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
