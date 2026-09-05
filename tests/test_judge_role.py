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


def test_judge_role_falls_back_to_the_router_model():
    from silica.agent.providers import get_provider

    assert get_provider(_cfg(), role="judge").model == get_provider(_cfg(), role="router").model


def test_judge_role_uses_its_own_model_when_set():
    from silica.agent.providers import get_provider

    p = get_provider(_cfg(judge_provider="lmstudio", judge_model="judge-small"), role="judge")
    assert p.model.endswith("judge-small")
    assert p.model != get_provider(_cfg(), role="router").model


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


def test_residue_judge_calls_the_judge_model(monkeypatch):
    from silica import config as cfg_mod
    from silica.kernel import residue

    seen = {}

    def fake_call_llm(model, messages, **kw):
        seen["model"] = model
        return types.SimpleNamespace(text="1: yes", finish_reason="stop", completion_tokens=3)

    monkeypatch.setattr("silica.agent.llm.call_llm", fake_call_llm)
    monkeypatch.setattr(cfg_mod.CONFIG, "model", "writer")
    monkeypatch.setattr(cfg_mod.CONFIG, "judge_model", "judge-small")
    residue._llm("prompt", 10)
    assert seen["model"] == "judge-small"
    monkeypatch.setattr(cfg_mod.CONFIG, "judge_model", "")
    residue._llm("prompt", 10)
    assert seen["model"] == "writer"


def test_dedup_judge_asks_for_the_judge_role_only_when_configured(monkeypatch):
    from silica.capabilities import dedup

    seen = []

    class _Resp:
        text = ""
        content = ""

    def fake_get_provider(config, role="router"):
        seen.append(role)
        return types.SimpleNamespace(call_llm=lambda **kw: (_ for _ in ()).throw(RuntimeError("stop")))

    monkeypatch.setattr("silica.agent.providers.get_provider", fake_get_provider)
    for cfg in (_cfg(), _cfg(judge_model="judge-small", judge_provider="lmstudio")):
        try:
            dedup._decide_dedup(cfg, concept="c", excerpt="excerpt",
                                candidate_name="cand", candidate_body="body")
        except Exception:
            pass
    assert seen == ["worker", "judge"]
