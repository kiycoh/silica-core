"""M4 (docs/specs/epistemic-state.md): a person's attestation enters only
through a command (`/verify`), carries the hash of the body it vouched for,
and `/stale` reports both an attested note whose body moved on and a
gate-written note edited outside Silica since.
"""
from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest

from silica.kernel.write import frontmatter
from silica.kernel.write.notetype import (
    attestation_drift,
    body_sha256,
    clear_verified,
    is_human_verified,
    stamp_verified,
    verified_entries,
)

NOTE = "---\ndate: 2026-01-01\n---\n\n# T\n\nbody text\n"


def _data(content):
    return frontmatter.split(content)[0]


# --- attestation on the note ---------------------------------------------------

def test_stamp_verified_writes_a_human_entry_with_the_body_hash():
    out = stamp_verified(NOTE, "human:kiycoh", "2026-09-04")
    entries = verified_entries(_data(out))
    assert entries == [{"by": "human:kiycoh", "at": "2026-09-04", "body_sha256": body_sha256(NOTE)}]
    assert is_human_verified(_data(out))
    assert frontmatter.split(out)[2] == frontmatter.split(NOTE)[2]  # the body is untouched


def test_stamp_verified_refuses_a_non_human_actor():
    with pytest.raises(ValueError):
        stamp_verified(NOTE, "silica:gate", "2026-09-04")


def test_stamp_verified_appends_to_an_existing_mapping_entry():
    prior = "---\nverified:\n  by: pipeline:x\n  at: 2026-01-01\n---\n\nbody\n"
    out = stamp_verified(prior, "human:me", "2026-09-04")
    bys = [e["by"] for e in verified_entries(_data(out))]
    assert bys == ["pipeline:x", "human:me"]


def test_stamp_verified_creates_frontmatter_on_a_bare_note():
    out = stamp_verified("# T\n\nplain\n", "human:me", "2026-09-04")
    assert is_human_verified(_data(out))
    assert out.endswith("# T\n\nplain\n")


def test_clear_verified_drops_only_human_entries():
    content = stamp_verified(
        "---\nverified:\n  - by: pipeline:x\n    at: 2026-01-01\n---\n\nbody\n", "human:me", "2026-09-04")
    out = clear_verified(content)
    assert [e["by"] for e in verified_entries(_data(out))] == ["pipeline:x"]
    assert "verified" not in (_data(clear_verified(stamp_verified(NOTE, "human:me", "2026-09-04"))) or {})


def test_attestation_drift_names_the_stale_attestation():
    attested = stamp_verified(NOTE, "human:me", "2026-09-04")
    assert attestation_drift(attested) is None
    edited = attested.replace("body text", "body text, edited by hand")
    assert attestation_drift(edited) == "2026-09-04"
    # an entry written by hand before this convention carries no hash: unknowable, not stale
    legacy = "---\nverified:\n  by: human:me\n  at: 2026-01-01\n---\n\nbody\n"
    assert attestation_drift(legacy) is None


# --- edited since Silica wrote it --------------------------------------------

def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def test_edited_since_write_compares_journal_and_checkpoints(tmp_vault):
    from silica.config import CONFIG
    from silica.kernel.write.checkpoints import get_checkpoint_store
    from silica.kernel.write.ops import InverseOp, InverseOpKind
    from silica.kernel.write.undo_journal import edited_since_write, get_undo_journal

    vault = CONFIG.vault_path
    written = "---\nAI: true\n---\n\ngate wrote this\n"
    for name in ("same", "hand", "tool"):
        tmp_vault.note(f"c/{name}.md", written)
    journal = get_undo_journal()
    run = journal.start_run("lez.md", vault=vault)
    for name in ("same", "hand", "tool"):
        journal.record(run, InverseOp(kind=InverseOpKind.delete_created, path=f"c/{name}.md"),
                       _sha(written))
    # hand: edited outside Silica. tool: edited through a Silica tool write.
    (Path(vault) / "c/hand.md").write_text(written + "\nadded by hand\n", encoding="utf-8")
    tool_new = written + "\nadded by silica_patch_note\n"
    (Path(vault) / "c/tool.md").write_text(tool_new, encoding="utf-8")
    get_checkpoint_store().push("c/tool.md", written, tool_new)

    assert edited_since_write(vault) == ["c/hand.md"]


