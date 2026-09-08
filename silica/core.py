# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""The five tools of TOOLS.md as plain functions: files, search, read,
code_pack, write_note. The CLI and the MCP server wrap these. Nothing here
calls a model; the root is the folder Silica was started in (or --vault)."""
from __future__ import annotations

import hashlib
import math
import os
import re
import time
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import orjson
import yaml
from pydantic import Field

from silica.config import CONFIG
from silica.kernel.link.ast import parse_headings
from silica.kernel.recall import paths as _paths
from silica.kernel.recall.lexical import TOKENIZER_VERSION, _tokens, get_lexical_store
from silica.kernel.recall.rerank import _query_terms, best_window_spans
from silica.tools import tool

K1, B = 1.5, 0.75
_HEADING = re.compile(r"(?m)^(?=#{1,4} )")
_PAGE = re.compile(r"p\.?\s*(\d+)", re.I)
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


def _extract_cache(rel: str) -> Path:
    return _paths.index_dir() / "extract" / f"{hashlib.sha1(rel.encode()).hexdigest()}.txt"


def _pdf_text(rel: str, full: Path) -> tuple[str, list[dict], str]:
    """A PDF's own text layer, the page map, and the reason the layer is empty
    when it is. Cached beside the index (`.txt` with the text, `.json` with the
    map) and re-extracted whenever the PDF is newer than the cache.

    The page is the section: search ranks pages, `silica_read` serves one by
    `p. N`, `page_map` carries a slice back to its pages. The map is kept
    beside the text rather than marked inside it — a `## p. N` marker splits
    the pages of any paper that prints markdown (olmOCR does), and a form feed
    is a line boundary to `str.splitlines`, which would shift every line
    number the tool reports.

    ponytail: the text layer as PDFium hands it over — no OCR, no column
    reordering, no table reconstruction. A scan carries no text layer, stays
    `unconverted`, and `silica import` (mineru, docling) is the upgrade path.
    """
    cache = _extract_cache(rel)
    pages_file = cache.with_suffix(".json")
    try:
        if cache.stat().st_mtime >= full.stat().st_mtime:
            text = cache.read_text(encoding="utf-8", errors="replace")
            return text, orjson.loads(pages_file.read_bytes()), "" if text else "no text layer"
    except (OSError, ValueError):
        pass
    try:
        import pypdfium2 as pdfium
    except ImportError:  # pragma: no cover - a base dependency
        return "", [], "pypdfium2 not installed"
    try:
        pdf = pdfium.PdfDocument(str(full))
        try:
            bodies = []
            for i in range(len(pdf)):
                page = pdf[i]
                textpage = page.get_textpage()
                try:
                    bodies.append(textpage.get_text_range().strip())
                finally:
                    textpage.close()
                    page.close()
        finally:
            pdf.close()
    except Exception as e:  # encrypted, truncated, not a PDF: a status, not a crash
        return "", [], f"unreadable: {e}"
    parts: list[str] = []
    pages: list[dict] = []
    line, offset = 1, 0
    for n, body in enumerate(bodies, 1):
        chunk = body + "\n\n"
        pages.append({"page": n, "line": line, "offset": offset})
        parts.append(chunk)
        line += chunk.count("\n")
        offset += len(chunk)
    text = "".join(parts) if any(bodies) else ""
    cache.parent.mkdir(parents=True, exist_ok=True)
    _paths.atomic_write_bytes(cache, text.encode("utf-8"))
    _paths.atomic_write_bytes(pages_file, orjson.dumps(pages if text else []))
    return text, (pages if text else []), "" if text else "no text layer"


def _doc_text(rel: str) -> str:
    """What the index reads for one document: a note's bytes, a PDF's text layer."""
    full = _root() / rel
    return _pdf_text(rel, full)[0] if full.suffix.lower() == ".pdf" else _read(full)


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
    """The stamps file; an index built by another tokenizer reads as never built."""
    try:
        meta = orjson.loads(_meta_path().read_bytes())
    except (OSError, ValueError):
        meta = {}
    if meta.get("tokenizer") != TOKENIZER_VERSION:
        return {"built_at": None, "stamps": {}, "tokenizer": TOKENIZER_VERSION}
    return meta


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
    """Every document the index reads: notes, and PDFs with no extracted `.md`
    beside them — that sidecar is the note, and indexing both would double it."""
    out = []
    for rel, full in _walk():
        if full is None:
            continue
        if rel.endswith(".md") or (full.suffix.lower() == ".pdf" and not full.with_suffix(".md").is_file()):
            out.append(rel)
    return sorted(out)


def _mtime(rel: str) -> float | None:
    try:
        return (_root() / rel).stat().st_mtime
    except OSError:
        return None


def index_state(meta: dict | None = None) -> dict:
    """{state: cold|stale|ready, docs, built_at}. `cold` = never built."""
    meta = meta or _load_meta()
    if not _paths.index_file("lexical").is_file() or not meta.get("built_at"):
        return {"state": "cold", "docs": 0, "built_at": None}
    stamps = meta.get("stamps", {})
    skipped = meta.get("no_text", {})
    live = _note_paths()
    stale = any(stamps.get(r) != _mtime(r) and skipped.get(r, {}).get("mtime") != _mtime(r)
                for r in live) or bool(set(stamps) - set(live))
    return {"state": "stale" if stale else "ready", "docs": len(stamps), "built_at": meta.get("built_at")}


def build_index(rebuild: bool = False, embed: bool = False) -> dict:
    """Build or refresh the document index. Incremental by mtime; `rebuild`
    re-reads everything. Never raises for one unreadable file: it lands in
    `failed` and in the failures file `silica_files` reports from."""
    meta = _load_meta()
    rebuild = rebuild or not meta.get("built_at")
    if rebuild:
        meta = {"built_at": None, "stamps": {}, "no_text": {}, "tokenizer": TOKENIZER_VERSION}
    store = get_lexical_store()
    stamps: dict[str, float] = meta["stamps"]
    skipped: dict[str, dict] = meta.setdefault("no_text", {})
    root = _root()
    live = _note_paths()
    changed, failed = 0, {}
    for rel in live:
        mt = _mtime(rel)
        if mt is None:
            continue
        if not rebuild and (stamps.get(rel) == mt or skipped.get(rel, {}).get("mtime") == mt):
            continue
        if rel.lower().endswith(".pdf"):
            text, _pages, reason = _pdf_text(rel, root / rel)
            if not text:  # a scan, or a PDF PDFium could not open: `unconverted`, never indexed
                skipped[rel] = {"mtime": mt, "reason": reason}
                if stamps.pop(rel, None) is not None:
                    store.remove(rel)
                continue
        else:
            try:
                text = _read(root / rel)
            except OSError as e:
                failed[rel] = str(e)
                continue
        store.upsert(rel, Path(rel).stem, text)
        stamps[rel] = mt
        skipped.pop(rel, None)
        changed += 1
    live_set = set(live)
    for gone in [p for p in list(stamps) + list(skipped) if p not in live_set]:
        stamps.pop(gone, None)
        skipped.pop(gone, None)
        _extract_cache(gone).unlink(missing_ok=True)  # a deleted PDF leaves no extraction behind
    for gone in [p for p in store.paths() if p not in live_set]:
        store.remove(gone)
    store.save()
    meta["built_at"] = time.time()
    _paths.atomic_write_bytes(_meta_path(), orjson.dumps(meta))
    _paths.atomic_write_bytes(_paths.index_dir() / "failures.json", orjson.dumps(failed))
    out = {"docs": len(stamps), "changed": changed, "failed": failed, "index": index_state(meta)}
    if embed:
        from silica import embeddings
        if not embeddings.enabled():
            return _error("bad_argument", "embed needs SILICA_EMBEDDING_BASE_URL", **out)
        notes = [(rel, stamps[rel], _doc_text(rel)) for rel in live if rel in stamps]
        try:
            out["embeddings"] = embeddings.build(notes, rebuild=rebuild)
        except Exception as e:
            return _error("bad_argument", f"embeddings endpoint failed: {e}", **out)
    return out


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
    says which), `failed` (read or conversion error), `unconverted` (a PDF
    whose text layer the index could not read, or an office file with no
    extracted `.md` beside it; `reason` says which). A PDF is indexed from
    its own text layer, one section per page, unless a `.md` sits beside it —
    that sidecar is indexed instead. `index.state` is `cold` when nothing was
    ever indexed: an empty listing there is not "no files"."""
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
    skipped = meta.get("no_text", {})
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
            skip = skipped.get(rel, {})
            if sidecar.is_file():
                row.update(status="excluded", reason=f"converted: {sidecar.relative_to(root).as_posix()}")
            elif suffix != ".pdf":
                row["status"] = "unconverted"
            elif stamps.get(rel) == st.st_mtime:
                row["status"] = "indexed"
            elif skip.get("mtime") == st.st_mtime:
                # the index read this PDF and found no text to index; `reason` says why
                row.update(status="unconverted", reason=skip.get("reason") or "no text layer")
            else:
                row["status"] = "changed"
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

