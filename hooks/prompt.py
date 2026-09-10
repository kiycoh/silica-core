#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""UserPromptSubmit hook: one line into the context when the prompt is a
question that names no identifier, asking the model to say whether
silica_search applies before it greps. A commitment, not an order: the
tools stay optional and nothing is blocked. Measured 2026-09-10
(docs/baseline/2026-09-10-code-search.md): without it, Sonnet opened
every such question with Grep. Reads Claude Code's hook JSON on stdin,
prints the line or nothing, exits 0 whatever happens."""
import json
import re
import sys

_QUESTION = re.compile(r"\?|^\s*(what|where|how|why|which|when|who|does|do|is|are|can|list|explain|describe|find)\b", re.I)
# a backtick, snake_case, camelCase, a path or an extension, `::`, `#L`: the
# prompt names something grep can look for
_IDENTIFIER = re.compile(r"`|\b[A-Za-z]\w*_\w+\b|\b[A-Za-z]*[a-z][A-Z]\w*\b|/\w|\.\w{1,4}\b|::|#L\d")
LINE = ("silica-core: this question names no identifier. Before Grep, Read or Glob, state whether "
        "silica_search applies (yes or no); if yes, call it first with the question as `query`.")


def nudge(prompt: str) -> str | None:
    text = prompt.strip()
    if not text or not _QUESTION.search(text) or _IDENTIFIER.search(text):
        return None
    return LINE


def main() -> int:
    try:
        prompt = json.load(sys.stdin).get("prompt", "")
    except Exception:
        return 0
    line = nudge(prompt if isinstance(prompt, str) else "")
    if line:
        print(line)
    return 0


if __name__ == "__main__":
    sys.exit(main())
