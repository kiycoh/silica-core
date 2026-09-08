# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""codeast.python — Python skeleton walker."""
from __future__ import annotations

from silica.kernel.code.codeast.base import (
    _CALL_NAME, Call, Symbol, _signature, _text, clean_comment_block,
)


def _py_doc_node(node):
    """Bare string node of a body's leading docstring, or None."""
    body = node.child_by_field_name("body")
    if body is None or body.named_child_count == 0:
        return None
    first = body.named_child(0)
    # In tree-sitter >= 0.23 the docstring is a bare 'string' node as first
    # child of the block; older grammars wrap it in expression_statement.
    if first.type == "expression_statement" and first.named_child_count > 0:
        first = first.named_child(0)
    return first if first.type == "string" else None


def _strip_quotes(text: str) -> str:
    for q in ('"""', "'''", '"', "'"):
        if text.startswith(q) and text.endswith(q) and len(text) >= 2 * len(q):
            return text[len(q):-len(q)]
    return text


def _doc_text(string_node, src: bytes) -> str:
    """Whole docstring, quotes stripped, each line stripped, blank edges gone."""
    text = _strip_quotes(_text(string_node, src).strip())
    return "\n".join(line.strip() for line in text.strip().splitlines()).strip()


def _py_docstring_full(node, src: bytes) -> str:
    doc = _py_doc_node(node)
    return _doc_text(doc, src) if doc is not None else ""


def _py_docstring(node, src: bytes) -> str:
    """First line of the body's leading docstring, quotes stripped."""
    full = _py_docstring_full(node, src)
    return full.splitlines()[0].strip() if full else ""


_COMMENT_CAP_LINES = 40  # per file: keeps the digest bounded


def _py_module_docs(root, src: bytes) -> tuple[str, list[str]]:
    """Module-level docstring (whole) and top-level comment blocks. Comments
    group by consecutive source rows; capped at _COMMENT_CAP_LINES per file."""
    module_doc = ""
    # skip leading comments: a licence header sits above the docstring in most
    # real files, and reading named_child(0) alone dropped the single most
    # informative field of the whole digest for every one of them
    for i in range(root.named_child_count):
        first = root.named_child(i)
        if first.type == "comment":
            continue
        if first.type == "expression_statement" and first.named_child_count > 0:
            first = first.named_child(0)
        if first.type == "string":
            module_doc = _doc_text(first, src)
        break
    blocks: list[str] = []
    current: list[str] = []
    last_row = None
    total = 0
    for i in range(root.child_count):
        child = root.child(i)
        if child.type != "comment":
            continue
        row = child.start_point.row
        if last_row is not None and row != last_row + 1 and current:
            blocks.append("\n".join(current))
            current = []
        line = _text(child, src).lstrip("#").strip()
        # noise never spends the budget: the cap is for real commentary
        if clean_comment_block(line) and total < _COMMENT_CAP_LINES:
            current.append(line)
            total += 1
        last_row = row
    if current:
        blocks.append("\n".join(current))
    return module_doc, [b for b in (clean_comment_block(b) for b in blocks) if b]


def _py_decorators(node, src: bytes) -> list[str]:
    """Decorator names of a decorated_definition, '@' and call args stripped."""
    out: list[str] = []
    for i in range(node.named_child_count):
        child = node.named_child(i)
        if child.type == "decorator":
            out.append(_text(child, src).lstrip("@").split("(", 1)[0].strip())
    return out


def _py_dunder_all(root, src: bytes) -> list[str] | None:
    """Literal `__all__` list, or None (absent / dynamic: no authority). This
    grammar emits a bare `assignment` at module level; older ones wrap it in
    `expression_statement`, so both shapes are unwrapped."""
    for i in range(root.named_child_count):
        node = root.named_child(i)
        assign = node
        if node.type == "expression_statement" and node.named_child_count > 0:
            assign = node.named_child(0)
        if assign.type != "assignment":
            continue
        left = assign.child_by_field_name("left")
        right = assign.child_by_field_name("right")
        if left is None or right is None or _text(left, src) != "__all__":
            continue
        if right.type != "list":
            return None
        names: list[str] = []
        for j in range(right.named_child_count):
            el = right.named_child(j)
            if el.type != "string":
                return None
            names.append(_strip_quotes(_text(el, src).strip()))
        return names
    return None


