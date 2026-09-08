# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""Tool registry: the @tool decorator, the TOOLS dict, and the JSON schema
each tool's annotated signature becomes. The MCP server serves a named
slice of TOOLS; the CLI calls the same functions directly."""
from __future__ import annotations

import inspect
import json
import logging
from typing import Any, Callable, get_type_hints

from pydantic import BaseModel, create_model

logger = logging.getLogger(__name__)


# Schema keys whose VALUE is a name -> subschema map, not a schema node. Their
# keys are field names the model has to see; everything else is an annotation.
_SCHEMA_MAPS = frozenset({"properties", "patternProperties", "$defs", "definitions"})


def _strip_titles(node: Any, *, in_map: bool = False) -> Any:
    """Compact a JSON Schema for the wire: the whole toolset is re-sent on
    every iteration of the agent loop, so annotation-only keys are paid per
    request. Three lossless passes, applied at any depth:

    - drop every annotation `title` (restates the field name in Title Case,
      ~600 tokens on the chat toolset). A `title` sitting inside `properties`
      is a real PARAMETER, not an annotation, and survives: dropping it left
      `required: ["title", ...]` naming a field the model could not see, so a
      strict validator rejected the call and a lenient one omitted the value
      (silica_event_create, silica_write_note, silica_graph_export);
    - collapse the Optional pattern `anyOf: [X, {"type": "null"}]` to X —
      omitting an optional field already means null, so the null branch says
      nothing. A real union (two live branches) is left alone;
    - drop `"default": null` for the same reason; informative defaults
      (5, "", false) survive.
    """
    if isinstance(node, dict):
        if in_map:
            return {k: _strip_titles(v) for k, v in node.items()}
        out = {}
        for k, v in node.items():
            if k == "title" or (k == "default" and v is None):
                continue
            out[k] = _strip_titles(v, in_map=k in _SCHEMA_MAPS)
        any_of = out.get("anyOf")
        if isinstance(any_of, list) and len(any_of) == 2:
            live = [m for m in any_of if m != {"type": "null"}]
            if len(live) == 1 and isinstance(live[0], dict):
                del out["anyOf"]
                # Parent keys (description, default) win over the branch's.
                out = {**live[0], **out}
        return out
    if isinstance(node, list):
        return [_strip_titles(v) for v in node]
    return node


class Tool:
    """Metadata and executor for a single registered tool."""

    __slots__ = ("fn", "name", "description", "params_model", "cls", "collapse", "sensitive", "internal")

    def __init__(
        self,
        fn: Callable,
        name: str,
        description: str,
        params_model: type[BaseModel],
        cls: str,
        collapse: str = "lazy",
        sensitive: bool = False,
        internal: bool = False,
    ):
        self.fn = fn
        self.name = name
        self.description = description
        self.params_model = params_model
        self.cls = cls  # "atomic" | "composed" | "wrapped"
        self.collapse = collapse  # "lazy" | "eager" | "never"
        # classified once at definition; the default toolset filters
        # on this so a sensitive tool can never leak into the main agent by
        # mere registration. New sensitive tools are covered automatically.
        self.sensitive = sensitive
        # Pipeline internals the FSM drives programmatically (recon, bulk_write,
        # snapshot, ...): registered for dispatch but hidden from the main
        # agent's default toolset — same filter seam as `sensitive`. Still
        # reachable when named in AgentConstraints.tools.
        self.internal = internal

    def json_schema(self) -> dict:
        """Return the OpenAI-compatible function schema for this tool."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": _strip_titles(self.params_model.model_json_schema()),
            },
        }

    def run(self, _cancel_token: Any = None, _progress: Any = None, **kwargs: Any) -> str:
        """Validate args via pydantic, then execute the tool function.

        `_cancel_token` and `_progress` are injected by the agent loop and
        forwarded to the underlying function only when it declares the matching
        parameter (`cancel_token` / `progress`). Neither is part of the params
        model / JSON schema. `progress` is what lets a tool running a loop of its
        own — silica_web_answer — draw its steps into the UI the outer loop is
        already streaming to, instead of showing one opaque spinning row.
        Always returns a JSON string — either the result or an error.
        """
        try:
            validated = self.params_model(**kwargs)
            call_kwargs = validated.model_dump()
            injected = {"cancel_token": _cancel_token, "progress": _progress}
            if any(v is not None for v in injected.values()):
                sig = inspect.signature(self.fn)
                for param, value in injected.items():
                    if value is not None and param in sig.parameters:
                        call_kwargs[param] = value
            result = self.fn(**call_kwargs)
            # Ensure result is always a string
            if isinstance(result, str):
                return result
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception as e:
            logger.exception("Tool %s execution error: %s", self.name, e)
            return json.dumps(
                {"error": f"{type(e).__name__}: {e}"}, ensure_ascii=False
            )


# Global tool registry — the single source of truth
TOOLS: dict[str, Tool] = {}


def _model_from_signature(fn: Callable) -> type[BaseModel]:
    """Build the tool input model from its annotated public parameters."""
    hints = get_type_hints(fn, include_extras=True)
    fields: dict[str, tuple[Any, Any]] = {}
    for name, param in inspect.signature(fn).parameters.items():
        if name in {"cancel_token", "progress"}:
            continue
        if param.kind in {param.VAR_POSITIONAL, param.VAR_KEYWORD}:
            raise TypeError(f"Tool {fn.__name__} cannot expose variadic parameter {name!r}")
        annotation = hints.get(name, param.annotation)
        if annotation is inspect.Parameter.empty:
            raise TypeError(f"Tool {fn.__name__} parameter {name!r} needs a type annotation")
        default = ... if param.default is inspect.Parameter.empty else param.default
        fields[name] = (annotation, default)
    return create_model(f"{fn.__name__}Args", **fields)  # type: ignore[call-overload]


def tool(
    params_model: type[BaseModel] | None = None,
    cls: str = "atomic",
    collapse: str = "lazy",
    sensitive: bool = False,
    internal: bool = False,
):
    """Decorator that registers a function as a Silica tool.

    Usage:
        @tool(cls="atomic")
        def silica_read_note(name: str):
            '''Read a vault note by name (wikilink-style resolution).'''
            return DRIVER.read_note(name)
    """

    def decorator(fn: Callable) -> Callable:
        tool_name = fn.__name__
        # cleandoc, not strip: a docstring's continuation lines carry the source
        # indentation, and every "\n    " is its own token in a description sent
        # on every request.
        tool_desc = inspect.cleandoc(fn.__doc__ or "")
        model = params_model or _model_from_signature(fn)
        TOOLS[tool_name] = Tool(
            fn, tool_name, tool_desc, model, cls,
            collapse=collapse, sensitive=sensitive,
            internal=internal,
        )
        logger.debug("Registered tool: %s (class=%s, collapse=%s)", tool_name, cls, collapse)
        return fn

    return decorator
