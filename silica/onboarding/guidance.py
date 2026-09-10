# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""The block `silica setup claude` writes into Claude Code's user-level
CLAUDE.md, between markers so a second run replaces it and nothing else
on the page is touched: when to search before grep, what to do with a
hit. The same text the harness appends to the system prompt when it
measures the contract (scripts/baseline.py --guidance)."""
from __future__ import annotations

import os
from pathlib import Path

START = "<!-- SILICA_START -->"
END = "<!-- SILICA_END -->"
GUIDANCE = """## silica-core

- For a question about what the files in the current folder say, or how a behaviour is implemented, when no identifier is known: call `silica_search` with the question before Grep, Read or Glob. It ranks passages and, with the code index on, the functions, methods, classes and constants themselves, by the question's words and their vectors; a hit's `section` is the symbol, `span` its lines.
- Grep for an exact string or a symbol name you already know; read a file directly when its path and section are already known.
- Cite a hit's passage by path and line when it answers; read with `silica_read(path, section=…)` only for missing context, carrying the hit's `version` as `expect_version`.
- `terms_absent` and `coverage` are lexical diagnostics, not verdicts: a dense hit can answer with coverage 0. If the passages give no evidence, rephrase once keeping names and identifiers; then report what remains unfound."""


def claude_md_path() -> Path:
    """Claude Code's user-level CLAUDE.md: CLAUDE_CONFIG_DIR when set, else ~/.claude."""
    return Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude") / "CLAUDE.md"


def block() -> str:
    return f"{START}\n{GUIDANCE}\n{END}\n"


def write_guidance(path: Path) -> str:
    """Put the block at `path`: replaced between the markers when present,
    appended after a blank line otherwise, the file created when missing.
    Returns 'replaced', 'appended' or 'created'."""
    if not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(block(), encoding="utf-8")
        return "created"
    text = path.read_text(encoding="utf-8")
    i, j = text.find(START), text.find(END)
    if i != -1 and j > i:
        path.write_text(text[:i] + block().rstrip("\n") + text[j + len(END):], encoding="utf-8")
        return "replaced"
    path.write_text(text.rstrip("\n") + "\n\n" + block(), encoding="utf-8")
    return "appended"
