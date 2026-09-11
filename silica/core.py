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
import threading
import time
from collections import Counter
from pathlib import Path
from typing import Annotated, Any

import orjson
import yaml
from pydantic import Field

from silica.config import CONFIG
from silica.kernel.code.codeast.base import language_for
from silica.kernel.code.codeunits import outline as _code_outline
from silica.kernel.code.codeunits import units as _code_units
from silica.kernel.link.ast import parse_headings
from silica.kernel.recall import paths as _paths
from silica.kernel.recall.lexical import TOKENIZER_VERSION, _tokens, get_store
from silica.kernel.recall.rerank import _query_terms, best_window_spans
from silica.tools import tool

K1, B = 1.5, 0.75
# candidates the section stage scores: k hits from k*POOL_FACTOR documents
# (never fewer than 10). scripts/bench_beir.py --pool-factor measures it.
POOL_FACTOR = 3
# Documents a search refreshes inline before answering. Past it the index is
# served as it is and `index.pending` says how far behind, so a bulk drop of
# files never turns one search into a rebuild: `silica index` catches up.
# ~13 ms a document on the 254-paper corpus (2026-09-08), so 50 stays under
# a second. ponytail: a count, not bytes; one 40 MB PDF still blocks.
STALE_BUDGET = 50
# The lexical store's mutators are not thread-safe (lexical.py): one thread
# in them or in a search at a time, so the warm-up thread never fills the
# store under a search. ponytail: searches serialise too; a reader-writer
# lock if a client ever fans them out.
_STORE_RW = threading.RLock()
# Seconds between partial saves during a build, so Ctrl+C at 90% of a cold
# build keeps the 90% and the next run does the rest (save() is hundreds of
# ms on a big index, so not per document).
_FLUSH_S = 5.0
_NO_EMBEDDER = ("needs SILICA_EMBEDDING_BASE_URL (an OpenAI-compatible /v1/embeddings endpoint) "
                "or SILICA_EMBEDDING_MODEL=model2vec/<id> with the [dense] extra")
# The index this engine owns. The one line silica-internal changes when it
# vendors this file: build_index() drops every path its own walk did not
# produce, so sharing a store with a lane that indexes a different set would
# have each build prune the other's entries.
INDEX = "lexical"
_HEADING = re.compile(r"(?m)^(?=#{1,4} )")
_PAGE = re.compile(r"p\.?\s*(\d+)", re.I)
_WIKILINK = re.compile(r"\[\[([^\]|#]+)")
_TEXT_SUFFIXES = frozenset({".md", ".txt", ".rst", ".csv", ".json", ".yaml", ".yml", ".toml", ".xml", ".html"})
# What the index reads as code when SILICA_INDEX_CODE is set: the languages
# codeast parses, one unit per symbol, and these as windows of lines.
# prose is always indexed, a heading section at a time; the code lane adds
# source languages (codeast's, or 80-line windows for the rest) and the
# config and data files that only mean something beside code
_PROSE_SUFFIXES = frozenset({".md", ".txt", ".rst"})
_CODE_SUFFIXES = frozenset({".rs", ".go", ".rb", ".php", ".kt", ".swift", ".scala", ".sh", ".sql", ".cfg",
                            ".ini", ".yaml", ".yml", ".toml", ".json"})
_CODE_MAX_BYTES = 512 * 1024  # a lockfile, a minified bundle, a fixture dump: not read


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _pool(k: int) -> int:
    return max(k * POOL_FACTOR, 10)


def _rrf(rankings: list[list[Any]], k: int = 60) -> dict[Any, float]:
    """Reciprocal-rank fusion (Cormack 2009, k=60): one score per key over
    several rankings, each best first. Rank is what fuses; the raw scores
    stay what they were, in the reply."""
    fused: dict[Any, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking):
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + rank + 1)
    return fused


_ROOT: tuple[str, Path] | None = None


def _root() -> Path:
    """The configured root, resolved once per value: `resolve()` on every
    call was 2.3 s of a 3 s search over 5k files (profiled 2026-09-09).
    ponytail: a root that is a symlink re-resolves only when the setting
    changes."""
    global _ROOT
    key = CONFIG.vault_path or os.getcwd()
    if _ROOT is None or _ROOT[0] != key:
        _ROOT = (key, Path(key).resolve())
    return _ROOT[1]


def _version(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "surrogateescape")).hexdigest()[:12]


def _error(code: str, hint: str, **extra: Any) -> dict:
    return {"error": {"code": code, "hint": hint}, **extra}


def _is_code(rel: str) -> bool:
    if not CONFIG.index_code:
        return False
    name = rel.rsplit("/", 1)[-1]
    suffix = name[name.rfind("."):].lower() if "." in name else ""
    return suffix in _CODE_SUFFIXES or (language_for(rel) is not None and suffix not in (".html", ".css"))


