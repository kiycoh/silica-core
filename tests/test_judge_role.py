"""M7 (docs/specs/epistemic-state.md): the judge (residue coverage, dedup
verdicts) can run on a model other than the writer. Default: unchanged.
"""
from __future__ import annotations

import types


def _cfg(**over):
    base = dict(provider="lmstudio", model="writer", worker_provider="", worker_model="",
                worker_api_key=None, judge_provider="", judge_model="")
    base.update(over)
    return types.SimpleNamespace(**base)






def test_config_reads_judge_model_from_env(monkeypatch):
    monkeypatch.setenv("SILICA_JUDGE_MODEL", "judge-small")
    monkeypatch.setenv("SILICA_JUDGE_PROVIDER", "lmstudio")
    from silica.config import SilicaConfig

    c = SilicaConfig()
    assert c.judge_model.endswith("judge-small") and c.judge_provider == "lmstudio"


def test_config_judge_model_is_none_when_unset(monkeypatch):
    monkeypatch.delenv("SILICA_JUDGE_MODEL", raising=False)
    from silica.config import SilicaConfig

    assert not SilicaConfig().judge_model




