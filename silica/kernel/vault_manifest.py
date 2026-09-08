# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Vault manifest — declared capabilities per vault (ADR-0014).

`<vault>/vault.yaml` declares which source adapters participate and the
co-occurrence language. This is
composition, not taxonomy: there is no vault *type*. Absence of the file ⇒
retro-compatible defaults (prose always on; code on iff the vault sits
inside a git repo) — no migration required. Cached like kernel/overlay.py;
reset on /vault switch.
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from silica.kernel.recall import paths

logger = logging.getLogger(__name__)

MANIFEST_REL = "vault.yaml"
# Where notes go in a source tree, and the safe-mode mirror folder of a prose vault.
CODE_WRITE_DIR = "docs/silica"
SAFE_WRITE_DIR = "silica"


@dataclass(frozen=True)
class VaultManifest:
    sources: tuple[str, ...]
    cooccurrence_lang: str | None = None
    # Write boundary: the only subtree of the vault Silica may create, patch,
    # move or delete notes in. "" ⇒ the vault root (in place, today's Obsidian
    # behaviour and the default for a vault with no manifest). A relative subdir
    # ⇒ reads stay vault-wide, writes are confined there, so everything outside
    # is read-only context. Top-level and parsed on its own: this is a boundary
    # the framework enforces against the model, and no malformed sibling key may
    # widen it. None ⇒ declared but unresolvable; the activation seam refuses the
    # vault instead of silently writing everywhere.
    write_dir: str | None = ""


# There is deliberately no function mapping a directory to "the vault it really
# means". The vault is the directory you launched in or named, full stop, and a
# resolver that could answer something else is what made the vault a thing you
# had to reconstruct rather than read off the screen. Where notes may be written
# inside it is the separate `write_dir` axis (`onboarding.adopt`).


def _safe_rel_dir(value) -> str | None:
    """Normalize a user-authored vault-relative dir; None when it escapes.

    Trust boundary: vault.yaml is hand-written and these paths reach the read
    and write paths — an absolute path or a traversal would scatter notes
    outside the vault, invisible to the index, /undo and snapshots. "" and "."
    both mean the vault root. Shared by write_dir, wiki_dir and templates_dir
    so the rule is stated once.
    """
    if not isinstance(value, str):
        return None
    raw = value.strip().replace("\\", "/")
    if not raw or raw == ".":
        return ""
    if raw.startswith("/"):
        return None
    parts = [p for p in raw.split("/") if p and p != "."]
    # `./` and `././` filter down to nothing: still the vault root, never an
    # IndexError out of load_manifest — this module's contract is that a
    # malformed manifest degrades softly, and it is read at vault activation.
    if not parts:
        return ""
    if ".." in parts or ":" in parts[0]:
        return None
    return "/".join(parts)


def within(rel_path: str, root: str) -> bool:
    """True when vault-relative `rel_path` sits inside vault-relative dir `root`.

    `root=""` is the vault root, which contains everything. Segment-wise so
    `docs/silica` never matches `docs/silicate/x.md`.
    """
    if not root:
        return True
    prefix = root.strip("/").lower()
    p = (rel_path or "").replace("\\", "/").strip("/").lower()
    return p == prefix or p.startswith(prefix + "/")


def default_sources(vault: str | Path) -> tuple[str, ...]:
    out = ["prose"]
    try:
        if vault and paths.repo_root_for(vault) is not None:
            out += ["code", "notebook"]
    except Exception:
        pass
    return tuple(out)


def load_manifest(vault: str | Path) -> VaultManifest:
    """Parse <vault>/vault.yaml; absent or malformed ⇒ defaults (soft)."""
    defaults = VaultManifest(sources=default_sources(vault))
    if not vault:
        return defaults
    path = Path(vault) / MANIFEST_REL
    if not path.is_file():
        return defaults
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("vault.yaml: parse failed (%s) — using defaults", exc)
        return defaults
    if not isinstance(raw, dict):
        logger.warning("vault.yaml: expected a mapping — using defaults")
        return defaults

    sources = raw.get("sources")
    if isinstance(sources, list) and sources and all(isinstance(s, str) for s in sources):
        src = tuple(sources)
    else:
        if sources is not None:
            logger.warning("vault.yaml: `sources` must be a non-empty string list — using defaults")
        src = defaults.sources

    lang = raw.get("cooccurrence_lang")

    # Absent ⇒ "" (vault root, in place). Declared-but-unresolvable ⇒ None, and
    # unlike every other field that does NOT degrade to the default: the default
    # is the widest write scope, so a typo would silently hand the whole vault
    # to the writer. `cli` refuses to activate the vault instead.
    write_dir = "" if raw.get("write_dir") is None else _safe_rel_dir(raw.get("write_dir"))
    if write_dir is None:
        logger.warning(
            "vault.yaml: `write_dir` must be a relative path inside the vault — got %r",
            raw.get("write_dir"),
        )

    return VaultManifest(
        sources=src,
        cooccurrence_lang=lang if isinstance(lang, str) and lang else None,
        write_dir=write_dir,
    )


_WRITE_DIR_LINE = re.compile(r"write_dir\s*:")


