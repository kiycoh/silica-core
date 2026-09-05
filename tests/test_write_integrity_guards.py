"""Guards on the write path's failure reporting.

Three separate lies the executor used to tell:
  - commit_note_atomic reported reverted=True even when the restore silently
    skipped (no snapshot content), leaving the rejected body on disk;
  - _execute_overwrite read a dead read channel as "note absent", which drops
    the user's frontmatter and disarms the conflict gate;
  - Ledger.__init__ raised on a pre-unique-index ledger.db holding duplicates,
    aborting every run for that vault.
"""
from __future__ import annotations

import sqlite3
import time

import pytest

from silica.driver import get_driver
from silica.driver.base import Txn
from silica.kernel.write.atomic_write import commit_note_atomic
from silica.kernel.write.bulk import execute_one
from silica.kernel.write.ledger import Ledger
from silica.kernel.write.ops import InverseOp, InverseOpKind, Op, OpType


def _patch_op(path: str, snippet: str = "body") -> Op:
    return Op(op=OpType.patch, heading="H", source_basename="src.md",
              path=path, snippet=snippet, hub="Hub")


# ---------------------------------------------------------------------------
# G1 — a revert that could not restore must not report reverted=True
# ---------------------------------------------------------------------------

def test_skipped_restore_does_not_report_reverted(tmp_vault, monkeypatch):
    """silica_restore skips a restore_version with prior_content=None (build_txn
    could read neither the note nor its mirror seed) with only a warning — it
    never lands in errors. reverted=True there claims the note was left
    untouched while the lint-rejected body is still on disk."""
    target = tmp_vault.note("Areas/Roadmap.md", "---\n---\nseed\n")

    blind_txn = Txn(
        id="txn_test",
        refs=[],
        created_paths=[],
        inverses=[InverseOp(kind=InverseOpKind.restore_version,
                            path=target, prior_content=None)],
    )
    monkeypatch.setattr("silica.tools.wrapped.build_txn", lambda ops: blind_txn)

    # Fail only once the patch has appended its block, so the violation is newly
    # introduced (a pre-existing one is tolerated by the patch baseline).
    def fake_lint(note_name, op_type="", hub=""):
        from silica.driver import DRIVER
        introduced = "[^" in DRIVER.read_note(note_name).content
        return {"success": not introduced, "errors": ["bad link"] if introduced else []}
    monkeypatch.setattr("silica.tools.composed.silica_lint", fake_lint)

    res = commit_note_atomic(_patch_op(target), lint=True)

    assert res.ok is False
    assert res.reverted is False, "claimed a revert the restore never performed"
    assert "revert failed" in (res.error or "")
    # The proof the report was a lie: the rejected body is still on disk.
    assert "body" in tmp_vault.read(target)


def test_successful_restore_still_reports_reverted(tmp_vault, monkeypatch):
    """The honest case must stay honest: a restore with snapshot content
    restores the note and reverted=True."""
    target = tmp_vault.note("Areas/Roadmap.md", "---\n---\nseed\n")
    original = tmp_vault.read(target)

    def fake_lint(note_name, op_type="", hub=""):
        from silica.driver import DRIVER
        introduced = "[^" in DRIVER.read_note(note_name).content
        return {"success": not introduced, "errors": ["bad link"] if introduced else []}
    monkeypatch.setattr("silica.tools.composed.silica_lint", fake_lint)

    res = commit_note_atomic(_patch_op(target), lint=True)

    assert res.ok is False
    assert res.reverted is True
    assert "revert failed" not in (res.error or "")
    assert tmp_vault.read(target) == original


# ---------------------------------------------------------------------------
# G2 — only a genuine "not found" means "no prior note"
# ---------------------------------------------------------------------------

_USER_FRONTMATTER = (
    "---\n"
    "tags: [research]\n"
    "aliases: [Roadmap 2026]\n"
    "verified: true\n"
    "---\n\n"
    "# Roadmap\n\nUser prose.\n"
)