def test_edited_since_write_ignores_other_vaults_and_reverted_runs(tmp_vault):
    from silica.config import CONFIG
    from silica.kernel.write.ops import InverseOp, InverseOpKind
    from silica.kernel.write.undo_journal import edited_since_write, get_undo_journal

    vault = CONFIG.vault_path
    tmp_vault.note("c/a.md", "x\n")
    journal = get_undo_journal()
    other = journal.start_run("lez.md", vault="/elsewhere")
    journal.record(other, InverseOp(kind=InverseOpKind.delete_created, path="c/a.md"), _sha("y\n"))
    reverted = journal.start_run("lez.md", vault=vault)
    journal.record(reverted, InverseOp(kind=InverseOpKind.delete_created, path="c/a.md"), _sha("y\n"))
    journal.mark_reverted(reverted)
    assert edited_since_write(vault) == []


# --- the command and the report ----------------------------------------------

def test_verify_command_writes_human_entry_and_a_checkpoint(tmp_vault, monkeypatch, capsys):
    from silica.cli import _handle_direct_shortcut
    from silica.kernel.write.checkpoints import get_checkpoint_store

    monkeypatch.setattr("getpass.getuser", lambda: "kiycoh")
    tmp_vault.note("c/n.md", NOTE)
    assert _handle_direct_shortcut("/verify c/n", []) is True
    from silica.config import CONFIG
    content = (Path(CONFIG.vault_path) / "c/n.md").read_text(encoding="utf-8")
    assert [e["by"] for e in verified_entries(_data(content))] == ["human:kiycoh"]
    assert get_checkpoint_store().depth("c/n.md") >= 1
    assert "verified" in capsys.readouterr().out
    assert _handle_direct_shortcut("/verify c/n --clear", []) is True
    content = (Path(CONFIG.vault_path) / "c/n.md").read_text(encoding="utf-8")
    assert not is_human_verified(_data(content))


def test_mcp_write_still_refuses_the_verified_key(tmp_vault):
    from silica.tools.notes import silica_write_note

    out = silica_write_note("c/x.md", "body", props={"verified": "human:me"})
    assert "error" in out and "verified" in out["error"]


def _init_repo(path: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.t"], cwd=path, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=path, check=True)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "c0"], cwd=path, check=True)


def test_stale_reports_the_two_epistemic_lanes(tmp_path, monkeypatch, capsys):
    from silica.cli import _handle_direct_shortcut
    from silica.config import CONFIG
    from silica.kernel.write.ops import InverseOp, InverseOpKind
    from silica.kernel.write.undo_journal import get_undo_journal

    _init_repo(tmp_path)
    vault = tmp_path / "docs"
    vault.mkdir()
    monkeypatch.setattr(CONFIG, "vault_path", str(vault))
    attested = stamp_verified(NOTE, "human:me", "2026-09-04").replace("body text", "changed")
    (vault / "attested.md").write_text(attested, encoding="utf-8")
    written = "---\nAI: true\n---\n\ngate\n"
    (vault / "hand.md").write_text(written + "edit\n", encoding="utf-8")
    journal = get_undo_journal()
    run = journal.start_run("lez.md", vault=str(vault))
    journal.record(run, InverseOp(kind=InverseOpKind.delete_created, path="hand.md"), _sha(written))

    assert _handle_direct_shortcut("/stale", []) is True
    out = capsys.readouterr().out
    assert "attested.md" in out and "2026-09-04" in out
    assert "hand.md" in out and "edited" in out.lower()


def test_stale_never_says_all_clear_above_a_stale_lane(tmp_path, monkeypatch, capsys):
    """Manual pass 2026-09-04: the code lane printed "No stale docs" and the
    epistemic lane listed a changed attestation right under it. One all-clear,
    printed only when every lane is empty."""
    from silica.cli import _handle_direct_shortcut
    from silica.config import CONFIG

    _init_repo(tmp_path)
    vault = tmp_path / "docs"
    vault.mkdir()
    monkeypatch.setattr(CONFIG, "vault_path", str(vault))
    attested = stamp_verified(NOTE, "human:me", "2026-09-04").replace("body text", "changed")
    (vault / "attested.md").write_text(attested, encoding="utf-8")

    assert _handle_direct_shortcut("/stale", []) is True
    out = capsys.readouterr().out
    assert "attested.md" in out
    assert "No stale" not in out and "Nothing stale" not in out

    (vault / "attested.md").write_text(NOTE, encoding="utf-8")
    _handle_direct_shortcut("/stale", [])
    out = capsys.readouterr().out
    assert "Nothing stale" in out and "attested.md" not in out
