#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Score the answers of a scripts/baseline.py grid on the SWE-QA questions
against the reference answers zvec-grep's benchmark isolates for its judge
(bench/swe-qa/references.json), blind to the arm: the judge sees the
question, the reference and one answer, never which tools produced it.

    scripts/judge.py docs/baseline/<folder> [--model sonnet]

Writes `judge.jsonl` beside `runs.jsonl` (one line per run, resumable) and
prints the mean score per task and arm. The score is 0-100 for how much of
the reference's substance the answer states correctly; a claim the reference
contradicts costs, an omission costs, extra correct detail is neutral.
"""
from __future__ import annotations

import argparse
import collections
import json
import statistics
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PROMPT = """You are grading an answer to a question about a software repository against a reference answer written by someone who read the code.

Question:
{question}

Reference answer:
{reference}

Candidate answer:
{answer}

Score the candidate from 0 to 100 for how much of the reference's substance it states correctly: the components named, the mechanism, the control or data flow, the reasons. An omission costs points in proportion to its weight in the reference; a claim the reference contradicts costs more; correct detail beyond the reference is neutral; file paths and line numbers are neutral unless they contradict the reference. A candidate that says the repository does not contain the answer scores 0.

Reply with one JSON object and nothing else: {{"score": <integer>, "missing": ["<what the candidate lacks, briefly>", ...], "wrong": ["<what it gets wrong>", ...]}}"""


def judge(question: str, reference: str, answer: str, model: str) -> dict:
    p = subprocess.run(["claude", "-p", "--model", model, "--output-format", "json", "--no-session-persistence",
                        "--max-turns", "1", "--tools", ""],
                       input=PROMPT.format(question=question, reference=reference, answer=answer or "(no answer)"),
                       capture_output=True, text=True, timeout=300)
    try:
        out = json.loads(p.stdout)
        text = out.get("result", "")
        start, end = text.find("{"), text.rfind("}")
        verdict = json.loads(text[start:end + 1])
        return {"score": int(verdict["score"]), "missing": verdict.get("missing", []), "wrong": verdict.get("wrong", []),
                "cost_usd": out.get("total_cost_usd")}
    except (ValueError, KeyError, TypeError) as e:
        return {"score": None, "error": f"{e}: {p.stdout[-300:]} {p.stderr[-300:]}"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("folder")
    ap.add_argument("--model", default="sonnet")
    a = ap.parse_args()
    folder = Path(a.folder)
    refs = {r["task_id"]: r["reference_answer"]
            for r in json.loads((REPO / "bench" / "swe-qa" / "references.json").read_text())["references"]}
    questions = {t["task_id"]: t["question"] for t in json.loads((REPO / "bench" / "swe-qa" / "tasks.json").read_text())}
    out = folder / "judge.jsonl"
    done = {}
    if out.is_file():
        for line in out.read_text().splitlines():
            if line.strip():
                j = json.loads(line)
                done[(j["task"], j["arm"], j["rep"])] = j
    runs = [json.loads(l) for l in (folder / "runs.jsonl").read_text().splitlines() if l.strip()]
    with out.open("a") as fh:
        for r in runs:
            key = (r["task"], r["arm"], r["rep"])
            if r["kind"] != "swe" or key in done or r["task"] not in refs or r.get("subtype") == "limit" or r["exit"]:
                continue
            v = judge(questions[r["task"]], refs[r["task"]], r["answer"], a.model)
            row = {"task": r["task"], "arm": r["arm"], "rep": r["rep"], "judge": a.model, **v}
            done[key] = row
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            fh.flush()
            print(f"{r['task']:14s} {r['arm']} rep{r['rep']}: {v.get('score')} {v.get('error', '')[:120]}", flush=True)
    by = collections.defaultdict(list)
    for j in done.values():
        if j.get("score") is not None:
            by[(j["task"], j["arm"])].append(j["score"])
    arms = sorted({arm for _t, arm in by})
    print("\n| task | " + " | ".join(arms) + " |")
    print("|---|" + "---|" * len(arms))
    for task in sorted({t for t, _a in by}):
        print(f"| {task} | " + " | ".join(f"{statistics.mean(by[(task, arm)]):.0f}" if by.get((task, arm)) else "" for arm in arms) + " |")
    for arm in arms:
        vals = [s for (t, ar), ss in by.items() if ar == arm for s in ss]
        print(f"{arm}: n={len(vals)} judge={statistics.mean(vals):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
