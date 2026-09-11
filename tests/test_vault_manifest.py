"""vault.yaml manifest (ADR-0014): declared capabilities, not vault types."""
import subprocess

import pytest

from silica.config import CONFIG
from silica.kernel.vault_manifest import (
    apply_manifest_to_config,
    get_active_manifest,
    load_manifest,
    reset_manifest_cache,
)


@pytest.fixture(autouse=True)
def _fresh_cache():
    reset_manifest_cache()
    yield
    reset_manifest_cache()


def test_defaults_prose_only_outside_git(tmp_path):
    m = load_manifest(tmp_path)
    assert m.sources == ("prose",)
    assert m.cooccurrence_lang is None


def test_defaults_include_code_inside_git(tmp_path):
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    vault = tmp_path / ".silica"
    vault.mkdir()
    assert load_manifest(vault).sources == ("prose", "code", "notebook")


def test_manifest_file_overrides_defaults(tmp_path):
    (tmp_path / "vault.yaml").write_text(
        "sources: [prose]\ncooccurrence_lang: italian\n",
        encoding="utf-8",
    )
    m = load_manifest(tmp_path)
    assert m.sources == ("prose",)
    assert m.cooccurrence_lang == "italian"


def test_malformed_manifest_degrades_to_defaults(tmp_path):
    (tmp_path / "vault.yaml").write_text("sources: 42\n", encoding="utf-8")
    assert load_manifest(tmp_path).sources == ("prose",)
    (tmp_path / "vault.yaml").write_text(":\n  - not yaml mapping [", encoding="utf-8")
    assert load_manifest(tmp_path).sources == ("prose",)


def test_get_active_manifest_caches_until_reset(tmp_path, monkeypatch):
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    assert get_active_manifest().sources == ("prose",)
    (tmp_path / "vault.yaml").write_text("sources: [prose, code]\n", encoding="utf-8")
    assert get_active_manifest().sources == ("prose",)  # cached
    reset_manifest_cache()
    assert get_active_manifest().sources == ("prose", "code")


def test_apply_manifest_defaults_to_auto_without_manifest(tmp_path, monkeypatch):
    """Regression: vault with no vault.yaml at all must land on the config-level
    "auto" default (per-store detection), not the dead "english" fallback."""
    monkeypatch.delenv("SILICA_COOCCURRENCE_LANG", raising=False)
    monkeypatch.delenv("SILICA_LANG", raising=False)
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "lang", "english")
    apply_manifest_to_config()
    assert CONFIG.lang == "auto"


def test_apply_manifest_defaults_to_auto_when_manifest_omits_field(tmp_path, monkeypatch):
    """Regression: vault.yaml present but without `cooccurrence_lang:` must also
    land on "auto", not "english"."""
    (tmp_path / "vault.yaml").write_text("sources: [prose]\n", encoding="utf-8")
    monkeypatch.delenv("SILICA_COOCCURRENCE_LANG", raising=False)
    monkeypatch.delenv("SILICA_LANG", raising=False)
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "lang", "english")
    apply_manifest_to_config()
    assert CONFIG.lang == "auto"


def test_apply_manifest_env_var_wins_over_auto_default(tmp_path, monkeypatch):
    """Env var precedence must survive the bug fix: it still wins even when the
    manifest declares nothing."""
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "lang", "french")
    monkeypatch.setenv("SILICA_COOCCURRENCE_LANG", "french")
    apply_manifest_to_config()
    assert CONFIG.lang == "french"


def test_apply_manifest_env_wins(tmp_path, monkeypatch):
    (tmp_path / "vault.yaml").write_text(
        "cooccurrence_lang: italian\n", encoding="utf-8"
    )
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "lang", "english")

    monkeypatch.delenv("SILICA_COOCCURRENCE_LANG", raising=False)
    monkeypatch.delenv("SILICA_LANG", raising=False)
    apply_manifest_to_config()
    assert CONFIG.lang == "italian"

    reset_manifest_cache()
    monkeypatch.setattr(CONFIG, "lang", "french")
    monkeypatch.setenv("SILICA_COOCCURRENCE_LANG", "french")
    apply_manifest_to_config()
    assert CONFIG.lang == "french"  # env precedence


def test_apply_manifest_clears_lang_on_switch_to_plain_vault(tmp_path, monkeypatch):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    (tmp_path / "a" / "vault.yaml").write_text(
        "cooccurrence_lang: italian\n", encoding="utf-8"
    )
    monkeypatch.delenv("SILICA_COOCCURRENCE_LANG", raising=False)
    monkeypatch.delenv("SILICA_LANG", raising=False)
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path / "a"))
    monkeypatch.setattr(CONFIG, "lang", "english")
    apply_manifest_to_config()
    assert CONFIG.lang == "italian"

    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path / "b"))
    reset_manifest_cache()
    apply_manifest_to_config()
    assert CONFIG.lang == "auto"  # not leaked from vault a; "auto" is the real default




def test_apply_manifest_turns_code_on_when_sources_declare_it(tmp_path, monkeypatch):
    (tmp_path / "vault.yaml").write_text("sources: [prose, code]\n", encoding="utf-8")
    monkeypatch.delenv("SILICA_INDEX_CODE", raising=False)
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "index_code", False)
    apply_manifest_to_config()
    assert CONFIG.index_code is True


def test_apply_manifest_keeps_a_prose_vault_prose_only(tmp_path, monkeypatch):
    (tmp_path / "vault.yaml").write_text("sources: [prose]\n", encoding="utf-8")
    monkeypatch.delenv("SILICA_INDEX_CODE", raising=False)
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "index_code", True)
    apply_manifest_to_config()
    assert CONFIG.index_code is False


def test_apply_manifest_no_manifest_plain_folder_stays_prose_only(tmp_path, monkeypatch):
    monkeypatch.delenv("SILICA_INDEX_CODE", raising=False)
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "index_code", True)
    apply_manifest_to_config()
    assert CONFIG.index_code is False


def test_apply_manifest_index_code_env_wins(tmp_path, monkeypatch):
    (tmp_path / "vault.yaml").write_text("sources: [prose]\n", encoding="utf-8")
    monkeypatch.setenv("SILICA_INDEX_CODE", "1")
    monkeypatch.setattr(CONFIG, "vault_path", str(tmp_path))
    monkeypatch.setattr(CONFIG, "index_code", True)
    apply_manifest_to_config()
    assert CONFIG.index_code is True
