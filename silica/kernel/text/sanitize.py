# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Text hygiene applied to converted sources before they become notes."""

import re

# Excludes markdown-structural chars (# = - * _ ` ~): they legitimately repeat
# — ATX headings up to ######, thematic breaks / setext underlines, emphasis,
# code fences — and collapsing them corrupts real document structure (the golden
# integrity probe caught `##### Heading` → `# Heading` on nucleate).
# Also excludes digits (\d): they are data, not garbage — "100000" is a number,
# not a degenerate run, and collapsing it silently corrupts the value.
_DEGENERATE_RUN_RE = re.compile(r'([^\n\d#*_=~`-])\1{4,}')


def strip_degenerate_runs(text: str) -> str:
    """Collapse runs of 5+ identical characters to a single instance.

    Lines are preserved; only in-line repetitions are collapsed.
    """
    return _DEGENERATE_RUN_RE.sub(r'\1', text)