def _py_calls(root, src: bytes) -> list[Call]:
    """Every call site's spelled name, tagged with its top-level container.
    Only grammar-clean names (identifier / dotted attribute) are kept:
    `f().g(...)` and subscripted receivers are skipped by the regex."""
    out: dict[tuple[str, str], int] = {}

    def walk(node, parent: str) -> None:
        if node.type == "call":
            fn = node.child_by_field_name("function")
            if fn is not None:
                text = _text(fn, src)
                if _CALL_NAME.match(text):
                    out.setdefault((text, parent), node.start_point[0] + 1)
        for i in range(node.named_child_count):
            walk(node.named_child(i), parent)

    for i in range(root.named_child_count):
        node = root.named_child(i)
        target = node
        if node.type == "decorated_definition":
            target = node.child_by_field_name("definition") or node
        name = ""
        if target.type in ("function_definition", "class_definition"):
            n = target.child_by_field_name("name")
            name = _text(n, src) if n is not None else ""
        walk(node, name)
    return [Call(name=k[0], parent=k[1], line=v) for k, v in out.items()]


def _py_deferred_imports(root, src: bytes, aliases: dict[str, str]) -> list[str]:
    """Imports that are not direct children of the module root: function-local
    (the deliberate cycle-break idiom), class-body, try/except and
    TYPE_CHECKING guards.

    A module-level walk cannot see them, and a codebase that leans on the
    deferred-import idiom keeps most of its dependency graph there (this repo:
    706 nested vs 306 top-level). Dropping them silently under-counts every
    derived signal — collaborator weights, fan-in hubs, entry points, flow
    sketches, impact — and makes a lazily-imported module look caller-free.
    """
    out: list[str] = []
    sink: list[Symbol] = []

    def walk(node) -> None:
        for i in range(node.named_child_count):
            child = node.named_child(i)
            if child.type in ("import_statement", "import_from_statement"):
                _py_extract(child, src, out, sink, aliases=aliases)
            else:
                walk(child)

    for i in range(root.named_child_count):
        node = root.named_child(i)
        if node.type in ("import_statement", "import_from_statement"):
            continue          # top-level: the module walk already has it
        walk(node)
    return out


def _py_has_main_guard(root, src: bytes) -> bool:
    for i in range(root.named_child_count):
        node = root.named_child(i)
        if node.type == "if_statement":
            cond = node.child_by_field_name("condition")
            if cond is not None and "__name__" in _text(cond, src):
                return True
    return False


_CONST_VALUE_CAP = 70


def _py_constant(node, src: bytes, symbols: list[Symbol]) -> None:
    """Module-level CONSTANT = value. Tuning knobs, thresholds and lookup
    tables are contract, and the comment blocks that explain them were landing
    in the digest with nothing to attach to: a paragraph about an RRF damping
    constant, and no `_RRF_K = 60` anywhere in sight.
    # name + truncated value; the explaining comment stays in the
    # module_comments block rather than being re-homed onto the symbol
    """
    assign = node
    if node.type == "expression_statement" and node.named_child_count > 0:
        assign = node.named_child(0)
    if assign.type != "assignment":
        return
    left = assign.child_by_field_name("left")
    right = assign.child_by_field_name("right")
    if left is None or left.type != "identifier":
        return
    name = _text(left, src)
    if name == "__all__" or not name.lstrip("_").isupper():
        return
    value = " ".join(_text(right, src).split()) if right is not None else ""
    if len(value) > _CONST_VALUE_CAP:
        value = value[:_CONST_VALUE_CAP] + " ..."
    symbols.append(Symbol(kind="constant", name=name,
                          signature=f"{name} = {value}" if value else name,
                          line=node.start_point[0] + 1, end_line=node.end_point[0] + 1))


