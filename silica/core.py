# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""The five tools of TOOLS.md as plain functions: files, search, read,
code_pack, write_note. The CLI and the MCP server wrap these. Nothing here
calls a model; the root is the folder Silica was started in (or --vault)."""
from __future__ import annotations

import hashlib
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import orjson
from pydantic import Field

from silica.config import CONFIG
from silica.kernel.recall import paths as _paths
from silica.kernel.recall.lexical import _tokens, get_lexical_store
from silica.kernel.recall.rerank import _query_terms, best_window_spans
from silica.tools import tool

K1, B = 1.5, 0.75
_HEADING = re.compile(r"(?m)^(?=#{1,4} )")
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_TEXT_SUFFIXES = frozenset({".md", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml", ".toml", ".xml", ".html"})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _root() -> Path:
    return Path(CONFIG.vault_path or os.getcwd()).resolve()


def _version(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()[:12]


def _error(code: str, hint: str, **extra: Any) -> dict:
    return {"error": {"code": code, "hint": hint}, **extra}


def _rel(path: str) -> str:
    """Root-relative posix path. ValueError when `path` escapes the root
    through `..` or a symlink (contain_in_vault resolves both)."""
    return _paths.contain_in_vault(path, _root())


def _read(full: Path) -> str:
    return full.read_text(encoding="utf-8", errors="replace")


def _is_text(full: Path) -> bool:
    if full.suffix.lower() in _TEXT_SUFFIXES:
        return True
    try:
        with full.open("rb") as fh:
            return b"\0" not in fh.read(4096)
    except OSError:
        return False


def _split(text: str):
    """(offset, section text) per heading-delimited section; the preamble
    before the first heading is a section too."""
    off = 0
    for part in _HEADING.split(text):
        if part.strip():
            yield off, part
        off += len(part)


# ---------------------------------------------------------------------------
# index: whole-document BM25, mtime stamps, cold/stale/ready
# ---------------------------------------------------------------------------

def _meta_path() -> Path:
    return _paths.index_dir() / "stamps.json"


def _load_meta() -> dict:
    try:
        return orjson.loads(_meta_path().read_bytes())
    except (OSError, ValueError):
        return {"built_at": None, "stamps": {}}


def _failures() -> dict[str, str]:
    try:
        return orjson.loads((_paths.index_dir() / "failures.json").read_bytes())
    except (OSError, ValueError):
        return {}


def _walk(base: Path | None = None):
    """(rel, Path) for every regular file under the root, plus the excluded
    directories as (rel + "/", None). Hidden and ignored dirs are not entered."""
    root = _root()
    ignore = _paths.ignore_matcher(root)
    for dirpath, dirnames, filenames in os.walk(base or root):
        keep = []
        for d in sorted(dirnames):
            if d.startswith(".") or ignore(d):
                yield (Path(dirpath) / d).relative_to(root).as_posix() + "/", None
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            if not name.startswith("."):
                full = Path(dirpath) / name
                yield full.relative_to(root).as_posix(), full


def _note_paths() -> list[str]:
    return sorted(rel for rel, full in _walk() if full is not None and rel.endswith(".md"))


def _mtime(rel: str) -> float | None:
    try:
        return (_root() / rel).stat().st_mtime
    except OSError:
        return None


def index_state(meta: dict | None = None) -> dict:
    """{state: cold|stale|ready, docs, built_at}. `cold` = never built."""
    if not _paths.index_file("lexical").is_file() or not _meta_path().is_file():
        return {"state": "cold", "docs": 0, "built_at": None}
    meta = meta or _load_meta()
    stamps = meta.get("stamps", {})
    live = _note_paths()
    stale = any(stamps.get(r) != _mtime(r) for r in live) or bool(set(stamps) - set(live))
    return {"state": "stale" if stale else "ready", "docs": len(stamps), "built_at": meta.get("built_at")}


def build_index(rebuild: bool = False) -> dict:
    """Build or refresh the document index. Incremental by mtime; `rebuild`
    re-reads everything. Never raises for one unreadable file: it lands in
    `failed` and in the failures file `silica_files` reports from."""
    rebuild = rebuild or not _meta_path().is_file()
    store = get_lexical_store()
    meta = {"built_at": None, "stamps": {}} if rebuild else _load_meta()
    stamps: dict[str, float] = meta["stamps"]
    root = _root()
    live = _note_paths()
    changed, failed = 0, {}
    for rel in live:
        mt = _mtime(rel)
        if mt is None:
            continue
        if not rebuild and stamps.get(rel) == mt:
            continue
        try:
            store.upsert(rel, Path(rel).stem, _read(root / rel))
        except OSError as e:
            failed[rel] = str(e)
            continue
        stamps[rel] = mt
        changed += 1
    live_set = set(live)
    for gone in [p for p in list(stamps) if p not in live_set]:
        stamps.pop(gone, None)
    for gone in [p for p in store.paths() if p not in live_set]:
        store.remove(gone)
    store.save()
    meta["built_at"] = time.time()
    _paths.atomic_write_bytes(_meta_path(), orjson.dumps(meta))
    _paths.atomic_write_bytes(_paths.index_dir() / "failures.json", orjson.dumps(failed))
    return {"docs": len(stamps), "changed": changed, "failed": failed, "index": index_state(meta)}


# ---------------------------------------------------------------------------
# 1. files
# ---------------------------------------------------------------------------

@tool(cls="atomic")
def silica_files(
    folder: Annotated[str, Field(description="Root-relative folder to list; empty = the whole root")] = "",
    status: Annotated[str, Field(description="Only this status: indexed, changed, excluded, failed, unconverted")] = "",
    limit: Annotated[int, Field(description="Max entries per page")] = 200,
    cursor: Annotated[str, Field(description="`next_cursor` from the previous page")] = "",
) -> dict:
    """Inventory of the root and what the index did with each file: `indexed`
    (in the index, matches disk), `changed` (differs from the index),
    `excluded` (an ignore rule or a type the index does not read; `reason`
    says which), `failed` (read or conversion error), `unconverted` (a PDF or
    office file with no extracted `.md` beside it). `index.state` is `cold`
    when nothing was ever indexed: an empty listing there is not "no files"."""
    from silica.sources.convert import DOC_EXTS, IMG_EXTS

    root = _root()
    try:
        scope = _rel(folder) if folder.strip() else ""
    except ValueError as e:
        return _error("out_of_root", str(e))
    base = root / scope if scope else root
    if not base.is_dir():
        return _error("not_found", f"{scope or '.'} is not a folder under {root}")
    meta = _load_meta()
    stamps = meta.get("stamps", {})
    failed = _failures()
    entries: list[dict] = []
    for rel, full in _walk(base):
        if full is None:
            entries.append({"path": rel, "status": "excluded",
                            "reason": "hidden" if rel.rstrip("/").rsplit("/", 1)[-1].startswith(".") else "ignore rule"})
            continue
        try:
            st = full.stat()
        except OSError:
            continue
        row: dict = {"path": rel, "bytes": st.st_size, "mtime": st.st_mtime}
        suffix = full.suffix.lower()
        if rel in failed:
            row.update(status="failed", reason=failed[rel])
        elif suffix == ".md":
            row["status"] = "indexed" if stamps.get(rel) == st.st_mtime else "changed"
        elif suffix in DOC_EXTS and suffix not in IMG_EXTS:
            sidecar = full.with_suffix(".md")
            if sidecar.is_file():
                row.update(status="excluded", reason=f"converted: {sidecar.relative_to(root).as_posix()}")
            else:
                row["status"] = "unconverted"
        else:
            row.update(status="excluded", reason=f"not indexed: {suffix or 'no extension'}")
        entries.append(row)
    counts = Counter(e["status"] for e in entries)
    if status:
        entries = [e for e in entries if e["status"] == status]
    try:
        offset = int(cursor) if cursor else 0
    except ValueError:
        return _error("bad_argument", "cursor must be the next_cursor of a previous reply")
    page = entries[offset:offset + limit]
    truncated = offset + limit < len(entries)
    return {"root": str(root), "total": len(entries), "files": page,
            "counts": {k: counts.get(k, 0) for k in ("indexed", "changed", "excluded", "failed", "unconverted")},
            "truncated": truncated, "next_cursor": str(offset + limit) if truncated else None,
            "index": index_state(meta)}


# ---------------------------------------------------------------------------
# 2. search
# ---------------------------------------------------------------------------

_section_cache: dict[tuple[str, float], list[tuple[int, str, Counter, int]]] = {}


def _sections_of(rel: str) -> tuple[str, list[tuple[int, str, Counter, int]]]:
    """Text and (offset, section, term counts, length) per section, cached by
    mtime so a second query over the same top documents does not retokenize."""
    full = _root() / rel
    text = _read(full)
    key = (rel, full.stat().st_mtime)
    if key not in _section_cache:
        secs = []
        for off, part in _split(text):
            tf = Counter(_tokens(part))
            secs.append((off, part, tf, sum(tf.values())))
        if len(_section_cache) > 64:
            _section_cache.clear()
        _section_cache[key] = secs
    return text, _section_cache[key]


@tool(cls="atomic")
def silica_search(
    query: Annotated[str, Field(description="Words to look for; the corpus vocabulary, not a question")],
    folder: Annotated[str, Field(description="Only documents under this root-relative folder")] = "",
    k: Annotated[int, Field(description="Max hits")] = 5,
    per_doc: Annotated[int, Field(description="Max sections per document")] = 2,
    width: Annotated[int, Field(description="Characters in each hit's window")] = 600,
    hybrid: Annotated[bool, Field(description="Add the dense-embedding leg (embeddings extension required)")] = False,
) -> dict:
    """Ranked passages with an honest zero. BM25 over documents, then over
    the heading sections of the top documents, at most `per_doc` per
    document; each hit carries its densest `width`-char window and line.
    `score` is raw BM25 (comparable within one call only). `matched_terms`
    are the query terms in the hit; `coverage` is the share of the query's
    idf mass they carry (near 1 = every rare term matched, near 0 = only the
    common words); `terms_absent` are query terms found nowhere in the
    corpus. No boolean "no answer" exists: read coverage and matched_terms
    and decide to stop or rephrase. Builds the index on first use."""
    if hybrid:
        return _error("bad_argument", "the embeddings extension is not installed in this build")
    state = index_state()
    if state["state"] != "ready":
        build_index(rebuild=state["state"] == "cold")
    store = get_lexical_store()
    idf = store.idf(set(_tokens(query)))
    known = {t: v for t, v in idf.items() if v is not None}
    mass = sum(known.values()) or 1.0
    docs = store.bm25(query)
    if folder.strip():
        try:
            scope = _rel(folder)
        except ValueError as e:
            return _error("out_of_root", str(e))
        docs = [d for d in docs if _paths.in_folder(d[0], scope)]
    top = docs[: max(k * 3, 10)]
    weights = store.query_idf(_query_terms(query))
    secs: list[tuple[str, int, str, Counter, int]] = []
    texts: dict[str, str] = {}
    for path, _score, _matched in top:
        try:
            texts[path], parts = _sections_of(path)
        except OSError:
            continue
        secs.extend((path, off, part, tf, dl) for off, part, tf, dl in parts)
    avg = (sum(s[4] for s in secs) / len(secs)) if secs else 1.0
    scored = []
    for path, off, part, tf, dl in secs:
        matched = {t for t in known if tf.get(t)}
        if not matched:
            continue
        score = sum(known[t] * (tf[t] * (K1 + 1)) / (tf[t] + K1 * (1 - B + B * dl / avg)) for t in matched)
        scored.append((score, path, off, part, matched))
    scored.sort(key=lambda s: (-s[0], s[1], s[2]))
    hits: list[dict] = []
    seen: dict[str, int] = {}
    for score, path, off, part, matched in scored:
        if seen.get(path, 0) >= per_doc:
            continue
        seen[path] = seen.get(path, 0) + 1
        o, window = best_window_spans(part, query, width, 1, weights, snap=True)[0]
        title = part.splitlines()[0].lstrip("#").strip() if part.startswith("#") else ""
        hits.append({"path": path, "section": title,
                     "line": texts[path].count("\n", 0, off + o) + 1,
                     "score": round(score, 2), "matched_terms": sorted(matched),
                     "coverage": round(sum(known[t] for t in matched) / mass, 2),
                     "window": window})
        if len(hits) == k:
            break
    return {"root": str(_root()), "query": query, "hits": hits,
            "documents": [{"path": p, "score": round(sc, 2), "matched_terms": sorted(m)} for p, sc, m in top],
            "candidates": len(docs),
            "terms_absent": sorted(t for t, v in idf.items() if v is None),
            "index": index_state()}


# ---------------------------------------------------------------------------
# 3. read
# ---------------------------------------------------------------------------

@tool(cls="atomic")
def silica_read(
    path: Annotated[str, Field(description="Root-relative file path (from silica_files or a search hit)")],
    start: Annotated[int, Field(description="First line, 1-based")] = 1,
    end: Annotated[int, Field(description="Last line; 0 = to the end, under max_chars")] = 0,
    section: Annotated[str, Field(description="Serve this heading's section instead (exact title or unique prefix)")] = "",
    max_chars: Annotated[int, Field(description="Cap on the served text")] = 16000,
    expect_version: Annotated[str, Field(description="Refuse with error.changed when the file's version differs")] = "",
) -> dict:
    """A located slice of one file with its outline. Lines `start..end`, or
    one `section` by heading, or the whole file when it fits `max_chars`;
    `truncated` and `next_start` say how to continue. A PDF or office file
    is served from the `.md` extracted beside it (`source` names the
    original); without one the reply is `error.unconverted`. Carry
    `version` forward as `expect_version` to be refused instead of reading
    different bytes under an old citation."""
    from silica.kernel.link.ast import parse_headings

    root = _root()
    try:
        rel = _rel(path)
    except ValueError as e:
        return _error("out_of_root", str(e))
    full = root / rel
    if not full.is_file():
        return _error("not_found", f"{rel} is not a file under {root}")
    source = None
    if not _is_text(full):
        sidecar = full.with_suffix(".md")
        if not sidecar.is_file():
            return _error("unconverted", f"no extracted text beside {rel}; run `silica import {rel}`")
        source, full = rel, sidecar
        rel = sidecar.relative_to(root).as_posix()
    text = _read(full)
    version = _version(text)
    if expect_version and expect_version != version:
        return _error("changed", f"{rel} is now version {version}", version=version)
    lines = text.splitlines()
    outline = [{"level": h["level"], "title": h["text"], "line": text.count("\n", 0, h["pos"]) + 1}
               for h in parse_headings(text)]
    if section.strip():
        want = section.strip().casefold()
        idx = [i for i, h in enumerate(outline) if h["title"].casefold() == want] or \
              [i for i, h in enumerate(outline) if h["title"].casefold().startswith(want)]
        if not idx:
            return _error("not_found", f"no heading matches {section!r}", outline=outline)
        if len(idx) > 1:
            return _error("bad_argument", f"{len(idx)} headings match {section!r}; be exact", outline=outline)
        h = outline[idx[0]]
        start = h["line"]
        end = next((o["line"] - 1 for o in outline[idx[0] + 1:] if o["level"] <= h["level"]), len(lines))
    start = max(1, start)
    end = len(lines) if end <= 0 else min(end, len(lines))
    out: list[str] = []
    size, truncated = 0, False
    for line in lines[start - 1:end]:
        if size + len(line) + 1 > max_chars:
            truncated = True
            break
        out.append(line)
        size += len(line) + 1
    served_end = start + len(out) - 1 if out else start - 1
    return {"root": str(root), "path": rel, "version": version, "start": start, "end": served_end,
            "text": "\n".join(out), "truncated": truncated,
            "next_start": served_end + 1 if truncated else None,
            "outline": outline, "source": source, "page_map": None}


# ---------------------------------------------------------------------------
# 4. code_pack
# ---------------------------------------------------------------------------

@tool(cls="atomic")
def silica_code_pack(
    target: Annotated[str, Field(description="Root-relative source path, optionally narrowed with '#Class' or '#Class.member'")],
    budget_chars: Annotated[int, Field(description="Character budget for the whole pack; the target is always served")] = 24000,
    sections: Annotated[list[str] | None, Field(description="Sections besides the target: any of 'hierarchy', 'neighborhood', 'external', 'importers'. Empty = all")] = None,
) -> dict:
    """Deterministic context pack for one source file inside a character
    budget: the target plus its supertypes, extenders, the signatures it
    names, external dependencies and importers. Static AST only, same repo
    state, same bytes. Check `truncated` before treating the target as
    complete; `dropped` names what did not fit. `languages` lists the
    parsers this install has."""
    from silica.kernel.code import codepack
    from silica.kernel.code.codeast import EXTENSION_MAP

    try:
        pack = codepack.code_pack(str(_root()), target, budget_chars, sections=sections or None)
    except (ValueError, OSError) as e:
        return _error("bad_argument", str(e))
    return {"root": str(_root()), **pack, "languages": sorted(set(EXTENSION_MAP.values()))}


# ---------------------------------------------------------------------------
# 5. write_note
# ---------------------------------------------------------------------------

def _name_map() -> dict[str, str]:
    """Wikilink resolution the way Obsidian does it: by note stem (case-
    insensitive), or by root-relative path with or without `.md`."""
    out: dict[str, str] = {}
    for rel in _note_paths():
        out.setdefault(Path(rel).stem.casefold(), rel)
        out[rel.casefold()] = rel
        out[rel[:-3].casefold()] = rel
    return out


def _lint(rel: str, content: str) -> list[dict]:
    from silica.kernel.link.ast import _balanced

    out = [{"kind": "structure", "target": v, "line": 0} for v in _balanced(content)]
    names = _name_map()
    for m in _WIKILINK.finditer(content):
        target = m.group(1).strip()
        if target.casefold() not in names:
            out.append({"kind": "unresolved_link", "target": target,
                        "line": content.count("\n", 0, m.start()) + 1})
    return out


@tool(cls="atomic")
def silica_write_note(
    path: Annotated[str, Field(description="Root-relative path of the note (.md added when missing)")],
    body: Annotated[str, Field(description="Markdown body, written as given")],
    frontmatter: Annotated[dict[str, Any] | None, Field(description="Optional YAML frontmatter, serialised on top of the body")] = None,
    expect_version: Annotated[str, Field(description="Refuse with error.changed unless the existing file has this version")] = "",
    create_only: Annotated[bool, Field(description="Refuse when the file already exists")] = False,
) -> dict:
    """Write one note atomically, as given, then lint it: `lint` lists
    structural violations and unresolved wikilinks. Refuses to overwrite
    when `expect_version` does not match or when `create_only` is set and
    the file exists. Undo is git; the core keeps no journal."""
    import yaml

    root = _root()
    try:
        rel = _rel(path if path.lower().endswith(".md") else path + ".md")
    except ValueError as e:
        return _error("out_of_root", str(e))
    full = root / rel
    exists = full.is_file()
    if exists and create_only:
        return _error("bad_argument", f"{rel} exists and create_only is set")
    if exists:
        current = _version(_read(full))
        if expect_version and expect_version != current:
            return _error("changed", f"{rel} is now version {current}", version=current)
    content = body
    if frontmatter:
        content = "---\n" + yaml.safe_dump(frontmatter, allow_unicode=True, sort_keys=False) + "---\n" + body
    try:
        full.parent.mkdir(parents=True, exist_ok=True)
        _paths.atomic_write_bytes(full, content.encode("utf-8"))
    except OSError as e:
        return _error("bad_argument", str(e))
    written = _read(full)
    return {"root": str(root), "path": rel, "version": _version(written), "created": not exists,
            "lint": _lint(rel, written)}


FUNCTIONS = (silica_files, silica_search, silica_read, silica_code_pack, silica_write_note)
files, search, read, code_pack, write_note = FUNCTIONS
