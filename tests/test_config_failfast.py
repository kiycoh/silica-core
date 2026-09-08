"""Model resolution: an exported provider key answers for SILICA_MODEL, and a
bare environment still fails fast rather than guessing a hosted model."""
from __future__ import annotations

import pytest

from silica.config import HOSTED_PROVIDERS, SilicaConfig, model_from_env


@pytest.fixture
def bare_env(monkeypatch):
    """No model, no provider pin, no provider key anywhere."""
    monkeypatch.delenv("SILICA_MODEL", raising=False)
    monkeypatch.delenv("SILICA_PROVIDER", raising=False)
    for key_env, _ in HOSTED_PROVIDERS.values():
        monkeypatch.delenv(key_env, raising=False)


def test_model_defaults_to_empty(bare_env):
    assert SilicaConfig().model == ""
    assert model_from_env() == ("", "")


def test_empty_model_provider_falls_back_to_lmstudio(bare_env):
    assert SilicaConfig().provider == "lmstudio"


def test_exported_key_answers_for_an_unset_model(bare_env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    model, source = model_from_env()
    assert source == "OPENROUTER_API_KEY"
    assert model == HOSTED_PROVIDERS["openrouter"][1][0]
    assert SilicaConfig().provider == "openrouter"


def test_chain_order_is_first_key_wins(bare_env, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
    assert model_from_env()[1] == "GROQ_API_KEY"
    monkeypatch.setenv("GEMINI_API_KEY", "g-test")
    assert model_from_env()[1] == "GEMINI_API_KEY"  # gemini precedes groq


def test_explicit_model_wins_over_the_chain(bare_env, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("SILICA_MODEL", "qwen3-30b")
    assert model_from_env() == ("qwen3-30b", "SILICA_MODEL")


def test_pinned_provider_stands_the_chain_down(bare_env, monkeypatch):
    """A custom endpoint with a stray hosted key must not get a hosted model."""
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-test")
    monkeypatch.setenv("SILICA_PROVIDER", "custom")
    assert model_from_env() == ("", "")


