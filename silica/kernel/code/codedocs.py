# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""codedocs — doc↔source staleness for codebase mode.

A note documents source files via frontmatter:
    documents: [src/m.py, src/n.py]   # repo-relative paths
    code_ref: <sha>                   # HEAD when last verified

A note is stale if any referenced path's newest commit differs from code_ref.
Staleness state lives in per-note frontmatter (ADR decision in the spec), not a
central index. All git access goes through gitstate and degrades soft.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from silica.kernel.code import codeast, gitstate

from silica.kernel.write import frontmatter

from silica.kernel.recall import paths
from silica.kernel.code.gitstate import CommitInfo

CHANGE_COSMETIC = "cosmetic"
CHANGE_STRUCTURAL = "structural"


@dataclass(frozen=True)
class StaleDoc:
    note_path: str          # vault-relative note path
    code_path: str          # repo-relative source path that changed
    recorded_ref: str       # code_ref stored in the note
    current_ref: str        # newest commit sha for code_path
    intervening: list[CommitInfo] = field(default_factory=list)
    change_level: str = CHANGE_STRUCTURAL   # conservative default (floor, not ceiling)
    details: list[str] = field(default_factory=list)


def documents_of(data: dict) -> list[str]:
    raw = (data or {}).get("documents")
    if not raw:
        return []
    if isinstance(raw, str):
        return [raw]
    return [str(x) for x in raw if x]


_documents_of = documents_of  # internal alias (pre-rename call sites)


def validate_documents(entries: list[str], root: Path | str) -> tuple[list[str], str | None]:
    """Normalize agent-supplied `documents:` entries against the repo root.

    Trust boundary: the value reaches frontmatter and nothing else ever reads
    the path back, so a typo'd binding would be invisible forever. Rejects
    absolute paths, traversal and drive letters (same rule as `wiki_dir` in
    vault_manifest) and any entry that does not exist in the working tree.
    Returns (normalized, error): on error the caller must not write.
    """
    root = Path(root)
    out: list[str] = []
    for raw in entries or []:
        p = str(raw or "").strip().replace("\\", "/").rstrip("/")
        if not p:
            continue
        parts = p.split("/")
        if p.startswith("/") or ".." in parts or ":" in parts[0]:
            return [], f"documents entry must be a repo-relative path: {raw!r}"
        if not (root / p).exists():
            return [], f"documents entry does not exist in the repo: {p}"
        out.append(p)
    return list(dict.fromkeys(out)), None


def iter_documenting_notes(vault: Path | str):
    """Yield (note_path, data, body) for every note carrying `documents:`.

    Same pruning as every other vault walk (hidden dirs, NOISE_DIRS,
    `.silicaignore`): this one used rglob and entered `.silica/` and `.venv/`,
    which on the repo vault meant 242 residue notes of a killed probe counted
    as stale and a 17.8 s walk for 4 real notes (ADR-0038).
    """
    vault = Path(vault)
    skip = paths.ignore_matcher(vault)
    for dirpath, dirnames, filenames in os.walk(vault):
        dirnames[:] = sorted(d for d in dirnames if not d.startswith(".") and not skip(d))
        for fn in sorted(filenames):
            if not fn.endswith(".md"):
                continue
            md = Path(dirpath) / fn
            try:
                content = md.read_text(encoding="utf-8")
            except OSError:
                continue
            data, _, body = frontmatter.split(content)
            if not data or not _documents_of(data):
                continue
            yield md.relative_to(vault).as_posix(), data, body


def _skeleton_of(src: str, path: str, language):
    if path.lower().endswith(".ipynb"):
        from silica.kernel.code import ipynb
        cells = ipynb.parse_cells(src)          # ValueError → caller's fallback
        lang = ipynb.CODEAST_LANGUAGE.get(cells.language)
        if lang is None:
            raise ValueError("unsupported kernel language")
        return codeast.extract_skeleton(cells.code, lang, path=path)
    return codeast.extract_skeleton(src, language, path=path)


