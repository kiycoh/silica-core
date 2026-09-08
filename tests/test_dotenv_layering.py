# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Which .env silica reads, and which it must not.

`~/.silica/.env` is the user's own file and the only ambient one. A `.env` in
the working directory belongs to whatever repository the shell happens to sit
in, so it has no provenance: it must not be able to repoint the model, the
endpoints, the vault or anything else. The real environment still outranks the
file, because an exported key is a deliberate per-invocation pin.

Subprocesses because the layering happens once, at import time, and because a
`.env` only proves anything when a real interpreter really starts inside it.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

# Read back through os.environ, not through CONFIG: load_dotenv writes there,
# so this catches a key that arrived even when no field happens to expose it.
_PROBE = (
    "import json, os, silica.config as c; "
    "print(json.dumps({k: os.getenv(k) for k in "
    "('SILICA_WORKER_MODEL', 'SILICA_EMBEDDING_SERVE_CMD')} "
    "| {'worker_model': c.CONFIG.worker_model}))"
)

HOSTILE = "hostile/model"
CMD = "touch /tmp/silica-should-never-run"


def _boot(cwd: Path, home: Path, env: dict[str, str] | None = None,
          probe: str = _PROBE) -> dict:
    """Start silica in `cwd` with `home` as the user's home, and report the env.

    Every SILICA_* the test runner carries is stripped: pytest itself imported
    silica.config from the checkout, so the developer's own .env is already in
    os.environ and would reach the child as a real export, outranking the files
    this test is about.
    """
    full = {k: v for k, v in os.environ.items() if not k.startswith("SILICA_")}
    full["HOME"] = str(home)
    full.update(env or {})
    out = subprocess.run(
        [sys.executable, "-c", probe], cwd=cwd, env=full,
        capture_output=True, text=True, timeout=300,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return json.loads(out.stdout.strip().splitlines()[-1])


def _user_env(home: Path, body: str) -> None:
    (home / ".silica").mkdir(parents=True, exist_ok=True)
    (home / ".silica" / ".env").write_text(body, encoding="utf-8")


def test_a_dotenv_in_the_working_directory_is_not_read(tmp_path):
    """The hostile-checkout case: cd into a repo, run silica, nothing of its."""
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(), home.mkdir()
    (project / ".env").write_text(
        f"SILICA_WORKER_MODEL={HOSTILE}\nSILICA_EMBEDDING_SERVE_CMD={CMD}\n",
        encoding="utf-8",
    )

    seen = _boot(project, home)

    assert seen["SILICA_WORKER_MODEL"] is None
    assert seen["SILICA_EMBEDDING_SERVE_CMD"] is None
    assert HOSTILE not in (seen["worker_model"] or "")


def test_the_user_level_dotenv_is_read(tmp_path):
    """The layer that stays. Without this the test above could pass broken."""
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(), home.mkdir()
    _user_env(home, "SILICA_WORKER_MODEL=mine/model\n")

    assert _boot(project, home)["SILICA_WORKER_MODEL"] == "mine/model"


def test_an_exported_key_still_outranks_the_user_dotenv(tmp_path):
    """override=False: what the shell exported is a deliberate pin."""
    project, home = tmp_path / "project", tmp_path / "home"
    project.mkdir(), home.mkdir()
    _user_env(home, "SILICA_WORKER_MODEL=mine/model\n")

    seen = _boot(project, home, {"SILICA_WORKER_MODEL": "exported/model"})

    assert seen["SILICA_WORKER_MODEL"] == "exported/model"


# litellm calls load_dotenv() at its own import, long after config.py has
# decided which files silica reads. Importing the module that owns litellm is
# the only honest way to catch it.
_PROBE_VIA_LITELLM = (
    "import silica.agent.llm, json, os; "
    "print(json.dumps({k: os.getenv(k) for k in "
    "('SILICA_WORKER_MODEL', 'SILICA_EMBEDDING_SERVE_CMD')} | {'worker_model': ''}))"
)




