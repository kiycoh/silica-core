# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Where SILICA_VAULT came from decides whether it pins the vault.

Exported in the real environment = deliberate pin, outranks cwd. The same name
in a .env = config, loses to cwd. The two are indistinguishable once anything
calls load_dotenv, and litellm does at import time, so the capture has to sit
in silica/__init__.py, ahead of every third-party import. Subprocesses because
the whole point is import-time ordering.
"""
import os
import subprocess
import sys

_PROBE = "import silica.cli, silica.config; print(silica.config.VAULT_PINNED)"


def _pinned(cwd, env) -> str:
    full = {k: v for k, v in os.environ.items() if k != "SILICA_VAULT"}
    full.update(env)
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=cwd, env=full,
        capture_output=True, text=True, timeout=300,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    return out.stdout.strip().splitlines()[-1]


def test_dotenv_vault_does_not_pin(tmp_path):
    (tmp_path / ".env").write_text(f"SILICA_VAULT={tmp_path}\n", encoding="utf-8")
    assert _pinned(tmp_path, {}) == "False"


def test_exported_vault_pins(tmp_path):
    (tmp_path / ".env").write_text(f"SILICA_VAULT={tmp_path}\n", encoding="utf-8")
    assert _pinned(tmp_path, {"SILICA_VAULT": str(tmp_path)}) == "True"