def classify_change(
    root: Path, base_ref: str, path: str, new_ref: str | None = None
) -> tuple[str, list[str]]:
    """Per-path verdict: skeleton of `path` at base_ref vs the working tree
    (or vs new_ref when given). Single conservative fallback branch: anything
    preventing structural analysis → STRUCTURAL with the named reason."""
    language = codeast.language_for(path)
    if language is None and not path.lower().endswith(".ipynb"):
        return CHANGE_STRUCTURAL, [f"{path}: no structural analysis (unsupported language)"]
    old_src = gitstate.show_file(root, base_ref, path)
    if old_src is None:
        return CHANGE_STRUCTURAL, [f"{path}: no structural analysis (ref {base_ref[:8]} unavailable)"]
    if new_ref is not None:
        new_src = gitstate.show_file(root, new_ref, path)
        if new_src is None:
            return CHANGE_STRUCTURAL, [f"{path}: deleted"]
    else:
        target = Path(root) / path
        if not target.is_file():
            return CHANGE_STRUCTURAL, [f"{path}: deleted"]
        try:
            new_src = target.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return CHANGE_STRUCTURAL, [f"{path}: no structural analysis (read failed)"]
    try:
        old_sk = _skeleton_of(old_src, path, language)
        new_sk = _skeleton_of(new_src, path, language)
    except ValueError as e:
        return CHANGE_STRUCTURAL, [f"{path}: no structural analysis ({e})"]
    if old_sk.parse_error or new_sk.parse_error:
        return CHANGE_STRUCTURAL, [f"{path}: no structural analysis (parse failed)"]
    diff = codeast.diff_skeletons(old_sk, new_sk)
    if not diff:
        return CHANGE_COSMETIC, []
    return CHANGE_STRUCTURAL, [f"{path}: {d}" for d in diff]


def note_verdict(docs: list[StaleDoc]) -> tuple[str, list[str]]:
    """Aggregate per-path verdicts for one note (spec §2): a single
    STRUCTURAL path makes the note structural; details concatenate."""
    level = (CHANGE_STRUCTURAL
             if any(d.change_level == CHANGE_STRUCTURAL for d in docs)
             else CHANGE_COSMETIC)
    return level, [line for d in docs for line in d.details]


def stale_docs(vault: Path | str, repo_root: Path | str | None = None,
               notes: list | None = None) -> list[StaleDoc]:
    """Return one StaleDoc per (note, changed path). Empty when git is absent.
    `notes` lets the snapshot pass the walk it already paid for."""
    vault = Path(vault)
    root = Path(repo_root) if repo_root else paths.repo_root_for(vault)
    if root is None:
        return []

    if notes is None:
        notes = list(iter_documenting_notes(vault))
    wanted: set[str] = set()
    by_ref: dict[str, set[str]] = {}
    for _, data, _ in notes:
        recorded = str(data.get("code_ref") or "").strip()
        if recorded:
            docs = _documents_of(data)
            wanted.update(docs)
            by_ref.setdefault(recorded, set()).update(docs)
    latest = gitstate.latest_shas(root, sorted(wanted))
    # One history walk per distinct code_ref, not per (note, path): notes
    # written in the same session share a ref.
    touched = {ref: gitstate.paths_touched_since(root, ref, sorted(paths))
               for ref, paths in by_ref.items()}

    out: list[StaleDoc] = []
    for note_path, data, _ in notes:
        out.extend(_stale_for_note(root, note_path, data, latest, touched))
    return out


def _stale_for_note(root: Path, note_path: str, data: dict,
                    latest: dict[str, str], touched: dict) -> list[StaleDoc]:
    """Per-(note, path) staleness, shared by the vault scan and the read gate.

    One rule in one place: a second copy for the read path would drift from the
    report and the two would disagree about the same note.
    """
    recorded = str(data.get("code_ref") or "").strip()
    if not recorded:
        return []  # unknown → not stale
    out: list[StaleDoc] = []
    for code_path in _documents_of(data):
        current = latest.get(code_path, "")
        if not current:
            continue  # path has no history → unknown, not stale
        # Not `current != recorded`: code_ref is HEAD when the note was
        # verified, so that test fires for every path HEAD did not touch
        # and reports "stale" with zero intervening commits. The path is
        # stale only when a commit after `recorded` actually touched it.
        # None = the ref does not resolve at all → conservatively stale.
        moved = touched.get(recorded)
        if moved is None or code_path in moved:
            level, details = classify_change(root, recorded, code_path)
            out.append(
                StaleDoc(
                    note_path=note_path,
                    code_path=code_path,
                    recorded_ref=recorded,
                    current_ref=current,
                    intervening=gitstate.commits_since(root, recorded, code_path),
                    change_level=level,
                    details=details,
                )
            )
    return out


# ---------------------------------------------------------------------------
# Snapshot: stale_docs() cached on HEAD (spec-stale-triggers §1)
# ---------------------------------------------------------------------------
# Staleness is created by exactly one event: HEAD moving. One cached answer,
# keyed on the HEAD sha, lets every surface read the same result for the price
# of one `git rev-parse` per call and one full recompute per HEAD move.


def _snapshot_path(vault: Path | str) -> Path:
    """Cache file location; a function so tests redirect it (conftest)."""
    return paths.index_dir_for(str(vault)) / "stale_snapshot.json"