_section_cache: dict[tuple[str, float], tuple[str, list[tuple[int, str, Counter, int, str]]]] = {}


def _sections_of(rel: str) -> tuple[str, list[tuple[int, str, Counter, int, str]]]:
    """Text and (offset, section, term counts, length, title) per section — a
    heading section in a note, a page in a PDF — cached by mtime so a second
    query over the same top documents does not retokenize (nor re-extract)."""
    full = _root() / rel
    key = (rel, full.stat().st_mtime)
    if key not in _section_cache:
        if full.suffix.lower() == ".pdf":
            text, pages, _reason = _pdf_text(rel, full)
            bounds = [p["offset"] for p in pages] + [len(text)]
            parts = [(p["offset"], text[p["offset"]:bounds[i + 1]], f"p. {p['page']}")
                     for i, p in enumerate(pages)]
        else:
            text = _read(full)
            parts = [(off, part, part.splitlines()[0].lstrip("#").strip() if part.startswith("#") else "")
                     for off, part in _split(text)]
        secs = []
        for off, part, title in parts:
            tf = Counter(_tokens(part))
            secs.append((off, part, tf, sum(tf.values()), title))
        if len(_section_cache) > 64:
            _section_cache.clear()
        _section_cache[key] = (text, secs)
    return _section_cache[key]


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
    idf mass they carry, absent terms included at the weight of a term found
    nowhere (near 1 = every rare term matched, near 0 = only the common
    words); `terms_absent` are query terms found nowhere in the corpus. No
    boolean "no answer" exists: read coverage and matched_terms
    and decide to stop or rephrase. Builds the index on first use."""
    if hybrid:
        from silica import embeddings
        if not embeddings.enabled():
            return _error("bad_argument", "hybrid needs SILICA_EMBEDDING_BASE_URL (an OpenAI-compatible /v1/embeddings endpoint)")
        if not embeddings.load_store()["vectors"]:
            return _error("index_cold", "no document vectors yet: run `silica index --embed`")
    state = index_state()
    if state["state"] != "ready":
        build_index(rebuild=state["state"] == "cold")
    store = get_lexical_store()
    idf = store.idf(set(_tokens(query)))
    known = {t: v for t, v in idf.items() if v is not None}
    absent = sorted(t for t, v in idf.items() if v is None)
    # A term found nowhere weighs what BM25 gives a term at df=0, the heaviest
    # in the corpus, so a query whose rare words are all absent reads low even
    # when its one surviving word matches: measured 2026-09-08, "kubernetes
    # ingress controller nginx annotations" read coverage 1.0 on "annotations".
    idf_absent = math.log(1 + (len(store) + 0.5) / 0.5)
    mass = (sum(known.values()) + idf_absent * len(absent)) or 1.0
    docs = store.bm25(query)
    if folder.strip():
        try:
            scope = _rel(folder)
        except ValueError as e:
            return _error("out_of_root", str(e))
        docs = [d for d in docs if _paths.in_folder(d[0], scope)]
    top = docs[: max(k * 3, 10)]
    dense: dict[str, float] = {}
    if hybrid:
        from silica import embeddings
        try:
            dense = dict(embeddings.dense_candidates(query, k=max(k * 3, 10)))
        except Exception as e:  # the endpoint is the extension's business, the reply says so
            return _error("bad_argument", f"embeddings endpoint failed: {e}")
        if folder.strip():
            dense = {p: c for p, c in dense.items() if _paths.in_folder(p, scope)}
        # reciprocal-rank fusion of the two document rankings; a dense-only
        # document enters `top` with no matched terms and scores 0 lexically
        fused: dict[str, float] = {}
        for rank, (p, _s, _m) in enumerate(top):
            fused[p] = fused.get(p, 0.0) + 1.0 / (60 + rank + 1)
        for rank, p in enumerate(dense):
            fused[p] = fused.get(p, 0.0) + 1.0 / (60 + rank + 1)
        lex = {p: (sc, m) for p, sc, m in docs}
        top = [(p, lex.get(p, (0.0, set()))[0], lex.get(p, (0.0, set()))[1])
               for p, _f in sorted(fused.items(), key=lambda kv: -kv[1])][: max(k * 3, 10)]
    weights = store.query_idf(_query_terms(query))
    secs: list[tuple[str, int, str, Counter, int, str]] = []
    texts: dict[str, str] = {}
    for path, _score, _matched in top:
        try:
            texts[path], parts = _sections_of(path)
        except OSError:
            continue
        secs.extend((path, off, part, tf, dl, title) for off, part, tf, dl, title in parts)
    avg = (sum(s[4] for s in secs) / len(secs)) if secs else 1.0
    scored = []
    for path, off, part, tf, dl, title in secs:
        matched = {t for t in known if tf.get(t)}
        if not matched:
            continue
        score = sum(known[t] * (tf[t] * (K1 + 1)) / (tf[t] + K1 * (1 - B + B * dl / avg)) for t in matched)
        scored.append((score, path, off, part, matched, title))
    scored.sort(key=lambda s: (-s[0], s[1], s[2]))
    for p, cos in dense.items():
        if p in texts and not any(sp == p for _sc, sp, _o, _pt, _m, _t in scored):
            first = next(iter(_sections_of(p)[1]), None)
            if first is not None:
                scored.append((0.0, p, first[0], first[1], set(), first[4]))
    hits: list[dict] = []
    seen: dict[str, int] = {}
    for score, path, off, part, matched, title in scored:
        if seen.get(path, 0) >= per_doc:
            continue
        seen[path] = seen.get(path, 0) + 1
        o, window = best_window_spans(part, query, width, 1, weights, snap=True)[0]
        hit = {"path": path, "section": title,
               "line": texts[path].count("\n", 0, off + o) + 1,
               "score": round(score, 2), "matched_terms": sorted(matched),
               "coverage": round(sum(known[t] for t in matched) / mass, 2),
               "window": window}
        if path in dense:
            hit["dense"] = round(dense[path], 3)
        hits.append(hit)
        if len(hits) == k:
            break
    return {"root": str(_root()), "query": query, "hits": hits,
            "documents": [{"path": p, "score": round(sc, 2), "matched_terms": sorted(m),
                           **({"dense": round(dense[p], 3)} if p in dense else {})} for p, sc, m in top],
            "candidates": len(docs),
            "terms_absent": absent,
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
    `truncated` and `next_start` say how to continue. A PDF is served from
    its own text layer, page by page: `section="p. 8"` serves one page,
    `pages` counts them, `page_map` gives the pages the slice covers, and
    `extract_path` is that text on disk, for grep and the harness's own
    reader (a cache, rebuilt when the PDF changes, under no `version`
    guard). A PDF or office file with a `.md` extracted beside it is served
    from that instead. Either way `source` names the original, and a PDF
    with no readable text layer is `error.unconverted`. Carry
    `version` forward as `expect_version` to be refused instead of reading
    different bytes under an old citation."""
    root = _root()
    try:
        rel = _rel(path)
    except ValueError as e:
        return _error("out_of_root", str(e))
    full = root / rel
    if not full.is_file():
        return _error("not_found", f"{rel} is not a file under {root}")
    pages: list[dict] = []
    source, paged = None, False
    if full.suffix.lower() != ".pdf" and _is_text(full):
        text = _read(full)
    else:
        sidecar = full.with_suffix(".md")
        if sidecar.is_file():
            source, full = rel, sidecar
            rel = sidecar.relative_to(root).as_posix()
            text = _read(full)
        elif full.suffix.lower() == ".pdf":
            text, pages, reason = _pdf_text(rel, full)
            if not text:
                return _error("unconverted", f"{reason} in {rel}; run `silica import {rel}`")
            source, paged = rel, True
        else:
            return _error("unconverted", f"no extracted text beside {rel}; run `silica import {rel}`")
    version = _version(text)
    if expect_version and expect_version != version:
        return _error("changed", f"{rel} is now version {version}", version=version)
    lines = text.splitlines()
    # An extracted text layer is not markdown: its pages are the structure, and
    # scanning it for `#` would read a paper's own markdown examples as headings.
    page_starts = [(p["page"], p["line"]) for p in pages] if paged else []
    outline = [] if paged else [
        {"level": h["level"], "title": h["text"], "line": text.count("\n", 0, h["pos"]) + 1}
        for h in parse_headings(text)]
    if section.strip() and paged:
        m = _PAGE.fullmatch(section.strip())
        if not m or not 1 <= int(m.group(1)) <= len(page_starts):
            return _error("not_found", f"no page matches {section!r}", pages=len(page_starts))
        n = int(m.group(1))
        start = page_starts[n - 1][1]
        end = page_starts[n][1] - 2 if n < len(page_starts) else len(lines)
    elif section.strip():
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
            "outline": outline, "source": source,
            "pages": len(page_starts) if paged else None,
            # the page the slice opens on, plus every page that starts inside it:
            # a 500-page book must not spend a page table on every read
            "page_map": [{"page": n, "line": ln} for n, ln in
                         [pl for pl in page_starts if pl[1] <= start][-1:]
                         + [pl for pl in page_starts if start < pl[1] <= served_end]] if paged else None,
            "extract_path": str(_extract_cache(rel)) if paged else None}


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