def _split_key(key: str) -> tuple[str, int | None]:
    """A unit key `path#offset` -> (path, offset); a document key -> (path, None).
    A code file is indexed one unit per symbol, each its own document to
    the store, so BM25 ranks units directly and idf is over units."""
    rel, sep, off = key.rpartition("#")
    return (rel, int(off)) if sep and rel and off.isdigit() else (key, None)


def _rel(path: str) -> str:
    """Root-relative posix path. ValueError when `path` escapes the root
    through `..` or a symlink (contain_in_vault resolves both)."""
    return _paths.contain_in_vault(path, _root())


def _read(full: Path) -> str:
    return full.read_text(encoding="utf-8", errors="replace")


def _source_state(original: Path, sidecar_text: str) -> str:
    """Whether a sidecar `.md` still describes the bytes beside it: `current`
    when the original hashes to the `source_sha256` the conversion recorded,
    `stale` when it does not (the original was replaced, the sidecar kept),
    `unverifiable` when the sidecar records no hash (a conversion older than
    the field, or a `.md` someone wrote by hand). The text's `version` says
    nothing about this: a stale sidecar is textually stable.

    ponytail: the original is hashed on every call; a folder of hundreds of
    large PDFs with sidecars pays that per listing. Cache by (mtime, size)
    beside the index if a listing ever shows it.
    """
    from silica.kernel.write.frontmatter import split

    data, _raw, _body = split(sidecar_text)
    want = data.get("source_sha256") if isinstance(data, dict) else None
    if not isinstance(want, str) or not want:
        return "unverifiable"
    try:
        return "current" if hashlib.sha256(original.read_bytes()).hexdigest() == want else "stale"
    except OSError:
        return "unverifiable"


def _conversion_of(md_full: Path, text: str) -> tuple[str | None, str | None]:
    """(root-relative original, source_state) for a `.md` a conversion wrote,
    (None, None) for any other note. Search returns the note's path, not the
    PDF's, so the freshness check has to start from the note: the original is
    the `source_file` its frontmatter names when that is a file under the
    root, else a sibling with the same stem and a convertible suffix (a vault
    moved since the conversion); neither found is `unverifiable`."""
    from silica.kernel.write.frontmatter import split
    from silica.sources.convert import DOC_EXTS

    data, _raw, _body = split(text)
    if not isinstance(data, dict) or not data.get("source_sha256"):
        return None, None
    named = data.get("source_file")
    root = _root()
    for cand in ([Path(named)] if isinstance(named, str) and named else []) + [md_full.with_suffix(e) for e in DOC_EXTS]:
        try:
            rel = _rel(str(cand))
        except ValueError:
            continue
        if (root / rel).is_file():
            return rel, _source_state(root / rel, text)
    return None, "unverifiable"


