"""The reference harness: endpoint resolution and the tool loop, with a fake model."""
from __future__ import annotations

import json

import pytest

from silica import repl


def _sse(*msgs):
    """A fake streaming post: each call yields the chunks of the next canned message."""
    calls = iter(msgs)

    def post(base, key, payload):
        msg = next(calls)
        if isinstance(msg, str):
            for piece in (msg[: len(msg) // 2], msg[len(msg) // 2:]):
                yield {"choices": [{"delta": {"content": piece}}]}
        else:
            name, args = msg
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": name, "arguments": json.dumps(args)[:5]}}]}}]}
            yield {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": json.dumps(args)[5:]}}]}}]}
    return post


@pytest.fixture
def model(monkeypatch):
    from silica.config import CONFIG
    monkeypatch.setattr(CONFIG, "model", "lmstudio/test-model")
    monkeypatch.setattr(CONFIG, "provider_base_url", "")
    monkeypatch.setattr(CONFIG, "provider_api_key", "")


def test_endpoint_presets(monkeypatch, model):
    from silica.config import CONFIG
    assert repl.endpoint() == ("test-model", "http://localhost:1234/v1", "lmstudio")
    monkeypatch.setattr(CONFIG, "model", "openrouter/deepseek/deepseek-chat")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    assert repl.endpoint() == ("deepseek/deepseek-chat", "https://openrouter.ai/api/v1", "k")
    monkeypatch.setattr(CONFIG, "model", "")
    assert repl.endpoint() == ("", "", "")


def test_turn_calls_the_tool_then_answers(model):
    seen = []

    class Tool:
        def json_schema(self):
            return {"type": "function", "function": {"name": "silica_search", "parameters": {}}}

        def run(self, **kw):
            seen.append(kw)
            return json.dumps({"hits": [{"path": "a.md"}]})

    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "q"}]
    deltas, tools = [], []
    answer = repl.run_turn(messages, {"silica_search": Tool()}, deltas.append,
                           lambda n, a, r: tools.append((n, a)),
                           post=_sse(("silica_search", {"query": "lsm compaction"}), "found it in a.md"))
    assert answer == "found it in a.md" and "".join(deltas) == answer
    assert seen == [{"query": "lsm compaction"}] and tools == [("silica_search", {"query": "lsm compaction"})]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "tool", "assistant"]
    assert messages[2]["tool_calls"][0]["function"]["arguments"] == '{"query": "lsm compaction"}'


def test_unknown_tool_is_an_error_result_not_a_crash(model):
    messages = [{"role": "user", "content": "q"}]
    answer = repl.run_turn(messages, {}, lambda s: None, lambda n, a, r: None,
                           post=_sse(("nope", {}), "ok"))
    assert answer == "ok" and json.loads(messages[2]["content"])["error"] == "unknown tool nope"


def test_repl_without_a_model_says_so(monkeypatch, capsys):
    from silica.config import CONFIG
    monkeypatch.setattr(CONFIG, "model", "")
    assert repl.main() == 1
    assert "SILICA_MODEL" in capsys.readouterr().err