def _unescape(body: str) -> str:
    r"""Decode a body the model escaped twice: no real newline anywhere and a
    literal \n where each one belonged. Measured through `silica repl` on a
    local gemma4:e4b, which escapes its tool arguments twice, so json.loads
    hands over backslash-n and the note lands as a single line. A single real
    newline means the model got it right and the body is left alone, which is
    what keeps a fenced code block containing \n intact.

    ponytail: three replaces, not codecs unicode_escape — that one also eats a
    lone backslash and every \x and \u a note is entitled to contain.
    """
    if "\n" in body or "\\n" not in body:
        return body
    return body.replace("\\r\\n", "\n").replace("\\n", "\n").replace("\\t", "\t")


@tool(cls="atomic")
def silica_write_note(
    path: Annotated[str, Field(description="Root-relative path of the note (.md added when missing)")],
    body: Annotated[str, Field(description="Markdown body, written as given; a body escaped twice (no real newline, \\n in their place) is decoded first")],
    frontmatter: Annotated[dict[str, Any] | None, Field(description="Optional YAML frontmatter, serialised on top of the body")] = None,
    expect_version: Annotated[str, Field(description="Refuse with error.changed unless the existing file has this version")] = "",
    create_only: Annotated[bool, Field(description="Refuse when the file already exists")] = False,
) -> dict:
    """Write one note atomically, as given, then lint it: `lint` lists
    structural violations and unresolved wikilinks. Refuses to overwrite
    when `expect_version` does not match or when `create_only` is set and
    the file exists. Undo is git; the core keeps no journal."""
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
    content = body = _unescape(body)
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
