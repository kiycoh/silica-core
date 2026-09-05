# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""derive_documents — `documents:` / `code_ref` for notes that never got them.

The binding is derived from the working tree, never typed and never guessed
by a model (ADR-0038 point 3). Two lanes:

- cited: a note filed as `NNNN-*` documents every source file whose text
  cites `ADR-NNNN`. The same edge `link/ast.py` reads prose-to-ADR, walked
  the other way.
- mentioned: a note documents every repo-relative source path its body
  spells out, when the path exists in git's file universe and is source
  (another note is a link, not a binding).

`code_ref` is the commit at the note's own date (ADR-0038 point 2). HEAD
would declare a July note verified against today's code and silence /stale
on every drift that already happened.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from silica.kernel.code import codeast, codedocs, gitstate
from silica.kernel.recall import paths
from silica.kernel.write import frontmatter, templates


@dataclass(frozen=True)
class Proposal:
    note_path: str          # vault-relative
    documents: list[str]    # repo-relative, existing, source
    code_ref: str           # commit at the note's date
    lanes: tuple[str, ...]  # "cited" and/or "mentioned"


_ADR_NOTE_RE = re.compile(r"^(\d{4})-")
# Twin of `silica.kernel.link.ast.ADR_REF_RE`, spelled again here because the
# code lane may not import the link lane (import-linter: "text and code lanes
# sit low"). tests/test_derive_documents.py pins the two patterns equal.
ADR_REF_RE = re.compile(r"\bADR-(\d{4})\b")
# A path with at least one directory and an extension, not glued to other
# path characters: `src/m.py` in prose or backticks, not `a.b/c` inside a URL.
_PATH_RE = re.compile(r"(?<![\w/.-])((?:[\w.-]+/)+[\w.-]+\.[A-Za-z0-9]{1,6})(?![\w/])")
_STATUS_DATE_RE = re.compile(r"\*\*Status:\*\*[^\n]*?(\d{4}-\d{2}-\d{2})")
_DATE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _source_files(root: Path) -> list[str]:
    files = gitstate.list_files(root) or []
    return [f for f in files
            if codeast.language_for(f) is not None or f.lower().endswith(".ipynb")]


def _citations(root: Path, files: list[str]) -> dict[str, list[str]]:
    """ADR number -> source files whose text cites ADR-NNNN."""
    out: dict[str, list[str]] = {}
    for f in files:
        try:
            text = (root / f).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for num in sorted(set(ADR_REF_RE.findall(text))):
            out.setdefault(num, []).append(f)
    return out


def _note_date(data: dict, body: str, md: Path) -> str:
    """Frontmatter `created:`/`date:`, else the ADR status bullet, else mtime.
    mtime is last: it is the only one that is never wrong, only imprecise."""
    for key in ("created", "date"):
        m = _DATE_RE.search(str(data.get(key) or ""))
        if m:
            return m.group(0)
    m = _STATUS_DATE_RE.search(body)
    if m:
        return m.group(1)
    return datetime.fromtimestamp(md.stat().st_mtime, tz=timezone.utc).strftime("%Y-%m-%d")


def _iter_notes(vault: Path):
    skip = paths.ignore_matcher(vault)
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not skip(d))
        for fn in sorted(filenames):
            if fn.endswith(".md"):
                yield Path(dirpath) / fn


def propose(vault: Path | str, repo_root: Path | str | None = None,
            folder: str = "") -> list[Proposal]:
    """One Proposal per unbound note that names source, under `folder` when
    given. Idempotent: a note already carrying `documents:` is never proposed,
    whoever wrote it. The folder is the unit of consent: on the repo vault 92
    of 245 proposals were plans naming files they meant to touch (ADR-0038)."""
    vault = Path(vault)
    root = Path(repo_root) if repo_root else paths.repo_root_for(vault)
    if root is None:
        return []
    scope = folder.strip().strip("/")
    scope = f"{scope}/" if scope else ""
    files = _source_files(root)
    cited = _citations(root, files)
    known = set(files)
    out: list[Proposal] = []
    for md in _iter_notes(vault):
        rel = md.relative_to(vault).as_posix()
        if scope and not rel.startswith(scope):
            continue
        try:
            content = md.read_text(encoding="utf-8")
        except OSError:
            continue
        data, raw, body = frontmatter.split(content)
        if data is None and raw is not None:
            continue  # a block that does not parse is not ours to rewrite
        data = data or {}
        if codedocs.documents_of(data):
            continue
        lanes: list[str] = []
        docs: list[str] = []
        m = _ADR_NOTE_RE.match(md.name)
        if m and m.group(1) in cited:
            lanes.append("cited")
            docs += cited[m.group(1)]
        mentioned = [p for p in dict.fromkeys(_PATH_RE.findall(body)) if p in known]
        if mentioned:
            lanes.append("mentioned")
            docs += mentioned
        docs = list(dict.fromkeys(docs))
        if not docs:
            continue
        ref = gitstate.ref_before(root, _note_date(data, body, md))
        if not ref:
            continue
        out.append(Proposal(rel, docs, ref, tuple(lanes)))
    return out


def apply(vault: Path | str, proposals: list[Proposal]) -> int:
    """Stamp each proposal into its note; the count written. A note without
    frontmatter gets a block of exactly these two keys above its first line."""
    vault = Path(vault)
    n = 0
    for p in proposals:
        md = vault / p.note_path
        try:
            content = md.read_text(encoding="utf-8")
        except OSError:
            continue
        if content.startswith("---\n"):
            stamped = templates.stamp_documents(content, p.documents, p.code_ref)
        else:
            stamped = frontmatter.dump({"documents": list(p.documents),
                                        "code_ref": p.code_ref}, content)
        if stamped == content:
            continue
        md.write_text(stamped, encoding="utf-8")
        n += 1
    if n:
        codedocs.invalidate_snapshot(vault)
    return n
