# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""notetype — the OKF `type` field, derived and censused.

Open Knowledge Format v0.2 (§4.1) requires every note to declare a `type`.
Silica never asked the user for one, so it is derived here from signals the
kernel already keys on (source-leaf path, code bindings, plan status) and
stamped at the driver seam when the field is absent. Presence wins: a human-
or agent-authored `type` is never overwritten — §4.1 tolerates unknown types,
so the user's own vocabulary stays conformant.

`okf_conformance()` is the read side: it walks a vault and reports the three
§11 clauses (parseable frontmatter, non-empty `type`, no reserved `index`/
`log` note names). `silica doctor` renders it; `scripts/backfill_notetype.py`
closes the legacy gap once.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import NamedTuple

from silica.kernel.write import frontmatter

# The four values derive_type can produce. Free-form per §4.1 — this is
# Silica's vocabulary, not the spec's.
SOURCE, CODE, PLAN, NOTE = "Source", "Code", "Plan", "Note"

# §11.3 binds these names to a bundle-level index/changelog when present. The
# vault generates neither, so a note carrying the name is a collision to rename
# by hand.
RESERVED_NAMES = frozenset({"index", "log"})


def derive_type(path: str, content: str) -> str:
    """The OKF `type` for a note, from signals the vault already carries.

    Precedence is by strength of evidence: a path under `sources/` is a leaf
    whatever its frontmatter says, code bindings outrank a plan status, and
    everything without a signal is a plain Note.
    """
    from silica.kernel.plans import VALID_STATUS
    from silica.kernel.recall.paths import is_source_leaf

    if is_source_leaf(path):
        return SOURCE
    data, _raw, _body = frontmatter.split(content)
    data = data or {}
    if data.get("documents") or data.get("code_ref"):
        return CODE
    if str(data.get("status") or "").strip() in VALID_STATUS:
        return PLAN
    return NOTE


_TYPE_KEY_RE = re.compile(r"^type:\s", re.MULTILINE)


def stamp_type(path: str, content: str) -> str:
    """Insert a derived `type` into a frontmatter block that lacks the field.

    String-level, like `templates.ensure_ai_flag`: the rest of the user's
    frontmatter stays byte-for-byte intact. No-ops when there is no
    frontmatter block (adding one to a plain-markdown note is the user's call,
    and the walker censuses it), when the YAML is unparseable (stamping into
    it would corrupt it further), and when `type` is already present.
    """
    if not content.startswith("---\n"):
        return content
    end = content.find("\n---\n", 4)
    if end == -1:
        return content  # unterminated frontmatter — the walker flags it
    if _TYPE_KEY_RE.search(content[4:end]):
        return content
    data, _raw, _body = frontmatter.split(content)
    if data is None:
        return content
    return content[:end] + f"\ntype: {derive_type(path, content)}" + content[end:]


# OKF §5.2 `verified`. The actor prefix is what separates a person vouching for
# a note from a pipeline re-running over it; only the former carries authority.
HUMAN_ACTOR_PREFIX = "human:"

# Who signs an agent write. Set by the MCP server from the client's own name
# in the initialize handshake, never by the model calling a tool: an identity
# a caller can assert about itself is not provenance. One value per process
# because a stdio server serves exactly one client for its whole life.
_MCP_CLIENT = ""


def set_mcp_client(identity: str) -> None:
    global _MCP_CLIENT
    _MCP_CLIENT = identity.strip()


def agent_id() -> str:
    """The agent behind this write, or "" when a person is at the REPL.

    SILICA_AGENT_ID first: a fleet operator naming its agents outranks what a
    client calls itself. Then the handshake identity. Empty means no agent,
    which is why callers that need a name fall back to `user` themselves
    rather than this function guessing one.
    """
    return os.environ.get("SILICA_AGENT_ID", "").strip() or _MCP_CLIENT


def verified_entries(data: dict | None) -> list[dict]:
    """The `verified` entries of a note's frontmatter, always as a list.

    §5.2 is a MUST on readers: a single `{by, at}` mapping means a one-element
    list. Hand-edited frontmatter is where this field comes from, and a person
    writing one verification writes the mapping, not a list of one.
    """
    raw = (data or {}).get("verified")
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [e for e in raw if isinstance(e, dict)]
    return []


def is_human_verified(data: dict | None) -> bool:
    """True when a person vouched for this note (`verified[].by: human:…`)."""
    return any(
        str(e.get("by") or "").strip().lower().startswith(HUMAN_ACTOR_PREFIX)
        for e in verified_entries(data)
    )


def _is_human(entry: dict) -> bool:
    return str(entry.get("by") or "").strip().lower().startswith(HUMAN_ACTOR_PREFIX)


def body_sha256(content: str) -> str:
    """The hash an attestation binds to: the body alone, stripped. Frontmatter
    is excluded so stamping `verified` itself (or any later metadata edit)
    cannot invalidate what a person vouched for; the strip so a driver that
    normalises the trailing newline does not read as an edit."""
    _data, _raw, body = frontmatter.split(content or "")
    return hashlib.sha256((body or "").strip().encode("utf-8")).hexdigest()


# The `verified:` key with every indented continuation line under it, in
# either §5.2 shape (a mapping or a list of mappings). String-level like
# `stamp_type`: the rest of the block is never re-serialised.
_VERIFIED_BLOCK_RE = re.compile(r"^verified:[^\n]*\n(?:[ \t]+[^\n]*\n?)*", re.MULTILINE)