def _py_extract(node, src: bytes, imports: list[str], symbols: list[Symbol],
                decorators: list[str] | None = None,
                aliases: dict[str, str] | None = None,
                first_line: int = 0) -> None:
    if node.type == "decorated_definition":
        inner = node.child_by_field_name("definition")
        if inner is not None:
            # the declaration starts at its first decorator: a diff hunk or a
            # traceback that lands on `@route(...)` still names the function
            _py_extract(inner, src, imports, symbols, _py_decorators(node, src), aliases,
                        first_line=node.start_point[0] + 1)
        return
    if node.type == "import_statement":
        for i in range(node.named_child_count):
            child = node.named_child(i)
            if child.type == "dotted_name":
                imports.append(_text(child, src))
            elif child.type == "aliased_import":
                name = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                if name is not None:
                    imports.append(_text(name, src))
                    if alias is not None and aliases is not None:
                        aliases[_text(alias, src)] = _text(name, src)
        return
    if node.type == "import_from_statement":
        module = node.child_by_field_name("module_name")
        if module is None:
            return
        base = _text(module, src)
        sep = "" if base.endswith(".") else "."
        names: list[str] = []
        mstart = module.start_byte
        for i in range(node.named_child_count):
            child = node.named_child(i)
            if child.start_byte == mstart:
                continue  # the module_name node itself
            if child.type == "dotted_name":
                names.append(_text(child, src))
            elif child.type == "aliased_import":
                name = child.child_by_field_name("name")
                alias = child.child_by_field_name("alias")
                if name is not None:
                    names.append(_text(name, src))
                    if alias is not None and aliases is not None:
                        aliases[_text(alias, src)] = f"{base}{sep}{_text(name, src)}"
        if names:
            imports.extend(f"{base}{sep}{n}" for n in names)
        else:
            imports.append(base)  # `from X import *` — bare module
        return
    if node.type in ("expression_statement", "assignment"):
        _py_constant(node, src, symbols)
        return
    if node.type == "function_definition":
        name = node.child_by_field_name("name")
        symbols.append(Symbol(
            kind="function",
            name=_text(name, src) if name is not None else "?",
            signature=_signature(node, src),
            doc=_py_docstring(node, src),
            doc_full=_py_docstring_full(node, src),
            decorators=decorators or [],
            line=first_line or node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        ))
        return
    if node.type == "class_definition":
        name_node = node.child_by_field_name("name")
        cls_name = _text(name_node, src) if name_node is not None else "?"
        symbols.append(Symbol(
            kind="class",
            name=cls_name,
            signature=_signature(node, src),
            doc=_py_docstring(node, src),
            doc_full=_py_docstring_full(node, src),
            decorators=decorators or [],
            line=first_line or node.start_point[0] + 1, end_line=node.end_point[0] + 1,
        ))
        body = node.child_by_field_name("body")
        for i in range(body.named_child_count if body is not None else 0):
            child = body.named_child(i)
            target = child
            method_decos: list[str] = []
            if child.type == "decorated_definition":
                target = child.child_by_field_name("definition") or child
                method_decos = _py_decorators(child, src)
            if target.type == "function_definition":
                mname = target.child_by_field_name("name")
                symbols.append(Symbol(
                    kind="method",
                    name=_text(mname, src) if mname is not None else "?",
                    signature=_signature(target, src),
                    doc=_py_docstring(target, src),
                    doc_full=_py_docstring_full(target, src),
                    parent=cls_name,
                    decorators=method_decos,
                    line=child.start_point[0] + 1, end_line=target.end_point[0] + 1,
                ))