def test_broken_read_channel_does_not_stomp_the_note(tmp_vault, monkeypatch):
    """A ws bridge error is not "absent": prior=None would delete the user's
    frontmatter (ensure_system_floor builds a minimal block) and skip the
    concurrent-edit callout. The op must fail so it is deferred instead."""
    target = tmp_vault.note("Areas/Roadmap.md", _USER_FRONTMATTER)
    driver = get_driver()

    def dead_channel(ref):
        raise RuntimeError("ws bridge: connection reset by peer")
    monkeypatch.setattr(driver, "read_note", dead_channel)

    op = Op(op=OpType.overwrite, heading="H", source_basename="src.md",
            path=target, content="# Roadmap\n\nIncoming body.\n",
            base_content="# Roadmap\n\nUser prose.\n")

    with pytest.raises(RuntimeError, match="connection reset"):
        execute_one(op)

    on_disk = tmp_vault.read(target)
    assert "tags: [research]" in on_disk
    assert "verified: true" in on_disk
    assert "Incoming body." not in on_disk


def test_not_found_read_is_still_treated_as_absent(tmp_vault, monkeypatch):
    """The narrowed catch must keep the genuine absent case working: a
    "File not found" read means there is no prior note, so the write proceeds
    with the minimal floor rather than failing the op."""
    target = tmp_vault.note("Areas/Roadmap.md", _USER_FRONTMATTER)
    driver = get_driver()
    real_read = driver.read_note
    calls = {"n": 0}

    def flaky(ref):
        calls["n"] += 1
        if calls["n"] == 1:  # the prior-content read; the post-write verify reads for real
            raise RuntimeError(f"File not found: {ref}")
        return real_read(ref)
    monkeypatch.setattr(driver, "read_note", flaky)

    op = Op(op=OpType.overwrite, heading="H", source_basename="src.md",
            path=target, content="# Roadmap\n\nIncoming body.\n")
    res = execute_one(op)

    assert res["success"] is True
    assert "Incoming body." in tmp_vault.read(target)


# ---------------------------------------------------------------------------
# G3 — a legacy ledger.db with duplicate rows must not abort the run
# ---------------------------------------------------------------------------

def _legacy_ledger(path, rows) -> None:
    """Write a pre-unique-index ledger.db (old column name, no content_hash)."""
    conn = sqlite3.connect(str(path))
    conn.execute("""
        CREATE TABLE ops (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_id           TEXT NOT NULL,
            source_basename  TEXT NOT NULL,
            path             TEXT,
            op               TEXT NOT NULL,
            status           TEXT NOT NULL,
            ts               REAL NOT NULL
        )
    """)
    for txn_id, source, note_path, status, ts in rows:
        conn.execute(
            "INSERT INTO ops(txn_id, source_basename, path, op, status, ts) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (txn_id, source, note_path, "write", status, ts),
        )
    conn.commit()
    conn.close()


def test_duplicate_rows_do_not_abort_ledger_init(tmp_path):
    now = time.time()
    db = tmp_path / "ledger.db"
    _legacy_ledger(db, [
        ("t1", "src.md", "Notes/A.md", "failed", now),
        ("t2", "src.md", "Notes/A.md", "committed", now + 1),
        ("t3", "other.md", "Notes/B.md", "committed", now + 2),
    ])

    ledger = Ledger(db)  # used to raise sqlite3.IntegrityError out of __init__

    rows = ledger._conn.execute(
        "SELECT txn_id, status FROM ops WHERE path='Notes/A.md'"
    ).fetchall()
    assert rows == [("t2", "committed")], "the newest row per key must survive"
    assert ledger._conn.execute("SELECT COUNT(*) FROM ops").fetchone()[0] == 2
    ledger.close()


def test_deduplicated_ledger_upserts(tmp_path):
    """De-duplicating is what makes the UPSERT contract hold afterwards."""
    now = time.time()
    db = tmp_path / "ledger.db"
    _legacy_ledger(db, [
        ("t1", "src.md", "Notes/A.md", "committed", now),
        ("t2", "src.md", "Notes/A.md", "committed", now + 1),
    ])

    ledger = Ledger(db)
    ledger.record("t9", "src.md", "Notes/A.md", "write", "committed", content_hash="h")

    rows = ledger._conn.execute(
        "SELECT txn_id, content_hash FROM ops WHERE path='Notes/A.md'"
    ).fetchall()
    assert rows == [("t9", "h")]
    ledger.close()
