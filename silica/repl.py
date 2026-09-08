# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""`silica repl` — the optional reference harness: a free agent loop over the
same tools the MCP server serves, with an OpenAI-compatible chat model.

The model, its endpoint and its key belong to this surface alone: nothing
else in the package needs them, and `silica mcp` runs without them. No
ingestion state machine, no memory lane, no capture: a turn is the model
calling tools until it answers.

ponytail: one HTTP client, SSE parsed by hand, readline for line editing.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Any, Callable

from silica.config import CONFIG

HOSTED = {  # provider prefix -> (base url, key env var)
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "deepseek": ("https://api.deepseek.com", "DEEPSEEK_API_KEY"),
    "groq": ("https://api.groq.com/openai/v1", "GROQ_API_KEY"),
    "mistral": ("https://api.mistral.ai/v1", "MISTRAL_API_KEY"),
    "xai": ("https://api.x.ai/v1", "XAI_API_KEY"),
    "gemini": ("https://generativelanguage.googleapis.com/v1beta/openai", "GEMINI_API_KEY"),
}
LOCAL = {"lmstudio": "http://localhost:1234/v1", "ollama": "http://localhost:11434/v1"}
MAX_ITERATIONS = 25
TOOL_RESULT_CHARS = 12000
SYSTEM = (
    "You are a research assistant working inside the folder {root}. You have tools that "
    "return located evidence and never answers: silica_search gives ranked passages with "
    "path, section, line, matched_terms, coverage, dominance and terms_absent; read a passage with "
    "silica_read before citing it, and cite path and line. Low coverage or a discriminating "
    "term in terms_absent means the corpus does not answer: say so instead of guessing. "
    "silica_files says what the index skipped. Write a note only when asked."
)


def endpoint() -> tuple[str, str, str]:
    """(model id, base url, api key) from SILICA_MODEL, or ('', '', '') when no model is set.
    `provider/model` picks a preset; SILICA_PROVIDER_BASE_URL overrides the url."""
    spec = (CONFIG.model or "").strip()
    if not spec:
        return "", "", ""
    prefix, _, rest = spec.partition("/")
    base, key = CONFIG.provider_base_url, CONFIG.provider_api_key
    if prefix in HOSTED:
        model = rest
        base = base or HOSTED[prefix][0]
        key = key or os.getenv(HOSTED[prefix][1], "")
    elif prefix in LOCAL:
        model = rest
        base = base or LOCAL[prefix]
        key = key or prefix
    else:
        model = spec
        base = base or LOCAL["lmstudio"]
        key = key or "lm-studio"
    return model, base.rstrip("/"), key


def _post_stream(base: str, key: str, payload: dict):
    """Yield the parsed SSE chunks of one streaming chat completion."""
    import httpx

    headers = {"Authorization": f"Bearer {key}"} if key else {}
    with httpx.Client(timeout=httpx.Timeout(connect=10.0, read=300.0, write=10.0, pool=5.0)) as client:
        with client.stream("POST", f"{base}/chat/completions", headers=headers, json=payload) as r:
            if r.status_code != 200:
                body = r.read().decode("utf-8", "replace")[:400]
                raise RuntimeError(f"{r.status_code} from {base}: {body}")
            for line in r.iter_lines():
                if line.startswith("data: ") and line[6:].strip() != "[DONE]":
                    yield json.loads(line[6:])


def complete(messages: list[dict], tools: list[dict], on_delta: Callable[[str], None], *,
             post=_post_stream) -> dict:
    """One model call: streams content through `on_delta`, returns the
    assistant message with any tool_calls assembled from the deltas."""
    model, base, key = endpoint()
    payload = {"model": model, "messages": messages, "stream": True}
    if tools:
        payload["tools"] = tools
    content: list[str] = []
    calls: dict[int, dict[str, str]] = {}
    for chunk in post(base, key, payload):
        for choice in chunk.get("choices", []):
            delta = choice.get("delta") or {}
            if delta.get("content"):
                content.append(delta["content"])
                on_delta(delta["content"])
            for tc in delta.get("tool_calls") or []:
                slot = calls.setdefault(tc.get("index", 0), {"id": "", "name": "", "arguments": ""})
                slot["id"] = tc.get("id") or slot["id"]
                fn = tc.get("function") or {}
                slot["name"] = fn.get("name") or slot["name"]
                slot["arguments"] += fn.get("arguments") or ""
    msg: dict[str, Any] = {"role": "assistant", "content": "".join(content) or None}
    if calls:
        msg["tool_calls"] = [{"id": c["id"] or f"call_{i}", "type": "function",
                              "function": {"name": c["name"], "arguments": c["arguments"] or "{}"}}
                             for i, c in sorted(calls.items())]
    return msg


