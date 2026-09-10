# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""A source file as addressable units: one per symbol codeast extracts (a
class without its methods, each method, each function, each constant), the
module-level residue between them (imports, registrations, assignments),
fixed windows where no parser applies. What the index reads for a code
file, as `_split` reads a note by its headings: the unit is what a search
ranks and a hit cites, with its symbol as the section and its lines as the
span."""
from __future__ import annotations

from silica.kernel.code.codeast.base import BARE_LANGUAGES, extract_skeleton, language_for

MAX_LINES = 120     # a unit longer than this is split ...
WINDOW_LINES = 80   # ... into windows of this many lines, under the same title
_COMMENT = ("#", "//", "/*", "*", "*/", "--", ";")


def units(rel: str, text: str) -> list[tuple[int, str, str]]:
    """(char offset, text, title) per unit, in file order. The title is the
    qualified symbol (`Class.method`, `function`, `CONSTANT`), "" for the
    module-level residue and for the windows of a file no parser covers. A
    comment block right above a symbol belongs to the symbol."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return []
    offs = [0]
    for line in lines:
        offs.append(offs[-1] + len(line))
    lang = language_for(rel)
    spans: list[tuple[int, int, str]] = []
    if lang and lang not in BARE_LANGUAGES:
        sk = extract_skeleton(text, lang, rel)
        if not sk.parse_error:
            spans = [(s.line, min(s.end_line, len(lines)), f"{s.parent}.{s.name}" if s.parent else s.name)
                     for s in sk.symbols if 0 < s.line <= s.end_line]
    # innermost owner per line: a method's lines are the method's, the rest
    # of the class the class's, what no symbol covers the module's
    owner = [""] * (len(lines) + 2)
    for a, b, title in sorted(spans, key=lambda s: (s[0], -s[1])):
        for i in range(a, b + 1):
            owner[i] = title
    runs: list[tuple[int, int, str]] = []
    i = 1
    while i <= len(lines):
        j = i
        while j < len(lines) and owner[j + 1] == owner[i]:
            j += 1
        runs.append((i, j, owner[i]))
        i = j + 1
    out: list[tuple[int, str, str]] = []
    carry = 0  # first line of a comment block waiting for the symbol below it
    for n, (a, b, title) in enumerate(runs):
        if n + 1 < len(runs) and runs[n + 1][2] != title and runs[n + 1][2] and all(
                not s or s.startswith(_COMMENT) for s in (l.strip() for l in lines[a - 1:b])):
            carry = carry or a
            continue
        a = carry or a
        carry = 0
        if not any(l.strip() for l in lines[a - 1:b]):
            continue
        step = b - a + 1 if b - a + 1 <= MAX_LINES else WINDOW_LINES
        for s in range(a, b + 1, step):
            e = min(s + step - 1, b)
            part = "".join(lines[s - 1:e])
            if part.strip():
                out.append((offs[s - 1], part, title))
    return out


def outline(rel: str, text: str) -> list[dict]:
    """{level, title, line, end_line} per symbol, a reader's table of contents."""
    lang = language_for(rel)
    if not lang or lang in BARE_LANGUAGES:
        return []
    sk = extract_skeleton(text, lang, rel)
    return [{"level": 2 if s.parent else 1, "title": f"{s.parent}.{s.name}" if s.parent else s.name,
             "line": s.line, "end_line": s.end_line} for s in sk.symbols if s.line]
