# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Mechanical migrations of notes the gate wrote in an older shape (ADR-0039).

A migration is a dated rewrite that needs no model: it recognises one shape
Silica itself emitted, routes it through the writer of today, and leaves
anything else untouched. Listing is free; the write is a yes given per run
and per folder (the /stale --stamp stance, ADR-0038) and lands as one
revertible journal run.
"""
from __future__ import annotations

import hashlib
import os
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

# Only templates at import: the session hook imports this module at every
# start (ADR-0039), and ops (pydantic) plus provenance (config) cost more than
# the hook itself, 0.28 s against 0.13 s measured 2026-09-05. The walk and
# the detect need neither; propose/apply import them when called.
from silica.kernel.write.templates import (
    LEGACY_PROVENANCE_HEADER_PREFIX,
    PROVENANCE_HEADER_PREFIX,
    RELATIONS_MARKER,
    SOURCES_MARKER,
    SUPERSEDED_MARKER,
    _RELATION_BULLET_RE,
    _body_lines,
    append_under,
    patch_snippet,
)


def edited_since_write(vault: str) -> list[str]:
    # A module-level name, so a test can patch it here; the import stays lazy.
    from silica.kernel.write.undo_journal import edited_since_write as _edited
    return _edited(vault)


class Unrecognized(ValueError):
    """The note carries the shape, but a part of it has no mechanical route.
    The migration reports it and leaves the note as it is: guessing is what
    a model does, and a migration must be explainable line by line."""


@dataclass(frozen=True)
class Migration:
    name: str                        # dated, so a log line can be grepped in a year
    detect: Callable[[str], int]     # how many hits a note body carries
    apply: Callable[[str], str]      # the note after; raises Unrecognized, never guesses


@dataclass(frozen=True)
class Plan:
    path: str
    hits: int
    skip: str | None = None          # why the note is listed but not rewritten


# ---------------------------------------------------------------------------
# provenance-header-2026-09-04: `## Additional notes: <heading> (from <source>)`
# ---------------------------------------------------------------------------

_HEADER_RE = re.compile(
    rf"^(?:{re.escape(PROVENANCE_HEADER_PREFIX)}: (?P<h>.*) \(from (?P<s>[^()]+)\)"
    rf"|{re.escape(LEGACY_PROVENANCE_HEADER_PREFIX)} — (?P<hl>.*) \(da (?P<sl>[^()]+)\))\s*$"
)
# A block runs to the next block, `## Sources`, `## Superseded` or EOF. Only
# these end it: the old snippet carried its own `### facets` and, 9 times in
# 142 live blocks, its own `## Relations`, so a note's H2 cannot be the stop.
_STOPS = (SOURCES_MARKER, SUPERSEDED_MARKER)
# The old CLEANUP appended the source's link at EOF, which was inside the last
# block: 113 of 142 live blocks end with it, none carries one mid-block.
_SOURCE_LINK_RE = re.compile(r"^\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]$")


def _regions(content: str) -> list[tuple[int, int, str, str]]:
    """(start, end, heading, source) per legacy block, line-indexed on
    `content.splitlines()`; `end` is exclusive."""
    marks: list[tuple[int, str | None, str | None]] = []
    for i, line in _body_lines(content):
        m = _HEADER_RE.match(line.rstrip())
        if m:
            marks.append((i, m.group("h") or m.group("hl"), m.group("s") or m.group("sl")))
        elif line.rstrip() in _STOPS:
            marks.append((i, None, None))
    total = len(content.splitlines())
    out = []
    for k, (i, heading, source) in enumerate(marks):
        if heading is None:
            continue
        end = marks[k + 1][0] if k + 1 < len(marks) else total
        out.append((i, end, heading, source))
    return out


def _route(body: str, source: str) -> tuple[str, str, str]:
    """(prose, relations, source link) of one block's body. The old snippet
    put its typed edges under an inner `## Relations`; a block that is bullets
    and nothing else (20 of 142 live) is relations too. A trailing bare link
    to the block's own source is the old CLEANUP's, not a claim: it comes out
    and defines the footnote label. A heading inside the relations part has
    no route."""
    lines = [l for l in body.splitlines()]
    while lines and not lines[-1].strip():
        lines.pop()
    link = ""
    stem = source.removesuffix(".md")
    while lines and (m := _SOURCE_LINK_RE.match(lines[-1].strip())) and m.group(1).rsplit("/", 1)[-1] == stem:
        link = lines.pop().strip()
    cut = next((i for i, l in enumerate(lines) if l.rstrip() == RELATIONS_MARKER), len(lines))
    prose, rel = lines[:cut], lines[cut + 1:]
    if any(l.startswith("#") for l in rel):
        raise Unrecognized("a heading inside the block's relations part")
    filled = [l for l in prose if l.strip()]
    if filled and all(_RELATION_BULLET_RE.match(l) for l in filled):
        prose, rel = [], prose + rel
    return "\n".join(prose).strip("\n"), "\n".join(rel).strip("\n"), link


def _cut(lines: list[str], regions: list[tuple[int, int, str, str]]) -> str:
    """The note without its legacy blocks. Blank runs are normalised only at
    the cut points, so a fence elsewhere keeps its blank lines."""
    kept: list[str] = []
    last = 0
    for start, end, *_ in [*regions, (len(lines), len(lines))]:
        seg = lines[last:start]
        if kept:
            while seg and not seg[0].strip():
                seg.pop(0)
            if seg:
                kept.append("")
        kept.extend(seg)
        while kept and not kept[-1].strip():
            kept.pop()
        last = end
    return "\n".join(kept) + "\n"


def _provenance_header_detect(content: str) -> int:
    return len(_regions(content))


def _provenance_header_apply(content: str) -> str:
    regions = _regions(content)
    if not regions:
        return content
    lines = content.splitlines()
    blocks = [(h, s, *_route("\n".join(lines[start + 1:end]), s)) for start, end, h, s in regions]
    out = _cut(lines, regions)
    for heading, source, prose, rel, _link in blocks:
        if prose:
            out = patch_snippet(heading, prose, source, existing_content=out)
        if rel:
            out = patch_snippet(heading, rel, source, existing_content=out, relation=True)
    # What CLEANUP does with the leaf in hand (finalize._write_source_leaf):
    # the link joins `## Sources` unless the note carries it, and a label the
    # writer defined as the plain name is upgraded to the link.
    from silica.kernel.write.provenance import footnote_label

    for _h, source, _p, _r, link in blocks:
        if not link:
            continue
        if link not in out:
            out = append_under(out, SOURCES_MARKER, link, above=(SUPERSEDED_MARKER,))
        plain = f"[^{footnote_label(source)}]: {source}\n"
        if plain in out:
            out = out.replace(plain, f"[^{footnote_label(source)}]: {link}\n", 1)
    return out


PROVENANCE_HEADER = Migration(
    name="provenance-header-2026-09-04",
    detect=_provenance_header_detect,
    apply=_provenance_header_apply,
)

# ponytail: one entry, so /migrate runs it directly; the second migration
# earns the loop over this tuple and a per-name column in the listing.
MIGRATIONS = (PROVENANCE_HEADER,)


# ---------------------------------------------------------------------------
# the vault walk and the journalled write
# ---------------------------------------------------------------------------

def _iter_notes(vault: Path):
    # The same walk derive_documents uses; not imported from there because the
    # code lane sits below the write lane (import-linter contract).
    from silica.kernel.recall.paths import ignore_matcher

    skip = ignore_matcher(vault)
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not skip(d))
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield Path(dirpath) / fn


def count(vault: Path | str, migration: Migration = PROVENANCE_HEADER) -> int:
    """Notes carrying the shape: the session hook's number, a walk and a
    regex, no DRIVER and no journal."""
    n = 0
    for md in _iter_notes(Path(vault)):
        try:
            content = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        n += bool(migration.detect(content))
    return n


def propose(vault: Path | str, folder: str = "", migration: Migration = PROVENANCE_HEADER) -> list[Plan]:
    """One Plan per note carrying the shape, under `folder` when given. A
    hand-edited note and an unrecognised block are listed with the reason
    and never rewritten. Reads through DRIVER for the edit check, so `vault`
    must be the active one."""
    from silica.kernel.write.provenance import note_key

    vault = Path(vault)
    scope = folder.strip().strip("/")
    scope = f"{scope}/" if scope else ""
    edited = {note_key(p) for p in edited_since_write(str(vault))}
    out: list[Plan] = []
    for md in _iter_notes(vault):
        rel = md.relative_to(vault).as_posix()
        if scope and not rel.startswith(scope):
            continue
        try:
            content = md.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        hits = migration.detect(content)
        if not hits:
            continue
        skip = None
        if note_key(rel) in edited:
            skip = "edited by hand since the gate wrote it"
        else:
            try:
                migration.apply(content)
            except Unrecognized as e:
                skip = str(e)
        out.append(Plan(rel, hits, skip))
    return out


def apply(vault: Path | str, plans: list[Plan], migration: Migration = PROVENANCE_HEADER) -> tuple[int, str]:
    """Rewrite every unskipped plan; (notes written, journal run id). The run
    is opened on the first write, so a listing that rewrites nothing leaves
    no empty run for /revert to find."""
    from silica.driver import DRIVER
    from silica.kernel.write.ops import InverseOp, InverseOpKind
    from silica.kernel.write.undo_journal import get_undo_journal

    journal = get_undo_journal()
    run_id = ""
    n = 0
    for p in plans:
        if p.skip:
            continue
        before = DRIVER.read_note(p.path).content or ""
        try:
            after = migration.apply(before)
        except Unrecognized:
            continue
        if after == before:
            continue
        if not run_id:
            run_id = journal.start_run(source="migrate", vault=str(vault))
        DRIVER.overwrite(p.path, after)
        post = DRIVER.read_note(p.path).content or ""
        journal.record(
            run_id,
            InverseOp(kind=InverseOpKind.restore_version, path=p.path, prior_content=before),
            hashlib.sha256(post.encode("utf-8")).hexdigest(),
        )
        n += 1
    return n, run_id