def _doc_to_json(d: StaleDoc) -> dict:
    return {
        "note_path": d.note_path,
        "code_path": d.code_path,
        "recorded_ref": d.recorded_ref,
        "current_ref": d.current_ref,
        "intervening": [{"sha": c.sha, "committed_at": c.committed_at,
                         "subject": c.subject} for c in d.intervening],
        "change_level": d.change_level,
        "details": list(d.details),
    }


def _doc_from_json(raw: dict) -> StaleDoc:
    return StaleDoc(
        note_path=raw["note_path"],
        code_path=raw["code_path"],
        recorded_ref=raw["recorded_ref"],
        current_ref=raw["current_ref"],
        intervening=[CommitInfo(sha=c["sha"], committed_at=c["committed_at"],
                                subject=c["subject"])
                     for c in raw.get("intervening", [])],
        change_level=raw.get("change_level", CHANGE_STRUCTURAL),
        details=list(raw.get("details", [])),
    )


def snapshot(vault: Path | str, repo_root: Path | str | None = None) -> list[StaleDoc]:
    """stale_docs() served from the HEAD-keyed cache; recomputes on a miss.

    Warming entry point (read gate, digest, /wiki, /stale): a miss pays the
    full vault walk once, then every consumer reads the same answer until the
    next HEAD move. Key is HEAD only: uncommitted working-tree edits shifting
    a stale path between cosmetic and structural are a declared residue.
    """
    vault = Path(vault)
    root = Path(repo_root) if repo_root else paths.repo_root_for(vault)
    if root is None:
        return []
    head = gitstate.head_ref(root)
    if not head:
        return []
    cache = _snapshot_path(vault)
    try:
        raw = json.loads(cache.read_text(encoding="utf-8"))
        if raw.get("head") == head:
            return [_doc_from_json(d) for d in raw.get("docs", [])]
    except Exception:
        pass  # missing, corrupt, or unreadable: recompute and rewrite below
    notes = list(iter_documenting_notes(vault))
    docs = stale_docs(vault, repo_root=root, notes=notes)
    # Every documenting note, stale or not, with its binding: the join a later
    # `git diff` needs (drift below). A cache like the rest of the file, keyed
    # on HEAD; frontmatter stays the only store of a binding (ADR-0038).
    documented = {note: {"documents": _documents_of(data),
                         "code_ref": str(data.get("code_ref") or "").strip()}
                  for note, data, _ in notes}
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        # Per-pid temp name: two writers (e.g. the MCP server and a CLI run)
        # never share a temp file, so neither can publish the other's
        # partial/interleaved bytes. Each writer's os.replace is then
        # independently atomic; whichever finishes last simply wins cleanly.
        tmp = cache.parent / (cache.name + f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps({"head": head,
                                   "docs": [_doc_to_json(d) for d in docs],
                                   "documented": documented}),
                       encoding="utf-8")
        os.replace(tmp, cache)
    except Exception:
        pass  # a cache-write failure must never fail the hosting operation
    return docs


def peek(vault: Path | str, repo_root: Path | str | None = None) -> dict[str, str]:
    """note_path -> change_level from the cache, read-only. NEVER recomputes.

    Hot paths (recall tools, curation guard) call only this: a search never
    pays the walk; at worst the first search after a commit ships without
    flags. Structural wins when a note has paths at both levels.
    """
    try:
        vault = Path(vault)
        root = Path(repo_root) if repo_root else paths.repo_root_for(vault)
        if root is None:
            return {}
        head = gitstate.head_ref(root)
        if not head:
            return {}
        raw = json.loads(_snapshot_path(vault).read_text(encoding="utf-8"))
        if raw.get("head") != head:
            return {}
        return _note_levels(raw)
    except Exception:
        return {}


def _hidden(note_path: str) -> bool:
    """A note under a dot-directory is never a vault note. Snapshots written
    before the walk was pruned (2026-09-04) still hold such entries, and a
    cache is not the place to trust over the walk that now excludes them."""
    return any(seg.startswith(".") for seg in note_path.split("/")[:-1])


def _note_levels(raw: dict) -> dict[str, str]:
    """note_path -> change_level from a raw snapshot; structural wins."""
    out: dict[str, str] = {}
    for d in raw.get("docs", []):
        if _hidden(d["note_path"]):
            continue
        if out.get(d["note_path"]) != CHANGE_STRUCTURAL:
            out[d["note_path"]] = d.get("change_level", CHANGE_STRUCTURAL)
    return out


