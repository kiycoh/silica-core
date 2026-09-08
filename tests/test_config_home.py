"""SILICA_HOME moves the runtime directory; the shell is its only source."""
from __future__ import annotations

from pathlib import Path


def test_default_home_is_dot_silica(monkeypatch):
    from silica.config import _silica_home

    monkeypatch.delenv("SILICA_HOME", raising=False)
    assert _silica_home() == Path.home() / ".silica"


def test_env_moves_the_whole_directory(monkeypatch, tmp_path):
    from silica.config import _silica_home

    monkeypatch.setenv("SILICA_HOME", str(tmp_path / "elsewhere"))
    assert _silica_home() == tmp_path / "elsewhere"


def test_tilde_is_expanded(monkeypatch):
    from silica.config import _silica_home

    monkeypatch.setenv("SILICA_HOME", "~/.silica-core")
    assert _silica_home() == Path.home() / ".silica-core"


def test_blank_falls_back_to_the_default(monkeypatch):
    from silica.config import _silica_home

    monkeypatch.setenv("SILICA_HOME", "   ")
    assert _silica_home() == Path.home() / ".silica"


def test_index_and_env_hang_off_the_same_root():
    """One root, not two: a moved home takes `.env` and `index/` with it.

    conftest rebinds `paths._SILICA_HOME` to a tmp dir for every test, so the
    identity with config.SILICA_HOME is asserted where the module sets it up,
    not on the live value the fixture has already replaced.
    """
    import inspect

    from silica.config import SILICA_HOME, USER_ENV
    from silica.kernel.recall import paths

    assert USER_ENV == SILICA_HOME / ".env"
    assert "_SILICA_HOME = SILICA_HOME" in inspect.getsource(paths)
    assert paths.index_dir_for("") == paths._SILICA_HOME / "index"
    assert paths.inbox_dir_for("/x").parent == paths._SILICA_HOME / "inbox"