def set_write_dir(vault: str | Path, value: str) -> Path:
    """Declare `write_dir: <value>` in `<vault>/vault.yaml`; "" ⇒ in place.

    The writer behind the settings panel's safe-mode toggle. Line-level rather
    than a yaml round-trip because vault.yaml is hand-written: safe_dump would
    hand it back reordered and stripped of every comment. Only a top-level
    `write_dir:` line is replaced (an indented one belongs to another block), and
    a manifest that has none gets it appended, so nothing else in the file moves.

    Clearing writes `write_dir: ""` instead of deleting the line: "in place" is a
    decision the user made and the file should say so.
    """
    from silica.kernel.recall.paths import atomic_write_bytes

    path = Path(vault) / MANIFEST_REL
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    line = f"write_dir: {value}\n" if value else 'write_dir: ""\n'

    out: list[str] = []
    replaced = False
    for existing in text.splitlines(keepends=True):
        if not replaced and _WRITE_DIR_LINE.match(existing):
            out.append(line)
            replaced = True
        else:
            out.append(existing)
    if not replaced:
        if out and not out[-1].endswith("\n"):
            out.append("\n")
        out.append(line)

    atomic_write_bytes(path, "".join(out).encode("utf-8"))
    reset_manifest_cache()
    return path


_cached: VaultManifest | None = None


def reset_manifest_cache() -> None:
    """Invalidate the cache. Use in tests and after /vault switch."""
    global _cached
    _cached = None


def get_active_manifest() -> VaultManifest:
    global _cached
    if _cached is None:
        from silica.config import CONFIG

        _cached = load_manifest((getattr(CONFIG, "vault_path", "") or "").strip())
    return _cached


# A path no note can have (validate sanitizes filenames and prunes hidden dirs),
# so every write op is rejected while a broken declaration stands.
_UNRESOLVABLE_WRITE_DIR = ".invalid-write-dir"


def active_write_dir() -> str:
    """Vault-relative write boundary for the active vault; "" ⇒ the whole vault.

    An unresolvable declaration (None) never degrades to "": the /vault seam
    refuses activation, and answering with an impossible path keeps any caller
    that skipped that seam (GUI, MCP) from writing vault-wide.
    """
    declared = get_active_manifest().write_dir
    return declared if declared is not None else _UNRESOLVABLE_WRITE_DIR


def resolve_inbox_dir(vault_root: str | Path, write_dir: str, inbox: str) -> str:
    """Where the inbox IS, given a declared boundary and a folder name.

    The composed path (`<write_dir>/<inbox>`) is where Silica CREATES its
    staging area, so it wins whenever it exists and whenever neither exists — a
    fresh vault must not scatter an inbox outside the boundary. But a vault that
    already keeps its `Inbox/` at the root predates the boundary, and composing
    over it pointed every inbox seam at a folder that is not there:
    `list_inbox_files` came back empty while the user was staring at a full
    inbox, and doctor reported the one that exists as missing.

    An unresolvable boundary never falls back. `_UNRESOLVABLE_WRITE_DIR` exists
    to make every path impossible, and answering with the root inbox would hand
    back a writable one.
    """
    inbox = (inbox or "").replace("\\", "/").strip("/")
    if not inbox:
        return ""
    write_dir = (write_dir or "").strip("/")
    if not write_dir:
        return inbox
    composed = f"{write_dir}/{inbox}"
    if write_dir == _UNRESOLVABLE_WRITE_DIR or not vault_root:
        return composed
    root = Path(vault_root)
    if (root / composed).is_dir() or not (root / inbox).is_dir():
        return composed
    return inbox


def active_inbox_dir() -> str:
    """Vault-relative inbox root for the active vault; "" ⇒ no inbox configured.

    The inbox is Silica's own staging area, so it belongs inside the write
    boundary like everything else Silica creates. Composed here rather than read
    raw off `CONFIG.inbox_dir` because that field knows nothing about
    `write_dir`: every caller that built a path from it was dropping an `Inbox/`
    at the root of the user's source tree, outside the one folder writes are
    supposed to land in. A root inbox that already exists keeps its place —
    see `resolve_inbox_dir`.
    """
    from silica.config import CONFIG

    return resolve_inbox_dir(
        getattr(CONFIG, "vault_path", "") or "",
        active_write_dir(),
        getattr(CONFIG, "inbox_dir", "") or "",
    )


def in_write_dir(rel_path: str) -> str:
    """`rel_path` composed into the write boundary; unchanged when already inside.

    The composer behind every folder Silica creates for its own bookkeeping
    (`done/`, `sources/`) the way `active_inbox_dir` composes the inbox. Those
    call sites build their path from a bare constant, which knows nothing about
    `write_dir` and so drops a folder at the root of a vault whose writes are
    confined elsewhere.
    """
    rel = (rel_path or "").replace("\\", "/").strip("/")
    write_dir = active_write_dir()
    if not write_dir or not rel or within(rel, write_dir):
        return rel
    return f"{write_dir}/{rel}"


def apply_manifest_to_config() -> None:
    """Manifest determines CONFIG fields the environment did not set (env
    wins). Symmetric on purpose: a vault that declares nothing clears a
    previous vault's setting on /vault switch instead of leaking it."""
    from silica.config import CONFIG

    m = get_active_manifest()
    if os.getenv("SILICA_LANG") is None and os.getenv("SILICA_COOCCURRENCE_LANG") is None:
        # "auto" mirrors the config-level default for this field (per-store
        # detection, frozen at build — see kernel/cooccurrence.py). A vault
        # without a declared cooccurrence_lang must NOT be silently pinned to
        # english.
        CONFIG.lang = m.cooccurrence_lang or "auto"
