# SPDX-License-Identifier: MIT
"""A conversion child must not outlive the process that spawned it.

mineru holds the GPU for minutes per book. subprocess.run reaps it on timeout
and on error, but nothing reaps it when the batch's own process is killed: the
child is reparented to init and keeps the card (observed 2026-08-16 on the
theology library, cleaned by hand). Reproduced here with `sleep` in place of
mineru, because the failure is in how the child is spawned, not in mineru.
"""
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO = str(Path(__file__).resolve().parents[1])

pytestmark = pytest.mark.skipif(
    sys.platform != "linux", reason="PR_SET_PDEATHSIG is a Linux facility"
)


def _children(pid: int) -> list[int]:
    try:
        raw = Path(f"/proc/{pid}/task/{pid}/children").read_text()
    except OSError:
        return []
    return [int(p) for p in raw.split()]


def _wait_for(predicate, timeout_s: float = 10.0):
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        got = predicate()
        if got:
            return got
        time.sleep(0.05)
    return None


def _alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_a_conversion_child_dies_when_its_parent_is_killed():
    script = (
        f"import subprocess, sys; sys.path.insert(0, {REPO!r});\n"
        "from silica.sources.convert import _REAP_WITH_PARENT\n"
        "print('up', flush=True)\n"
        "subprocess.run(['sleep', '60'], preexec_fn=_REAP_WITH_PARENT)\n"
    )
    parent = subprocess.Popen(
        [sys.executable, "-c", script], stdout=subprocess.PIPE, text=True
    )
    try:
        assert parent.stdout.readline().strip() == "up"
        kids = _wait_for(lambda: _children(parent.pid))
        assert kids, "the child never started"
        child = kids[0]

        parent.kill()
        parent.wait(timeout=10)

        assert _wait_for(lambda: not _alive(child), timeout_s=10), (
            f"pid {child} outlived its killed parent — orphaned on the GPU"
        )
    finally:
        parent.kill()