def _head(full: Path, limit: int = 65536) -> str:
    """The first `limit` characters: enough for any frontmatter, and a listing
    must not read every note whole to ask whether it is a conversion."""
    try:
        with full.open(encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


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
    """(rel, Path) for every regular file under the root, plus what the walk
    did not enter or list as (rel + "/", None) for a directory and (rel, None)
    for a file: hidden names, NOISE_DIRS, and `.silicaignore`, on files and
    directories alike. String paths and one relpath per directory: pathlib
    per file was 5 s of a 3 s search over 5k files (profiled 2026-09-09)."""
    root = _root()
    root_s = str(root)
    skip = _paths.ignore_path_matcher(root)
    for dirpath, dirnames, filenames in os.walk(base or root):
        rel_dir = os.path.relpath(dirpath, root_s).replace(os.sep, "/")
        prefix = "" if rel_dir == "." else rel_dir + "/"
        keep = []
        for d in sorted(dirnames):
            rel = prefix + d
            if d.startswith(".") or skip(rel):
                yield rel + "/", None
            else:
                keep.append(d)
        dirnames[:] = keep
        for name in sorted(filenames):
            if name.startswith("."):
                continue
            rel = prefix + name
            yield rel, (None if skip(rel) else Path(dirpath, name))


def _notes() -> dict[str, float]:
    """rel -> mtime for every document the index reads: notes, and PDFs with
    no extracted `.md` beside them — that sidecar is the note, and indexing
    both would double it. The one stat per file happens here, so
    `index_state` and `build_index` given this dict never walk again."""
    out: dict[str, float] = {}
    for rel, full in _walk():
        if full is None:
            continue
        suffix = full.suffix.lower()
        if suffix in _PROSE_SUFFIXES or (suffix == ".pdf" and not full.with_suffix(".md").is_file()):
            try:
                out[rel] = full.stat().st_mtime
            except OSError:
                continue
        elif _is_code(rel):
            try:
                st = full.stat()
            except OSError:
                continue
            if st.st_size <= _CODE_MAX_BYTES:
                out[rel] = st.st_mtime
    return dict(sorted(out.items()))


def _note_paths() -> list[str]:
    return list(_notes())


def _mtime(rel: str) -> float | None:
    try:
        return (_root() / rel).stat().st_mtime
    except OSError:
        return None


def index_state(meta: dict | None = None, live: dict[str, float] | None = None) -> dict:
    """{state: cold|stale|ready, docs, built_at, pending}. `cold` = never
    built. `pending` counts what the index has not caught up with: documents
    changed or new since their stamp, and stamped documents gone from disk.
    `live` is `_notes()` when the caller already walked."""
    meta = meta or _load_meta()
    if not _paths.index_file(INDEX).is_file() or not meta.get("built_at"):
        return {"state": "cold", "docs": 0, "built_at": None}
    live = _notes() if live is None else live
    stamps = meta.get("stamps", {})
    skipped = meta.get("no_text", {})
    pending = sum(1 for r, mt in live.items()
                  if stamps.get(r) != mt and skipped.get(r, {}).get("mtime") != mt)
    pending += sum(1 for r in stamps if r not in live)
    return {"state": "stale" if pending else "ready", "docs": len(stamps),
            "built_at": meta.get("built_at"), "pending": pending}


def _flush(store, meta: dict, failed: dict) -> None:
    """Everything the build knows so far, on disk: the store, the stamps with
    a `built_at` so the next run is incremental, the failures."""
    store.save()
    meta["built_at"] = time.time()
    _paths.atomic_write_bytes(_meta_path(), orjson.dumps(meta))
    _paths.atomic_write_bytes(_paths.index_dir() / "failures.json", orjson.dumps(failed))


def build_index(rebuild: bool = False, embed: bool = False, live: dict[str, float] | None = None,
                allow_remote: bool = False, wait: bool = True) -> dict:
    """`_build_index` under one lock per index: the stamps, the store and the
    vectors land as one, so a second server building the same folder waits
    and then finds the work done rather than interleaving with it. With
    `wait` off a busy lock is not waited for: the reply carries the index
    as it is on disk and `busy`, for a search that must answer now."""
    with _paths.index_lock(_paths.index_dir() / "build", blocking=wait) as held:
        if not held:
            state = index_state(live=live)
            return {"docs": state["docs"], "changed": 0, "failed": {}, "index": state, "busy": True}
        return _build_index(rebuild, embed, live, allow_remote)


def _build_index(rebuild: bool = False, embed: bool = False, live: dict[str, float] | None = None,
                 allow_remote: bool = False) -> dict:
    """Build or refresh the document index. Incremental by mtime; `rebuild`
    re-reads everything. Never raises for one unreadable file: it lands in
    `failed` and in the failures file `silica_files` reports from. Saves
    every `_FLUSH_S` seconds, so an interrupted build resumes where it
    stopped. `live` is `_notes()` when the caller already walked."""
    meta = _load_meta()
    rebuild = rebuild or not meta.get("built_at")
    if rebuild:
        meta = {"built_at": None, "stamps": {}, "no_text": {}, "tokenizer": TOKENIZER_VERSION}
    with _STORE_RW:  # the store is filled here; no search reads it meanwhile
        store = get_store(INDEX)
        if rebuild:  # a unit key of a rebuilt file would otherwise outlive the offset it names
            for p in store.paths():
                store.remove(p)
        stamps: dict[str, float] = meta["stamps"]
        skipped: dict[str, dict] = meta.setdefault("no_text", {})
        units: dict[str, list[str]] = meta.setdefault("units", {})  # code file -> its unit keys
        root = _root()
        live = _notes() if live is None else live
        changed, failed = 0, {}
        last_flush = time.monotonic()
        for rel, mt in live.items():
            if not rebuild and (stamps.get(rel) == mt or skipped.get(rel, {}).get("mtime") == mt):
                continue
            if rel.lower().endswith(".pdf"):
                text, _pages, reason = _pdf_text(rel, root / rel)
                if not text:  # a scan, or a PDF PDFium could not open: `unconverted`, never indexed
                    skipped[rel] = {"mtime": mt, "reason": reason}
                    if stamps.pop(rel, None) is not None:
                        store.remove(rel)
                    continue
            elif _is_code(rel):
                try:
                    parts = _sections_of(rel)[1]
                except OSError as e:
                    failed[rel] = str(e)
                    continue
                keys = [f"{rel}#{off}" for off, *_rest in parts]
                for old in units.get(rel, []):
                    if old not in keys:
                        store.remove(old)
                for (off, part, _tf, _dl, title), key in zip(parts, keys):
                    store.upsert(key, title, part)
                units[rel] = keys
                stamps[rel] = mt
                changed += 1
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
            if time.monotonic() - last_flush > _FLUSH_S:
                _flush(store, meta, failed)
                last_flush = time.monotonic()
        for gone in [p for p in list(stamps) + list(skipped) if p not in live]:
            stamps.pop(gone, None)
            skipped.pop(gone, None)
            units.pop(gone, None)
            _extract_cache(gone).unlink(missing_ok=True)  # a deleted PDF leaves no extraction behind
        for gone in [p for p in store.paths() if _split_key(p)[0] not in live]:
            store.remove(gone)
        _flush(store, meta, failed)
        # Every live document is now stamped, skipped or failed, so the state is
        # known without another walk: only the failures are still pending.
        state = {"state": "stale" if failed else "ready", "docs": len(stamps),
                 "built_at": meta["built_at"], "pending": len(failed)}
        out = {"docs": len(stamps), "changed": changed, "failed": failed, "index": state}
    if embed:
        from silica import embeddings
        if not embeddings.enabled():
            return _error("bad_argument", _NO_EMBEDDER, **out)
        host = embeddings.consent_needed()
        if host and allow_remote:
            embeddings.grant(host)
        elif host:
            return _error("consent_required", f"the sections would leave this machine for {host}: "
                          "run `silica index --embed --allow-remote` once to allow it", **out)
        to_embed = []
        for rel in live:
            if rel not in stamps:
                continue
            try:
                parts = _sections_of(rel)[1]
            except OSError:
                continue
            stem = rel[:-len(Path(rel).suffix)] if _is_code(rel) else Path(rel).stem
            to_embed.append((rel, stamps[rel], [(off, embeddings.unit_text(stem, title, part))
                                                for off, part, _tf, dl, title in parts if dl >= 3]))
        try:
            out["embeddings"] = embeddings.build(to_embed, rebuild=rebuild)
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
    """Ask what the index could see before trusting a search or calling
    absence. Inventory of the root and what the index did with each file: `indexed`
    (in the index, matches disk), `changed` (differs from the index),
    `excluded` (an ignore rule or a type the index does not read; `reason`
    says which), `failed` (read or conversion error), `unconverted` (a PDF
    whose text layer the index could not read, or an office file with no
    extracted `.md` beside it; `reason` says which). A PDF is indexed from
    its own text layer, one section per page, unless a `.md` sits beside it —
    that sidecar is indexed instead, and both rows carry `source_state`:
    `stale` means the original changed after the conversion, `unverifiable`
    that the sidecar records no hash of it or its original is not under the
    root. `index.state` is `cold` when nothing was
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
        if full is None:  # a directory not entered, or a file `.silicaignore` names
            entries.append({"path": rel, "status": "excluded",
                            "reason": "hidden" if rel.rstrip("/").rsplit("/", 1)[-1].startswith(".") else "ignore rule"})
            continue
        try:
            st = full.stat()
        except OSError:
            continue
        row: dict = {"path": rel, "bytes": st.st_size}  # `status` says whether it changed; a float mtime said nothing more
        suffix = full.suffix.lower()
        if rel in failed:
            row.update(status="failed", reason=failed[rel])
        elif suffix in _PROSE_SUFFIXES:
            row["status"] = "indexed" if stamps.get(rel) == st.st_mtime else "changed"
            src, state = _conversion_of(full, _head(full)) if suffix == ".md" else (None, None)
            if state:  # the note a conversion wrote carries the verdict too: it is the path search returns
                row.update(source=src, source_state=state)
        elif suffix in DOC_EXTS and suffix not in IMG_EXTS:
            sidecar = full.with_suffix(".md")
            skip = skipped.get(rel, {})
            if sidecar.is_file():
                row.update(status="excluded", reason=f"converted: {sidecar.relative_to(root).as_posix()}",
                           source_state=_source_state(full, _read(sidecar)))
            elif suffix != ".pdf":
                row["status"] = "unconverted"
            elif stamps.get(rel) == st.st_mtime:
                row["status"] = "indexed"
            elif skip.get("mtime") == st.st_mtime:
                # the index read this PDF and found no text to index; `reason` says why
                row.update(status="unconverted", reason=skip.get("reason") or "no text layer")
            else:
                row["status"] = "changed"
        elif _is_code(rel):
            if st.st_size > _CODE_MAX_BYTES:
                row.update(status="excluded", reason=f"not indexed: over {_CODE_MAX_BYTES // 1024} KB")
            else:
                row["status"] = "indexed" if stamps.get(rel) == st.st_mtime else "changed"
        else:
            row.update(status="excluded", reason=f"not indexed: {suffix or 'no extension'}")
        entries.append(row)
    counts = Counter(e["status"] for e in entries)
    # The folders the walk did not enter are a count in every listing and rows
    # only under status=excluded: thirteen hidden folders opened every listing
    # of this repository before any file (measured 2026-09-08).
    excluded_dirs = sum(1 for e in entries if e["path"].endswith("/"))
    if status:
        entries = [e for e in entries if e["status"] == status]
    else:
        entries = [e for e in entries if not e["path"].endswith("/")]
    try:
        offset = int(cursor) if cursor else 0
    except ValueError:
        return _error("bad_argument", "cursor must be the next_cursor of a previous reply")
    page = entries[offset:offset + limit]
    truncated = offset + limit < len(entries)
    return {"root": str(root), "total": len(entries), "files": page,
            "counts": {k: counts.get(k, 0) for k in ("indexed", "changed", "excluded", "failed", "unconverted")},
            "excluded_dirs": excluded_dirs,
            "truncated": truncated, "next_cursor": str(offset + limit) if truncated else None,
            "index": index_state(meta)}


# ---------------------------------------------------------------------------
# 2. search
# ---------------------------------------------------------------------------

_section_cache: dict[tuple[str, float], tuple[str, list[tuple[int, str, Counter, int, str]]]] = {}


def _sections_of(key: str) -> tuple[str, list[tuple[int, str, Counter, int, str]]]:
    """Text and (offset, section, term counts, length, title) per section — a
    heading section in a note, a page in a PDF, a symbol in a source file —
    cached by mtime so a second query over the same top documents does not
    retokenize (nor re-extract). For a unit key (`path#offset`) the one unit,
    with the whole file's text, so a line is counted from the file's start."""
    rel, unit = _split_key(key)
    full = _root() / rel
    stamp = (rel, full.stat().st_mtime)
    if stamp not in _section_cache:
        if full.suffix.lower() == ".pdf":
            text, pages, _reason = _pdf_text(rel, full)
            bounds = [p["offset"] for p in pages] + [len(text)]
            parts = [(p["offset"], text[p["offset"]:bounds[i + 1]], f"p. {p['page']}")
                     for i, p in enumerate(pages)]
        elif _is_code(rel):
            text = _read(full)
            parts = _code_units(rel, text)
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
        _section_cache[stamp] = (text, secs)
    text, secs = _section_cache[stamp]
    return text, (secs if unit is None else [s for s in secs if s[0] == unit])


@tool(cls="atomic")
def silica_search(
    query: Annotated[str, Field(description="Concise question or description of the concept or behavior to find; preserve known names and identifiers")],
    folder: Annotated[str, Field(description="Only documents under this root-relative folder")] = "",
    k: Annotated[int, Field(description="Max hits")] = 5,
    per_doc: Annotated[int, Field(description="Max sections per document")] = 2,
    width: Annotated[int, Field(description="Characters in each hit's window")] = 600,
    queries: Annotated[list[str] | None, Field(description="More query groups, each ranked on its own and fused with `query` by rank: the concept in one, its identifiers in another")] = None,
) -> dict:
    """For a question that names no identifier (what a behaviour is, where
    it is decided, why), call this before Grep, Read or Glob: it ranks
    passages and, in a source tree, the functions, methods, classes and
    constants themselves, by the question's words and their vectors.
    Do not grep for the words of a question; grep
    for an exact string or a symbol name you already know. Ranked passages
    with an honest zero: BM25 over documents, then over the heading sections
    of the top documents, at most `per_doc` per document; each hit carries
    its densest `width`-char window and line. A PDF's section is a page, so
    on a PDF hit widen `width` (1200-1500) rather than reading the page.
    `score` is raw BM25 (comparable within one call only). `matched_terms`
    are the query terms in the hit; `coverage` is the share of the query's
    idf mass they carry, absent terms included at the weight of a term found
    nowhere (near 1 = every rare term matched, near 0 = only the common
    words); `terms_absent` are query terms found nowhere in the corpus. No
    boolean "no answer" exists: read coverage and matched_terms
    and decide to stop or rephrase. The dense leg (one vector per section)
    runs once the vectors are built (the server's warm-up, or `silica index
    --embed`): `dense` in the reply says `ready` and how many documents it
    covered, or why not (`off`, `warming`, `no_vectors`, `consent_required`,
    `failed`) while the search stayed lexical; the user's to fix, not the
    caller's. With
    `queries` or the dense leg the order is by reciprocal rank across the
    groups (and the vectors); `score`, `matched_terms` and `coverage` then
    read over all the groups' terms, and a hit the dense leg alone found
    carries `dense` and coverage 0. In a source tree (vault.yaml `sources`;
    a git repository by default) source files are indexed one unit per
    function, method, class or constant (line windows where no parser
    applies), ranked beside the notes: a hit's `section` is the symbol,
    `span` its lines, and `silica_read(path, section=<symbol>)` serves its
    body. Builds the index on first use."""
    from silica import embeddings
    live = _notes()
    # A local-hybrid warm-up over a folder nothing has indexed yet: wait for
    # the first lexical index to land, this process's or another's, rather
    # than answer empty. Anything already on disk is served at once, even
    # while another process holds the build through its embedding phase.
    while index_state(live=live)["state"] == "cold" and embeddings.warmup_state()["state"] == "warming":
        embeddings.wait_lexical(0.5)
    with _STORE_RW:
        return _search(query, folder, k, per_doc, width, queries, live)


def _search(query: str, folder: str, k: int, per_doc: int, width: int, queries: list[str] | None,
            live: dict[str, float]) -> dict:
    groups = [query] + [q for q in (queries or []) if q.strip()]
    # The dense leg is the user's call, made at index time, not the caller's:
    # it runs whenever the vectors are there and the query may go to the
    # endpoint; otherwise the reply says why and the search stays lexical.
    from silica import embeddings
    warm = embeddings.warmup_state()
    leg: dict[str, Any]
    if warm["state"] == "warming":
        leg = {"state": "warming", "hint": "the vectors are building in the background; this search is lexical"}
    elif warm["state"] == "failed":
        leg = {"state": "failed", "hint": warm["hint"]}
    elif not embeddings.enabled():
        leg = {"state": "off"}
    elif embeddings.load() is None:
        leg = {"state": "no_vectors", "hint": "run `silica index --embed`"}
    elif host := embeddings.consent_needed():
        leg = {"state": "consent_required", "hint": f"the query would leave this machine for {host}: "
               "run `silica index --embed --allow-remote` once to allow it"}
    else:
        leg = {"state": "ready"}
    hybrid = leg["state"] == "ready"
    state = index_state(live=live)
    if warm["state"] != "warming" and (
            state["state"] == "cold" or (state["state"] == "stale" and state["pending"] <= STALE_BUDGET)):
        # `wait=False`: another process mid-build is served around, not waited for
        state = build_index(rebuild=state["state"] == "cold", embed=embeddings.lazy_embed(), live=live,
                            wait=False)["index"]
    # else: served as it is; `index` in the reply says stale and how far behind
    store = get_store(INDEX)
    group_terms = [set(_tokens(q)) for q in groups]
    idf = store.idf(set().union(*group_terms))
    known = {t: v for t, v in idf.items() if v is not None}
    absent = sorted(t for t, v in idf.items() if v is None)
    # A term found nowhere weighs what BM25 gives a term at df=0, the heaviest
    # in the corpus, so a query whose rare words are all absent reads low even
    # when its one surviving word matches: measured 2026-09-08, "kubernetes
    # ingress controller nginx annotations" read coverage 1.0 on "annotations".
    idf_absent = math.log(1 + (len(store) + 0.5) / 0.5)
    mass = (sum(known.values()) + idf_absent * len(absent)) or 1.0
    scope = ""
    if folder.strip():
        try:
            scope = _rel(folder)
        except ValueError as e:
            return _error("out_of_root", str(e))
    rankings = [[d for d in store.bm25(q) if _paths.in_folder(d[0], scope)] for q in groups]
    # What the search could see: a relevant document among the unconverted or
    # the failed never ranks, and a term the corpus holds may still be absent
    # from the folder. `in_folder` with an empty folder is the whole root.
    meta = _load_meta()
    visible = {"folder": scope,
               "docs": len({_split_key(p)[0] for p in store.paths() if _paths.in_folder(p, scope)}),
               "unconverted": sum(1 for p in meta.get("no_text", {}) if _paths.in_folder(p, scope)),
               "failed": sum(1 for p in _failures() if _paths.in_folder(p, scope))}
    absent_in_scope = sorted(t for t in known
                             if not any(_paths.in_folder(p, scope) for p in store.paths_with(t)))
    dense_docs: list[tuple[str, float]] = []
    dense_secs: dict[tuple[str, int], float] = {}
    if hybrid:
        try:
            dense_docs, dense_secs = embeddings.rank(query, live)
        except Exception as e:  # the endpoint is the extension's business: the reply says so, the search stays lexical
            leg, hybrid = {"state": "failed", "hint": f"embeddings endpoint failed: {e}"}, False
        else:
            # the dense ranking at the store's granularity: a note by its best
            # section, a code file unit by unit, so the fusion ranks like keys
            best: dict[str, float] = {}
            for (r, o), c in dense_secs.items():
                dk = f"{r}#{o}" if _is_code(r) else r
                if c > best.get(dk, -2.0) and _paths.in_folder(dk, scope):
                    best[dk] = c
            dense_docs = sorted(best.items(), key=lambda kv: (-kv[1], kv[0]))
            leg["docs"] = len({_split_key(dk)[0] for dk in best})
    fuse = hybrid or len(groups) > 1
    if not fuse:
        docs = rankings[0]
    else:
        # one ranking per group, plus the dense one: the fused order is by
        # rank; a document keeps its best raw score and the union of its terms
        lex: dict[str, tuple[float, set]] = {}
        for ranking in rankings:
            for p, sc, m in ranking:
                sc0, m0 = lex.get(p, (0.0, set()))
                lex[p] = (max(sc0, sc), m0 | m)
        lists: list[list[Any]] = [[p for p, _s, _m in ranking] for ranking in rankings]
        if hybrid:
            lists.append([p for p, _c in dense_docs])
        fused = _rrf(lists)
        docs = [(p, *lex.get(p, (0.0, set()))) for p in sorted(fused, key=lambda p: (-fused[p], p))]
    top = docs[: _pool(k)]
    dense = dict(dense_docs)  # a document's best section cosine, for `documents`
    text_all = " ".join(groups)
    weights = store.query_idf(_query_terms(text_all))
    secs: list[tuple[str, int, str, Counter, int, str]] = []
    texts: dict[str, str] = {}
    for path, _score, _matched in top:
        rel = _split_key(path)[0]
        try:
            texts[rel], parts = _sections_of(path)
        except OSError:
            continue
        secs.extend((rel, off, part, tf, dl, title) for off, part, tf, dl, title in parts)
    avg = (sum(s[4] for s in secs) / len(secs)) if secs else 1.0

    def bm25(tf: Counter, dl: int, terms: set) -> float:
        return sum(known[t] * (tf[t] * (K1 + 1)) / (tf[t] + K1 * (1 - B + B * dl / avg)) for t in terms)

    # every section of the pool that some leg can rank: a lexical match in any
    # group, or (hybrid) a vector; `scored` keeps what the reply shows
    scored: dict[tuple[str, int], tuple[float, str, set, str]] = {}
    per_group: list[list[tuple[float, tuple[str, int]]]] = [[] for _g in groups]
    for path, off, part, tf, dl, title in secs:
        matched = {t for t in known if tf.get(t)}
        key = (path, off)
        if not matched and key not in dense_secs:
            continue
        scored[key] = (bm25(tf, dl, matched), part, matched, title)
        for gi, tg in enumerate(group_terms):
            mg = {t for t in tg if t in matched}
            if mg:
                per_group[gi].append((bm25(tf, dl, mg), key))
    if not fuse:
        order = sorted(scored, key=lambda key: (-scored[key][0], key[0], key[1]))
    else:
        sec_lists: list[list[Any]] = [[key for _s, key in sorted(g, key=lambda x: (-x[0], x[1]))] for g in per_group]
        if hybrid:
            sec_lists.append([key for key, _c in sorted(((key, c) for key, c in dense_secs.items() if key in scored),
                                                        key=lambda kv: (-kv[1], kv[0]))])
        fused_secs = _rrf(sec_lists)
        order = sorted(fused_secs, key=lambda key: (-fused_secs[key], key[0], key[1]))
    hits: list[dict] = []
    seen: dict[str, int] = {}
    versions: dict[str, str] = {}  # the hash silica_read checks, from the text already in hand
    for key in order:
        path, off = key
        score, part, matched, title = scored[key]
        if seen.get(path, 0) >= per_doc:
            continue
        seen[path] = seen.get(path, 0) + 1
        o, window = best_window_spans(part, text_all, width, 1, weights, snap=True)[0]
        hit = {"path": path, "section": title,
               "line": texts[path].count("\n", 0, off + o) + 1,
               "version": versions.setdefault(path, _version(texts[path])),
               "score": round(score, 2), "matched_terms": sorted(matched),
               "coverage": round(sum(known[t] for t in matched) / mass, 2),
               "window": window}
        if _is_code(path):  # the unit's lines, for a read of the whole symbol
            first = texts[path].count("\n", 0, off) + 1
            hit["span"] = [first, first + part.rstrip("\n").count("\n")]
        if key in dense_secs:
            hit["dense"] = round(dense_secs[key], 3)
        hits.append(hit)
        if len(hits) == k:
            break
    return {"root": str(_root()), "query": query, **({"queries": groups[1:]} if len(groups) > 1 else {}),
            "hits": hits,
            # the first k of the ranking plus any document a hit came from lower
            # down: enough to tell "right paper, wrong section" from "wrong
            # paper" without repaying the whole candidate list (43% of the reply
            # at 15 entries, measured 2026-09-08)
            "documents": [{"path": _split_key(p)[0], "score": round(sc, 2), "matched_terms": sorted(m),
                           "coverage": round(sum(known[t] for t in m) / mass, 2),
                           **({"line": texts[_split_key(p)[0]].count("\n", 0, _split_key(p)[1]) + 1}
                              if _split_key(p)[1] is not None and _split_key(p)[0] in texts else {}),
                           **({"dense": round(dense[p], 3)} if p in dense else {})}
                          for i, (p, sc, m) in enumerate(top) if i < k or _split_key(p)[0] in versions],
            "candidates": len(docs),
            "terms_absent": absent,
            **({"terms_absent_in_scope": absent_in_scope} if scope else {}),
            "scope": visible,
            "dense": leg,
            "index": state}


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
    """Read a located slice before citing it: lines `start..end`, one
    `section` by heading, a PDF page, or the whole file when it fits
    `max_chars`, always with the outline and a `version`;
    `truncated` and `next_start` say how to continue. A PDF is served from
    its own text layer, page by page: `section="p. 8"` serves one page —
    the whole page, ~1.2k tokens (measured 2026-09-09), so ask for it to cite
    a passage a search located, and widen the search's `width` to explore —
    `pages` counts them, `page_map` gives the pages the slice covers, and
    `extract_path` is that text on disk, for grep and the harness's own
    reader (a cache, rebuilt when the PDF changes, under no `version`
    guard). A PDF or office file with a `.md` extracted beside it is served
    from that instead, and `source_state` says whether the original still
    hashes to what the conversion recorded: `stale` means the PDF changed
    and the `.md` did not, `unverifiable` that the sidecar records no hash or
    its original is gone. Reading the `.md` itself, the path a search hit
    carries, reports the same `source` and `source_state`.
    Either way `source` names the original, and a PDF
    with no readable text layer is `error.unconverted`. Carry
    `version` forward as `expect_version` to be refused instead of reading
    different bytes under an old citation; `version` is the served text's,
    never the original's."""
    root = _root()
    try:
        rel = _rel(path)
    except ValueError as e:
        return _error("out_of_root", str(e))
    full = root / rel
    if not full.is_file():
        return _error("not_found", f"{rel} is not a file under {root}")
    pages: list[dict] = []
    source, paged, source_state = None, False, None
    if full.suffix.lower() != ".pdf" and _is_text(full):
        text = _read(full)
        if full.suffix.lower() == ".md":
            source, source_state = _conversion_of(full, text)
    else:
        sidecar = full.with_suffix(".md")
        if sidecar.is_file():
            text = _read(sidecar)
            source_state = _source_state(full, text)
            source, full = rel, sidecar
            rel = sidecar.relative_to(root).as_posix()
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
    if paged:
        outline = []
    elif _is_code(rel):
        # symbols, methods included while the file is small enough that the
        # table stays a fraction of the read (a 500-symbol module lists its classes)
        outline = _code_outline(rel, text)
        if len(outline) > 150:
            outline = [h for h in outline if h["level"] == 1]
    else:
        outline = [{"level": h["level"], "title": h["text"], "line": text.count("\n", 0, h["pos"]) + 1}
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
        end = h.get("end_line") or next((o["line"] - 1 for o in outline[idx[0] + 1:] if o["level"] <= h["level"]), len(lines))
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
            "outline": outline, "source": source, "source_state": source_state,
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
    target: Annotated[str, Field(description="Root-relative source path, optionally narrowed with '#Class', '#Class.member' or '#L<line>' (the symbol whose declaration spans that line)")],
    budget_chars: Annotated[int, Field(description="Character budget for the whole pack; the target is always served")] = 24000,
    sections: Annotated[list[str] | None, Field(description="Sections besides the target: any of 'hierarchy', 'callers', 'neighborhood', 'external', 'importers'. Empty = all")] = None,
) -> dict:
    """Use before editing or explaining a source file, or when a traceback or
    a diff names `file:line`: one call serves the file, a `#Class.member`, or
    the symbol at `#L<line>`, with its supertypes, extenders, callers, the
    signatures it names, external dependencies and importers, inside a
    character budget. Static AST only, same repo state, same bytes. Check
    `truncated` before treating the target as complete; `dropped` names what
    did not fit. `languages` lists the parsers this install has."""
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