def _render_verified(entries: list[dict]) -> str:
    lines = ["verified:"]
    for e in entries:
        lead = "  - "
        for k, v in e.items():
            # Every scalar quoted: an unquoted 2026-09-04 comes back a date
            # object and the `at` a reader compares would no longer be a str.
            lines.append(f"{lead}{k}: {json.dumps(str(v), ensure_ascii=False)}")
            lead = "    "
    return "\n".join(lines) + "\n"


def _replace_verified(content: str, entries: list[dict]) -> str:
    """`content` with its `verified` block replaced by `entries` (dropped when
    empty). Requires a frontmatter block; the callers guarantee one."""
    if content.startswith("---\n---\n"):
        fm, tail = "", content[4:]
    else:
        end = content.find("\n---\n", 4)
        fm, tail = content[4:end + 1], content[end + 1:]
    fm = _VERIFIED_BLOCK_RE.sub("", fm)
    if fm and not fm.endswith("\n"):
        fm += "\n"
    if entries:
        fm += _render_verified(entries)
    return "---\n" + fm + tail


def stamp_verified(content: str, by: str, at: str) -> str:
    """Append a person's §5.2 entry, bound to the body it vouches for
    (`body_sha256`, spec M4). Only the `/verify` command calls this, and the
    actor prefix is enforced here as well: the MCP write tools refuse the key
    outright, so no model-controlled path can mint a human attestation."""
    if not by.strip().lower().startswith(HUMAN_ACTOR_PREFIX):
        raise ValueError(f"an attestation must name a person ({HUMAN_ACTOR_PREFIX}…), got {by!r}")
    content = content or ""
    entry = {"by": by.strip(), "at": at, "body_sha256": body_sha256(content)}
    if not content.startswith("---\n"):
        return "---\n" + _render_verified([entry]) + "---\n\n" + content
    data, raw, _body = frontmatter.split(content)
    if data is None:
        raise ValueError("frontmatter is not a YAML mapping; fix it before attesting")
    return _replace_verified(content, verified_entries(data) + [entry])


def clear_verified(content: str) -> str:
    """Drop every human entry; machine entries stay (a pipeline's mark is not
    a person's to withdraw)."""
    data, _raw, _body = frontmatter.split(content or "")
    if not data or not verified_entries(data):
        return content
    return _replace_verified(content, [e for e in verified_entries(data) if not _is_human(e)])


def attestation_drift(content: str) -> str | None:
    """The `at` of the newest human attestation whose body has changed since,
    None when there is none or none can be checked. A hand-written entry
    without `body_sha256` predates the binding: unknowable, never stale."""
    data, _raw, _body = frontmatter.split(content or "")
    if not data:
        return None
    bound = [e for e in verified_entries(data) if _is_human(e) and e.get("body_sha256")]
    if not bound:
        return None
    newest = bound[-1]
    if str(newest["body_sha256"]) == body_sha256(content):
        return None
    return str(newest.get("at") or "?")


@dataclass(frozen=True)
class Violation:
    """One note failing one §11 clause."""
    path: str
    clause: str   # "11.1" | "11.2" | "11.3"
    detail: str


class Census(NamedTuple):
    """One §11 walk: how many notes it reached, and which of them fail a clause.

    The count is the proof the walk reached the notes at all; doctor reads a
    zero as unknown, never as a conformant bundle.
    """
    scanned: int
    violations: list[Violation]


def okf_conformance(vault: Path | str) -> Census:
    """Census a vault against OKF §11. No violations ⇒ the vault IS a bundle.

    Walks the filesystem rather than DRIVER: doctor is routinely handed a
    vault that is not the active one (the init wizard does exactly that),
    and the DRIVER global is bound to the active vault only.

    A path that is not a directory raises instead of reading as an empty,
    conformant bundle: a zero-file scan is an invocation error, never a pass.
    """
    from silica.kernel.recall.paths import ignore_matcher

    from silica.kernel.recall.run_log import DEFAULT_LOG_FILENAME
    from silica.kernel.vault_manifest import load_manifest

    vault = Path(vault)
    if not vault.is_dir():
        raise NotADirectoryError(f"{vault} is not a vault directory")
    ignored = ignore_matcher(vault)
    # Silica's own journal is not a note. §11.3's advice for it reads "rename
    # any `index`/`log` note by hand" — which the user cannot do, because the
    # next run writes it again. Read the write_dir off THIS vault, not the
    # active one: doctor is routinely pointed at a vault it has not opened.
    try:
        journal = f"{load_manifest(vault).write_dir}/{DEFAULT_LOG_FILENAME}".lstrip("/")
    except Exception:
        journal = DEFAULT_LOG_FILENAME
    out: list[Violation] = []
    scanned = 0
    for f in sorted(vault.rglob("*.md")):
        parts = f.relative_to(vault).parts
        if any(p.startswith(".") for p in parts):
            continue  # .obsidian, .trash, .silica
        if any(ignored(p) for p in parts[:-1]):
            continue  # .silicaignore / NOISE_DIRS: node_modules under a repo vault
        rel = f.relative_to(vault).as_posix()
        if rel == journal:
            continue
        scanned += 1
        if f.stem.lower() in RESERVED_NAMES:
            out.append(Violation(rel, "11.3", f"reserved note name `{f.stem}` — rename by hand"))
        try:
            data, raw, _body = frontmatter.split(f.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            out.append(Violation(rel, "11.1", "unreadable file"))
            continue
        # split() returns raw=None for "no block at all" and data=None for
        # "block present, YAML broken" — different repairs, so different lines.
        if raw is None:
            out.append(Violation(rel, "11.1", "no frontmatter block"))
        elif data is None:
            out.append(Violation(rel, "11.1", "frontmatter is not parseable YAML"))
        elif not str(data.get("type") or "").strip():
            out.append(Violation(rel, "11.2", "missing `type`"))
    return Census(scanned, out)