def run_turn(messages: list[dict], registry: dict, on_delta: Callable[[str], None],
             on_tool: Callable[[str, dict, str], None], *, post=_post_stream) -> str:
    """Model and tools alternate until the model answers or the budget ends.
    `messages` is extended in place; the final text is returned."""
    schemas = [t.json_schema() for t in registry.values()]
    for iteration in range(MAX_ITERATIONS):
        msg = complete(messages, schemas, on_delta, post=post)
        messages.append(msg)
        if not msg.get("tool_calls"):
            return msg.get("content") or ""
        for call in msg["tool_calls"]:
            name = call["function"]["name"]
            try:
                args = json.loads(call["function"]["arguments"] or "{}")
            except ValueError:
                args = {}
            tool = registry.get(name)
            result = tool.run(**args) if tool else json.dumps({"error": f"unknown tool {name}"})
            on_tool(name, args, result)  # the whole reply, so the summary line reads hit counts
            if len(result) > TOOL_RESULT_CHARS:
                result = result[:TOOL_RESULT_CHARS] + f"\n… truncated at {TOOL_RESULT_CHARS} chars; narrow the call"
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        if iteration == MAX_ITERATIONS - 2:
            messages.append({"role": "user", "content": "Budget nearly spent: answer now with what you have."})
    return messages[-1].get("content") or "" if messages[-1]["role"] == "assistant" else ""


def _tools() -> dict:
    from silica.ui.mcp import exposed_tools
    return exposed_tools(extended=True)


def _describe_call(name: str, args: dict, result: str) -> str:
    arg = ", ".join(f"{k}={str(v)[:40]!r}" for k, v in args.items())
    try:
        d = json.loads(result)
        tail = (f"error {d['error']['code']}" if isinstance(d, dict) and "error" in d else
                f"{len(d['hits'])} hits" if isinstance(d, dict) and "hits" in d else
                f"{d.get('total', '')} entries" if isinstance(d, dict) and "total" in d else
                f"{len(result)} chars")
    except ValueError:
        tail = f"{len(result)} chars"
    return f"  ⏺ {name}({arg}) → {tail}"


def main() -> int:
    model, base, key = endpoint()
    if not model:
        print("silica repl needs a model: export SILICA_MODEL (e.g. openrouter/deepseek/deepseek-chat, "
              "lmstudio/<loaded model>, ollama/<model>) and, for a hosted provider, its key.", file=sys.stderr)
        return 1
    try:
        import readline  # noqa: F401 — line editing and history for input()
    except ImportError:
        pass
    registry = _tools()
    messages: list[dict] = [{"role": "system", "content": SYSTEM.format(root=CONFIG.vault_path)}]
    print(f"silica repl · {model} @ {base} · {len(registry)} tools · /help", file=sys.stderr)
    while True:
        try:
            line = input("\n› ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            return 0
        if not line:
            continue
        if line in ("/exit", "/quit", "/q"):
            return 0
        if line == "/help":
            print("  /tools  /clear  /exit   — anything else goes to the model")
            continue
        if line == "/tools":
            print("  " + "\n  ".join(registry))
            continue
        if line == "/clear":
            messages[1:] = []
            continue
        messages.append({"role": "user", "content": line})
        print()
        try:
            run_turn(messages, registry, lambda s: print(s, end="", flush=True),
                     lambda n, a, r: print("\n" + _describe_call(n, a, r), flush=True))
        except KeyboardInterrupt:
            print("\n  (interrupted)")
        except Exception as e:  # the endpoint's failure is the turn's answer, not a crash
            print(f"\n  error: {e}", file=sys.stderr)
        print()
