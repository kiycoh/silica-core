# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""atomic_write_bytes: overwrite lands, a failed write leaves the old file intact."""
import os

import pytest

from silica.kernel.recall.paths import atomic_write_bytes


def test_write_and_overwrite(tmp_path):
    p = tmp_path / "sub" / "index.json"
    atomic_write_bytes(p, b"v1")  # creates parent dirs
    atomic_write_bytes(p, b"v2")
    assert p.read_bytes() == b"v2"
    assert list(p.parent.iterdir()) == [p]  # no tmp leftovers


def test_failed_write_keeps_previous_content(tmp_path, monkeypatch):
    p = tmp_path / "index.json"
    atomic_write_bytes(p, b"good")

    def boom(fd):
        raise OSError("disk full")

    monkeypatch.setattr(os, "fsync", boom)
    with pytest.raises(OSError):
        atomic_write_bytes(p, b"torn")
    assert p.read_bytes() == b"good"
    assert list(p.parent.iterdir()) == [p]


def test_write_through_a_symlink_keeps_the_link(tmp_path):
    """The tmp file must land on the destination's filesystem and the write
    must go THROUGH the link — replacing the link with a regular file breaks
    every other reader of the real path."""
    real = tmp_path / "real.json"
    real.write_bytes(b"old")
    link = tmp_path / "link.json"
    link.symlink_to(real)

    atomic_write_bytes(link, b"new")

    assert link.is_symlink()
    assert real.read_bytes() == b"new"


def test_an_existing_destinations_mode_is_preserved(tmp_path):
    """mkstemp creates 0600; the replace must not silently tighten a file
    other readers (editor, sync daemon) already open."""
    dest = tmp_path / "f.bin"
    dest.write_bytes(b"x")
    dest.chmod(0o640)

    atomic_write_bytes(dest, b"y")

    assert (dest.stat().st_mode & 0o777) == 0o640


def test_a_new_destination_gets_the_umask_default(tmp_path):
    """There is no destination mode to preserve on a create, and mkstemp's 0600
    is nobody's intent: notes are the user's own files, so a syncer or a second
    account must keep the access an ordinary write would have granted."""
    reference = tmp_path / "reference.bin"
    reference.write_bytes(b"x")  # whatever this process's umask yields

    dest = tmp_path / "new.bin"
    atomic_write_bytes(dest, b"y")

    assert (dest.stat().st_mode & 0o777) == (reference.stat().st_mode & 0o777)
