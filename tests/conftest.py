# SPDX-License-Identifier: AGPL-3.0-or-later
# Copyright (C) 2026 Alessandro Carosia

"""Suite-wide isolation: no test touches the developer's ~/.silica, real
vault, or the tool registry another test sees."""
from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _restore_tools_registry() -> None:
    """Snapshot the global TOOLS dict around each test: a fake registered by
    a test is dropped afterwards, a real `silica.*` tool that registered
    lazily during the test stays (the import is cached for the session)."""
    from silica.tools import TOOLS
    snapshot = dict(TOOLS)
    yield
    TOOLS.update(snapshot)
    for name in set(TOOLS) - set(snapshot):
        if not getattr(TOOLS[name].fn, "__module__", "").startswith("silica"):
            del TOOLS[name]


@pytest.fixture(autouse=True)
def _isolate_silica_home(tmp_path_factory, monkeypatch: pytest.MonkeyPatch):
    """Every ~/.silica default (index dirs, tmp) lands under a per-test dir."""
    import silica.kernel.recall.paths as paths_mod
    monkeypatch.setattr(paths_mod, "_SILICA_HOME", tmp_path_factory.mktemp("silica-home"))


@pytest.fixture(autouse=True)
def _reset_manifest_cache() -> None:
    import silica.kernel.vault_manifest as manifest_mod
    manifest_mod.reset_manifest_cache()


@pytest.fixture(autouse=True)
def _clear_store_singletons() -> None:
    """Index stores are path-keyed singletons; a test that changes the vault
    must not see the previous test's store."""
    import silica.kernel.recall.lexical as lex
    lex._STORE_CACHE.clear()
    yield
    lex._STORE_CACHE.clear()


@pytest.fixture(autouse=True)
def _isolate_vault_path(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    """CONFIG.vault_path points at a per-test dir by default; `tmp_vault` and
    in-test monkeypatches still win. Not created: tests assert on tmp_path."""
    import silica.config as config_mod
    monkeypatch.setattr(config_mod.CONFIG, "vault_path", str(tmp_path / "isolated_vault"))
    monkeypatch.setattr(config_mod.CONFIG, "embedding_base_url", "")  # the extension is off unless a test turns it on


@pytest.fixture(scope="session")
def synthetic_vault() -> Path:
    from tests.fixtures.vault_factory import _resolve_root, build_synthetic_vault
    return build_synthetic_vault(_resolve_root())


@pytest.fixture
def tmp_vault(tmp_path, monkeypatch):
    """A temporary filesystem vault: .note(rel, content) -> abs path,
    .read(path), .write(path, content)."""
    import silica.config
    import silica.driver

    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    monkeypatch.setattr(silica.config.CONFIG, "vault_path", str(vault_dir))
    silica.driver._driver = None

    class _VaultHelper:
        root = vault_dir

        def note(self, rel: str, content: str = "") -> str:
            p = vault_dir / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return str(p)

        def read(self, path: str) -> str:
            return Path(path).read_text(encoding="utf-8")

        def write(self, path: str, content: str) -> None:
            Path(path).write_text(content, encoding="utf-8")

    yield _VaultHelper()
    silica.driver._driver = None