def drift(vault: Path | str, repo_root: Path | str | None = None) -> dict | None:
    """What moved since the snapshot was taken, for the session-open brief
    (ADR-0038): the cache as it is plus one `git diff snap_head..HEAD`,
    joined against the documented map. NEVER recomputes: a cold snapshot is
    measured at 80 s on the repo vault and this runs inside someone else's
    session hook. None when there is no repo, no HEAD or no snapshot yet.

    Baseline is the snapshot's own HEAD, not a per-session marker: the map is
    exact at that ref, so the diff from there is exactly what the snapshot
    does not know, and the line repeats until `/stale` refreshes it.
    """
    try:
        vault = Path(vault)
        root = Path(repo_root) if repo_root else paths.repo_root_for(vault)
        if root is None:
            return None
        head = gitstate.head_ref(root)
        if not head:
            return None
        raw = json.loads(_snapshot_path(vault).read_text(encoding="utf-8"))
        snap_head = str(raw.get("head") or "")
        if not snap_head:
            return None
        by_path: dict[str, list[str]] = {}
        for note, entry in (raw.get("documented") or {}).items():
            if _hidden(note):
                continue
            for p in entry.get("documents", []):
                by_path.setdefault(p, []).append(note)
        moved = ([] if snap_head == head
                 else gitstate.changed_paths(root, f"{snap_head}..{head}") or [])
        changed = sorted(p for p in set(moved) if p in by_path)
        affected: dict[str, list[str]] = {}
        for p in changed:
            for note in by_path[p]:
                affected.setdefault(note, []).append(p)
        return {"snap_head": snap_head, "head": head, "changed": changed,
                "affected": affected, "stale": _note_levels(raw)}
    except Exception:
        return None  # a brief is an aid; any failure here is silence, like the hook


def invalidate_snapshot(vault: Path | str) -> None:
    """Unlink the cache. Called by the write paths that stamp documents:/
    code_ref, so re-badging a note does not leave a false stale entry until
    the next HEAD move."""
    try:
        _snapshot_path(vault).unlink(missing_ok=True)
    except Exception:
        pass


def peek_level(peek_map: dict[str, str], path: str) -> str | None:
    """Level for a payload path. Peek keys end in .md (StaleDoc.note_path);
    store-keyspace paths (embed/cooccur/recall) do not — normalize here."""
    return peek_map.get(path if path.endswith(".md") else path + ".md")


def read_warning(vault: Path | str, data: dict, repo_root: Path | str | None = None) -> str:
    """One-line staleness banner for a note being READ, "" when it is fine.

    The freshness signal already existed in frontmatter and was only ever
    consumed by the `/stale` report, so an agent reading a wiki note got no hint
    that it described a layout that had since moved. Two checks, cheapest first:

    - a `documents:` path that is gone from the working tree — free, and the
      loud case: after a refactor the note names a file nobody can open;
    - the git staleness rule above, for paths that still exist.

    Soft on every failure: a banner is an aid, never a reason a read fails.
    """
    try:
        docs = _documents_of(data)
        if not docs:
            return ""
        root = Path(repo_root) if repo_root else paths.repo_root_for(Path(vault))
        if root is None:
            return ""
        missing = [p for p in docs if not (root / p).exists()]
        if missing:
            return (f"[stale] documents {len(missing)}/{len(docs)} path(s) that no longer "
                    f"exist ({', '.join(missing[:3])}) — the source moved or was deleted "
                    f"since code_ref. Verify against the working tree before trusting this.")
        recorded = str(data.get("code_ref") or "").strip()
        if not recorded:
            return ""
        # Staleness leg from the shared snapshot (spec §2). A note's staleness
        # is fully determined by its (code_ref, path) pairs, so no note path is
        # needed to find its entries. The missing-path leg above stays live and
        # uncached: the loud case is never frozen.
        #
        # Two different vault notes can share a code_ref (notes.py stamps
        # code_ref = HEAD, so every note written between two commits shares
        # one) and document an overlapping path, so the snapshot can hold
        # more than one StaleDoc for the SAME (recorded, path) pair — one per
        # note that documents it. Only one entry per path may survive here,
        # or `len(stale)` overcounts relative to the `changed` path list it is
        # reported alongside. Keep the first match per path (order follows
        # `docs`, the note's own documented paths, matching what the old
        # direct `_stale_for_note(root, "", data, ...)` call iterated).
        docset = set(docs)
        found: dict[str, StaleDoc] = {}
        for d in snapshot(vault, repo_root=root):
            if d.recorded_ref == recorded and d.code_path in docset:
                found.setdefault(d.code_path, d)
        stale = [found[p] for p in docs if p in found]
        if not stale:
            return ""
        level, _ = note_verdict(stale)
        changed = ", ".join(sorted({d.code_path for d in stale})[:3])
        return (f"[stale] {level} change in {len(stale)} documented path(s) since code_ref "
                f"{recorded[:8]} ({changed}). Verify against the source before trusting this.")
    except Exception:
        return ""


def stale_count(vault: Path | str) -> int:
    """Count of stale (note, path) pairs, served from the shared snapshot.
    Soft-zero on any failure / no git."""
    try:
        return len(snapshot(vault))
    except Exception:
        return 0
