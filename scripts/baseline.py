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
Arm S is arm A with the code index on (one unit per symbol, potion
vectors); arm Z is arm B plus zvec-grep's MCP search and its guidance, the
three-arm comparison of docs/2026-09-10-zvec-parity-plan.md; both leave
every native tool in and order nothing, and `--prepare` builds both
indexes first so a run measures queries, not indexing. Every run's stream
is kept under `streams/`.
Arm F names the three tools in the prompt. Arm P exposes `silica_code_pack`
alone beside the native tools and asks for grep to locate and code_pack to
read: the second variant of docs/2026-09-10-code-retrieval-research.md,
what the structure the code graph already keeps is worth once grep has
found the symbol.
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

Every invocation appends one line to `manifest.jsonl` in the results
folder: the runner's own hash and the checkout it ran from, the arguments,
the client's version, the SKILL.md arm A loads, the allowlists and prompt
preambles, each task with its rubric, and the identity of every folder a
task ran in: a hash over the paths and bytes of every file under it, hidden
folders aside, whatever git tracks, ignores or has never seen (a corpus of
papers sits in an ignored folder; the 2026-09-09 grids ran on dirty trees),
plus HEAD and the porcelain status when the folder is a checkout, for the
reader. A hash detects a change; rereading the old corpus takes the kept
files or the git revision.
"""
from __future__ import annotations

import argparse
import collections
import concurrent.futures
import hashlib
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
# Arm F: the same plugin, named in the prompt. Separates "not chosen" from
# "not useful": the value of the retrieval when it is used.
FORCED = ("Silica tools are available in this session: use silica_search to search, "
          "silica_read to read a passage, and silica_code_pack for a source file, instead "
          "of grep and file reads.\n\n")
# Arm P: grep to locate, code_pack to read. The pack is the only reader:
# Read, cat, sed, head and tail go in `--disallowedTools`, because Read, Grep
# and Glob never ask permission and an allowlist alone leaves Read in
# (measured 2026-09-10: a Read outside the allowlist went through). Search
# and read are outside the allowlist, so a call to them is refused. The arm
# measures what the pack serves once grep has found the symbol, not whether
# the model prefers it (haiku did not, with Read allowed). `rg -C` still
# shows lines, which is grep's job here. The preamble names the `target`
# argument: without it haiku passed `path` on every call and every call
# failed schema validation (6 of 6 on the first grid).
# The wording is an order, like arm F's: with the readers refused and the
# pack merely offered, haiku read through `Grep -C` and never called it
# (smoke of 2026-09-10, 0 calls). Agent is refused too: without permission it
# spawns an Explore subagent whose reads escape the arm.
PACK = ("File reads are disabled in this session: Read, cat, sed, head, tail, subagents, "
        "silica_search and silica_read are refused. Work in two steps. First locate the symbol "
        "with Grep, rg, Glob, ls or find. Then, before answering, read it with silica_code_pack: "
        "its `target` argument is the root-relative path narrowed with `#Class`, `#Class.member` "
        "or `#L<line>` (the symbol whose declaration spans that line); it returns the declaration "
        "together with its callers, hierarchy and importers inside a character budget. Cite from "
        "the pack, not from grep output alone.\n\n")
PACK_TOOLS = [t for t in SILICA_TOOLS if t.endswith("code_pack")]
# Arm S: the plugin as arm A, with the code index on: one unit per symbol
# and the potion vectors, through the server's environment (a stdio MCP
# server inherits this process's). Arm Z: the plugin off and zvec-grep's
# MCP search beside the native tools, its daemon serving the workspace index
# `zg index` built beforehand (`--prepare` builds both sides' indexes and
# records the seconds), the guidance `zg install` writes for Claude Code
# appended to the system prompt from scripts/zvec-guidance.md. Neither arm
# forbids a native tool or orders the search: what is measured is the
# choice, as in docs/2026-09-10-zvec-parity-plan.md.
CODE_ENV = {"SILICA_INDEX_CODE": "1", "SILICA_EMBEDDING_MODEL": "model2vec/minishlab/potion-retrieval-32M"}
ZG = os.environ.get("ZG_BIN", str(Path.home() / "tools" / "node_modules" / ".bin" / "zg"))
ZG_TOOLS = ["mcp__zvec-grep__zvec_grep_search"]
ZG_MCP = json.dumps({"mcpServers": {"zvec-grep": {"command": ZG, "args": ["server", "--stdio"]}}})
ZG_GUIDANCE = REPO / "scripts" / "zvec-guidance.md"
PACK_DISALLOWED = ["Read", "Bash(cat:*)", "Bash(sed:*)", "Bash(head:*)", "Bash(tail:*)", "Agent"]
PACK_NATIVE = [t for t in NATIVE_TOOLS if t not in PACK_DISALLOWED]
INSPIRATION = REPO.parent / "silica-core_inspiration"

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
     # the call sites are read from the tree at start: an edit above one of
     # them moved core.py's from 518 to 523 between two runs on 2026-09-09
     "all_of": [f"{f}:{n}" for f, n in (
         (f, i) for f in ("silica/core.py", "silica/kernel/recall/rerank.py", "tests/test_recall_windows.py")
         for i, line in enumerate((REPO / f).read_text().splitlines(), 1)
         if "best_window_spans(" in line and not line.lstrip().startswith(("def ", "from ", "import ")))]
     },
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
     # any paper in the corpus that reranks BM25 candidates and reports a number
     "any_of": [["rerank-before-you-reason", "2601.14224", "rankzephyr", "2312.02724",
                 "rankvicuna", "2309.15088", "2003.06713", "monot5", "mono-t5",
                 "document-ranking-with-a-pretrained", "rankgpt", "2304.09542",
                 "is-chatgpt-good-at-search"]],
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

# Harder: repositories of 470 to 3,800 source files, questions that name no
# identifier and no file, an answer that sits in one function's body.
# Expectations verified by grep on 2026-09-09.
HARD_TASKS = [
    {"id": "H1", "cwd": INSPIRATION / "serena", "kind": "hard",
     "prompt": "Where does the code decide what to do when a tool's answer is too long for the "
               "client, and what is the default limit in characters?",
     "all_of": ["tools_base.py"], "any_of": [["150_000", "150,000", "150000", "150 000", "150k"]]},
    {"id": "H2", "cwd": INSPIRATION / "codegraph", "kind": "hard",
     "prompt": "Which MCP tools does the server expose by default, and where is that default "
               "list defined?",
     "all_of": ["explore", "tools.ts"]},
    {"id": "H3", "cwd": INSPIRATION / "codegraph", "kind": "hard",
     "prompt": "When the relevance floor would leave the explore reply with too few files even "
               "though the gather step found candidates, what does the server do and why? Cite "
               "the code.",
     "all_of": ["tools.ts"],
     "any_of": [["backfill", "back-fill", "best-scoring", "falls straight back to grep",
                 "worst outcome"]]},
    {"id": "H4", "cwd": INSPIRATION / "repowise", "kind": "hard",
     "prompt": "When a reply is truncated to fit its budget, how can the agent recover the "
               "dropped content? Give the marker format and the tool that accepts it.",
     "any_of": [["repowise#"]], "all_of": ["get_symbol"]},
    {"id": "H5", "cwd": INSPIRATION / "claude-context", "kind": "hard",
     "prompt": "What similarity threshold does the MCP search handler pass to the core search, "
               "and what default does the core search function itself declare? Cite both.",
     "all_of": ["handlers.ts", "context.ts"], "any_of": [["0.3"], ["0.5"]]},
    {"id": "H6", "cwd": INSPIRATION / "serena", "kind": "hard",
     "prompt": "What does the Claude Code hook do after several consecutive grep or read-file "
               "calls? Cite the code.",
     "all_of": ["hooks.py"], "any_of": [["deny"]]},
    {"id": "H7", "cwd": INSPIRATION / "code-context-engine", "kind": "hard",
     "prompt": "When no chunk passes the confidence threshold but some chunks were scored, what "
               "does the retriever return? Cite the code.",
     "all_of": ["retriever.py"],
     "any_of": [["scored[:1]", "top-1", "top 1", "first", "single", "one result", "highest",
                 "best"]]},
    {"id": "H8", "cwd": INSPIRATION / "repowise", "kind": "hard",
     "prompt": "How is the three-level retrieval quality label of an answer decided, and what "
               "does 'weak' mean? Cite the code.",
     "all_of": ["confidence.py"], "any_of": [["dominant", "runner-up", "runner up"]]},
]


# The 20 SWE-QA questions zvec-grep publishes (bench/swe-qa/tasks.json, the
# checkouts pinned at the commits selection.json names), no rubric: the
# reference answers are long and judge-scored in their protocol, so a run
# records the answer and scripts/judge.py scores it blind to the arm.
SWE_TASKS = [
    {"id": t["task_id"], "cwd": Path(t["root"]), "kind": "swe", "prompt": t["question"]}
    for t in json.loads((REPO / "bench" / "swe-qa" / "tasks.json").read_text())
] if (REPO / "bench" / "swe-qa" / "tasks.json").is_file() else []


def _git(cwd: Path, *args: str) -> str:
    try:
        # rstrip, not strip: a porcelain line opens with its index column (" M a.md")
        return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                              timeout=60).stdout.rstrip("\n")
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def identity(folder: Path) -> dict:
    """What a run read in `folder`: `sha256` over the path and bytes of every
    regular file under it, hidden folders and symlinks aside, whether git
    tracks it, ignores it or never saw it — a task reads the folder, not the
    index. HEAD and the porcelain status ride along when the folder is a
    checkout, for the reader; neither is the identity, since an ignored
    corpus or an untracked folder changes under an unchanged HEAD and a
    status line names a folder, not its bytes. Measured 2026-09-09: 1.0 s
    for the 7,115-file paper corpus, under 0.7 s for each code checkout."""
    h, n = hashlib.sha256(), 0
    for dirpath, dirnames, filenames in os.walk(folder):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith("."))
        for name in sorted(filenames):
            p = Path(dirpath) / name
            if name.startswith(".") or p.is_symlink() or not p.is_file():
                continue
            h.update(p.relative_to(folder).as_posix().encode() + b"\0" + p.read_bytes() + b"\0")
            n += 1
    out = {"path": str(folder), "files": n, "sha256": h.hexdigest()}
    head = _git(folder, "rev-parse", "HEAD")
    if head:
        status = _git(folder, "status", "--porcelain", "--", ".")
        out.update(head=head, dirty=bool(status), changed=status.splitlines())
    return out


def skill_loaded(plugin_dir: str) -> dict | None:
    """The SKILL.md arm A loads: the checkout's under `--plugin-dir`, else the
    install Claude Code records in installed_plugins.json; None when neither
    exists, which is itself a finding."""
    if plugin_dir:
        path = Path(plugin_dir) / "silica" / "skills" / "silica" / "SKILL.md"
    else:
        reg = Path.home() / ".claude" / "plugins" / "installed_plugins.json"
        try:
            entries = json.loads(reg.read_text()).get("plugins", {}).get(PLUGIN_KEY, [])
        except (OSError, ValueError):
            entries = []
        if not entries:
            return None
        path = Path(entries[-1]["installPath"]) / "silica" / "skills" / "silica" / "SKILL.md"
    try:
        return {"path": str(path), "sha256": _sha256(path.read_bytes())}
    except OSError:
        return None


def write_manifest(out: Path, args: dict, tasks: list[dict]) -> Path:
    """One line per invocation: a results folder grows over several (E1–E3
    each added arms to one), so the manifest is a log, never the last
    invocation overwriting the first."""
    def plain(d: dict) -> dict:
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in d.items()}

    try:
        client = subprocess.run(["claude", "--version"], capture_output=True, text=True,
                                timeout=60).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        client = ""
    m = {"started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
         "runner": {"path": Path(__file__).resolve().relative_to(REPO).as_posix(),
                    "sha256": _sha256(Path(__file__).read_bytes()), "repo": identity(REPO)},
         "client": client, "args": plain(args),
         "skill": None if args.get("no_skills") else skill_loaded(args.get("plugin_dir") or ""),
         "tools": {"native": NATIVE_TOOLS, "silica": SILICA_TOOLS, "pack": PACK_NATIVE + PACK_TOOLS,
                   "pack_disallowed": PACK_DISALLOWED, "zg": ZG_TOOLS, "zg_mcp": ZG_MCP, "code_env": CODE_ENV,
                   "zg_guidance": _sha256(ZG_GUIDANCE.read_bytes()) if ZG_GUIDANCE.is_file() else None,
                   "guidance": _sha256(Path(GUIDANCE_FILE).read_bytes()) if GUIDANCE_FILE else None},
         "preamble": PREAMBLE, "forced": FORCED, "pack": PACK,
         "corpora": {str(c): identity(Path(c)) for c in sorted({str(t["cwd"]) for t in tasks})},
         "tasks": [plain(t) for t in tasks]}
    path = out / "manifest.jsonl"
    with path.open("a") as fh:
        fh.write(json.dumps(m, ensure_ascii=False) + "\n")
    return path


GUIDANCE_FILE = ""  # --guidance: appended to the system prompt of arms A and S, as arm Z gets zvec's


def command(arm: str, model: str, max_turns: int, budget: float,
            skills: bool = True, plugin_dir: str = "") -> list[str]:
    base = ["claude", "-p", "--model", model, "--max-turns", str(max_turns),
            "--max-budget-usd", str(budget), "--output-format", "stream-json", "--verbose",
            "--no-session-persistence"] + ([] if skills else ["--disable-slash-commands"])
    off = ["--settings", json.dumps({"enabledPlugins": {PLUGIN_KEY: False}})]
    if arm == "Z":
        return base + off + ["--mcp-config", ZG_MCP, "--allowedTools", ",".join(NATIVE_TOOLS + ZG_TOOLS)] + (
            ["--append-system-prompt-file", str(ZG_GUIDANCE)] if ZG_GUIDANCE.is_file() else [])
    if arm in ("A", "F", "P", "S"):
        # With --plugin-dir the installed plugin (the marketplace cache, 0.6.1 on
        # this machine on 2026-09-09) is replaced by the working tree.
        # Resolve before subprocess changes cwd to the task's repository.
        out = base + (off + ["--plugin-dir", str(Path(plugin_dir).resolve())] if plugin_dir else [])
        if arm == "P":
            return out + ["--disallowedTools", ",".join(PACK_DISALLOWED),
                          "--allowedTools", ",".join(PACK_NATIVE + PACK_TOOLS)]
        if GUIDANCE_FILE and arm in ("A", "S"):
            out += ["--append-system-prompt-file", GUIDANCE_FILE]
        return out + ["--allowedTools", ",".join(NATIVE_TOOLS + SILICA_TOOLS)]
    return base + off + ["--allowedTools", ",".join(NATIVE_TOOLS)]


def score(task: dict, answer: str) -> tuple[int | None, list[str]]:
    if not any(k in task for k in ("all_of", "any_of", "digits")):
        return None, []  # no rubric: judged afterwards
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
            timeout: int, skills: bool = True, plugin_dir: str = "", out: Path | None = None) -> dict:
    # The prompt goes on stdin: `--allowedTools` is variadic and would swallow
    # a trailing positional prompt (measured 2026-09-09: "Input must be provided").
    t0 = time.time()
    proc = subprocess.run(command(arm, model, max_turns, budget, skills, plugin_dir),
                          cwd=task["cwd"],
                          input=(FORCED if arm == "F" else PACK if arm == "P" else "")
                          + PREAMBLE + task["prompt"],
                          env={**os.environ, **CODE_ENV} if arm == "S" else None,
                          capture_output=True, text=True, timeout=timeout)
    if out is not None:  # the whole stream: every tool's input and output, for the reader of a miss
        (out / "streams").mkdir(exist_ok=True)
        (out / "streams" / f"{task['id']}-{arm}-{rep}.jsonl").write_text(proc.stdout)
    tools: collections.Counter = collections.Counter()
    # tool results flagged is_error, by tool: a call outside the allowlist
    # counts as a call and fails, and so does a call that fails the schema
    # (the 2026-09-10 grids could not tell the two apart; this can)
    errors_by: collections.Counter = collections.Counter()
    names: dict[str, str] = {}  # tool_use_id -> tool name
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
                    names[c.get("id", "")] = c["name"]
        elif d.get("type") == "user":
            content = d.get("message", {}).get("content", [])
            for c in content if isinstance(content, list) else []:
                if isinstance(c, dict) and c.get("type") == "tool_result" and c.get("is_error"):
                    errors_by[names.get(c.get("tool_use_id", ""), "?")] += 1
        elif d.get("type") == "result":
            result = d
    answer = result.get("result", "") or ""
    usage = result.get("usage", {}) or {}
    ok, missing = score(task, answer)
    # the subscription's session cap answers in place of the model (measured
    # 2026-09-10: exit 1, subtype success, "You've hit your session limit"):
    # not a run, so it is neither scored nor kept as done when resuming
    limit = answer.startswith("You've hit your session limit") or "session limit" in proc.stderr[-400:]
    if limit:
        result["subtype"], result["is_error"], ok = "limit", True, None
    return {"task": task["id"], "kind": task["kind"], "arm": arm, "rep": rep, "model": model,
            "exit": proc.returncode, "subtype": result.get("subtype"),
            "is_error": bool(result.get("is_error")),
            "cost_usd": result.get("total_cost_usd"), "duration_s": round(time.time() - t0, 1),
            "turns": result.get("num_turns"), "tools": dict(tools),
            "tool_calls": sum(tools.values()), "tool_errors": sum(errors_by.values()),
            "tool_errors_by": dict(errors_by),
            "silica_calls": sum(v for k, v in tools.items() if "silica" in k),
            "mcp_calls": sum(v for k, v in tools.items() if k.startswith("mcp__")),
            "input_tokens": usage.get("input_tokens"),
            "cache_read": usage.get("cache_read_input_tokens"),
            "cache_write": usage.get("cache_creation_input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "score": ok, "missing": missing, "answer": answer,
            "stderr_tail": proc.stderr[-400:] if proc.returncode else ""}


def summarize(out: Path) -> str:
    rows = [json.loads(l) for l in (out / "runs.jsonl").read_text().splitlines() if l.strip()]
    rows = [r for r in rows if r.get("subtype") != "limit"]  # a capped session is not a run
    by: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for r in rows:
        r.setdefault("mcp_calls", r.get("silica_calls", 0))  # rows written before arm Z
        by[(r["task"], r["arm"])].append(r)

    def mean(rs, key):
        vals = [r[key] for r in rs if isinstance(r.get(key), (int, float))]
        return statistics.mean(vals) if vals else float("nan")

    lines = ["| task | arm | n | score | cost $ | turns | tool calls | tool errors | mcp calls | "
             "context tok (in+cache) | out tok | s |", "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for task in TASKS + HARD_TASKS + SWE_TASKS:
        for arm in ("A", "S", "F", "P", "Z", "B"):
            rs = by.get((task["id"], arm), [])
            if not rs:
                continue
            ctx = statistics.mean([(r.get("input_tokens") or 0) + (r.get("cache_read") or 0)
                                   + (r.get("cache_write") or 0) for r in rs])
            lines.append(f"| {task['id']} | {arm} | {len(rs)} | {mean(rs, 'score'):.2f} | "
                         f"{mean(rs, 'cost_usd'):.3f} | {mean(rs, 'turns'):.1f} | "
                         f"{mean(rs, 'tool_calls'):.1f} | {mean(rs, 'tool_errors'):.1f} | "
                         f"{mean(rs, 'mcp_calls'):.1f} | "
                         f"{ctx:,.0f} | {mean(rs, 'output_tokens'):,.0f} | {mean(rs, 'duration_s'):.0f} |")
    lines.append("")
    for kind in ("code", "docs", "hard", "swe", "all"):
        for arm in ("A", "S", "F", "P", "Z", "B"):
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
    ap.add_argument("--arms", default="A,B", help="any of A (plugin), S (plugin, code index on), B (no plugin), "
                    "Z (no plugin, zvec-grep MCP), F (forced), P (grep then code_pack)")
    ap.add_argument("--prepare", action="store_true", help="build both sides' indexes per task root first and record the seconds")
    ap.add_argument("--guidance", default="", help="a file appended to the system prompt of arms A and S (the block silica setup claude writes)")
    ap.add_argument("--out", default="", help="results folder; default docs/baseline/<stamp>")
    ap.add_argument("--no-skills", action="store_true", help="disable every skill in both arms")
    ap.add_argument("--plugin-dir", default="", help="load the plugin from this checkout instead of the installed one")
    ap.add_argument("--set", default="base", choices=["base", "hard", "swe"], help="task set")
    ap.add_argument("--summary", default="", help="only summarise this results folder")
    a = ap.parse_args()
    if a.summary:
        print(summarize(Path(a.summary)))
        return 0
    global GUIDANCE_FILE
    GUIDANCE_FILE = str(Path(a.guidance).resolve()) if a.guidance else ""
    out = Path(a.out) if a.out else REPO / "docs" / "baseline" / time.strftime("%Y-%m-%d-%H%M")
    out.mkdir(parents=True, exist_ok=True)
    runs = out / "runs.jsonl"
    done = set()
    if runs.exists():
        for l in runs.read_text().splitlines():
            if l.strip():
                r = json.loads(l)
                if r.get("subtype") != "limit":  # a capped run is redone
                    done.add((r["task"], r["arm"], r["rep"]))
    pool_tasks = {"base": TASKS, "hard": HARD_TASKS, "swe": SWE_TASKS}[a.set]
    wanted = [t for t in pool_tasks if not a.tasks or t["id"] in a.tasks.split(",")]
    grid = [(t, arm, rep) for t in wanted for rep in range(1, a.reps + 1)
            for arm in a.arms.split(",") if (t["id"], arm, rep) not in done]
    print(f"{len(grid)} runs -> {runs}  (model {a.model}, jobs {a.jobs})", flush=True)
    if a.prepare:
        prep = {}
        for cwd in sorted({str(t["cwd"]) for t in wanted}):
            t0 = time.time()
            s = subprocess.run(["uv", "run", "--project", str(REPO), "--extra", "dense", "silica", "index", "--embed"],
                               cwd=cwd, env={**os.environ, **CODE_ENV, "SILICA_VAULT": cwd}, capture_output=True, text=True)
            t1 = time.time()
            z = subprocess.run([ZG, "index", cwd, "--embedding", "local/potion-retrieval-32m", "--mode", "direct"],
                               cwd=cwd, capture_output=True, text=True)
            prep[cwd] = {"silica_s": round(t1 - t0, 1), "silica_rc": s.returncode, "zg_s": round(time.time() - t1, 1),
                         "zg_rc": z.returncode}
            print(f"prepared {cwd}: silica {prep[cwd]['silica_s']} s, zg {prep[cwd]['zg_s']} s", flush=True)
        (out / "prepare.json").write_text(json.dumps(prep, indent=1))
    if grid:
        print(f"manifest -> {write_manifest(out, vars(a), wanted)}", flush=True)
    lock = threading.Lock()

    def job(t, arm, rep):
        try:
            r = run_one(t, arm, rep, a.model, a.max_turns, a.budget, a.timeout, not a.no_skills,
                        a.plugin_dir, out)
        except subprocess.TimeoutExpired:
            r = {"task": t["id"], "kind": t["kind"], "arm": arm, "rep": rep, "model": a.model,
                 "exit": -1, "subtype": "timeout", "is_error": True, "cost_usd": None,
                 "duration_s": a.timeout, "turns": None, "tools": {}, "tool_calls": 0,
                 "tool_errors": 0, "tool_errors_by": {}, "silica_calls": 0, "mcp_calls": 0, "input_tokens": None, "cache_read": None,
                 "cache_write": None, "output_tokens": None, "score": 0, "missing": ["timeout"],
                 "answer": "", "stderr_tail": ""}
        with lock:
            with runs.open("a") as fh:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            print(f"{r['task']} {r['arm']} rep{r['rep']}: score={r['score']} cost={r['cost_usd']} "
                  f"turns={r['turns']} tools={r['tool_calls']} mcp={r['mcp_calls']} "
                  f"{r['duration_s']}s {r['subtype'] or ''}", flush=True)

    with concurrent.futures.ThreadPoolExecutor(max_workers=a.jobs) as pool:
        list(pool.map(lambda g: job(*g), grid))
    (out / "summary.md").write_text(summarize(out) + "\n")
    print(summarize(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
