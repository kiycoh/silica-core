# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""codeast.ts — TypeScript / JavaScript skeleton walker.

Parity with the Python and Java lanes: JSDoc, module comments, call sites,
import aliases and the exported-name set. Without them a TS repo got
signatures and nothing else — no prose to ground the wiki on, no call edges,
so no flow sketches, no collaborator call weights and no entry points.
"""
from __future__ import annotations

from silica.kernel.code.codeast.base import (
    _CALL_NAME, Call, ModuleSkeleton, Symbol, _block_comment_text, _signature,
    _text, clean_comment_block,
)

_COMMENT_CAP_LINES = 40  # per file, mirrors the Python lane


def _doc(node, src: bytes) -> str:
    """JSDoc/block comment immediately above `node`, "" when absent. Comments
    are siblings in this grammar, so adjacency is the only attachment rule."""
    prev = node.prev_named_sibling
    if prev is None or prev.type != "comment":
        return ""
    text = _text(prev, src)
    if text.startswith("/*"):
        return _block_comment_text(prev, src)
    return clean_comment_block(text.lstrip("/").strip())


def _first_line(doc: str) -> str:
    return doc.splitlines()[0].strip() if doc else ""


def _import_names(node, src: bytes, aliases: dict[str, str], module: str) -> None:
    """Local name -> module specifier for every binding an import introduces.
    # a renamed import ({a as b}) resolves on the local name; the
    # callee then reads as `b`, which is what the call site spells anyway
    """
    for i in range(node.named_child_count):
        clause = node.named_child(i)
        if clause.type != "import_clause":
            continue
        for j in range(clause.named_child_count):
            spec = clause.named_child(j)
            if spec.type == "identifier":                      # default import
                aliases[_text(spec, src)] = module
            elif spec.type == "namespace_import":              # * as ns
                for k in range(spec.named_child_count):
                    if spec.named_child(k).type == "identifier":
                        aliases[_text(spec.named_child(k), src)] = module
            elif spec.type == "named_imports":
                for k in range(spec.named_child_count):
                    item = spec.named_child(k)
                    if item.type != "import_specifier":
                        continue
                    local = item.child_by_field_name("alias") or \
                        item.child_by_field_name("name")
                    if local is not None:
                        aliases[_text(local, src)] = module


def _export_names(node, src: bytes, aliases: dict[str, str] | None,
                  exported: list[str] | None, module: str) -> None:
    """Names that `export {a} from './m'` puts on this module's surface. They
    have no definition here, so the alias table is the only thing that can tell
    codegraph where they live; `export * from` names nothing and stays a plain
    import edge."""
    for i in range(node.named_child_count):
        clause = node.named_child(i)
        locals_: list = []
        if clause.type == "namespace_export":                   # * as ns
            locals_ = [c for k in range(clause.named_child_count)
                       if (c := clause.named_child(k)).type == "identifier"]
        elif clause.type == "export_clause":
            for k in range(clause.named_child_count):
                spec = clause.named_child(k)
                if spec.type == "export_specifier":
                    local = spec.child_by_field_name("alias") or \
                        spec.child_by_field_name("name")
                    if local is not None:
                        locals_.append(local)
        for n in locals_:
            name = _text(n, src)
            if exported is not None:
                exported.append(name)
            if aliases is not None:
                aliases.setdefault(name, module)


def _calls(root, src: bytes) -> list[Call]:
    """Every call site's spelled name, tagged with its top-level container."""
    out: dict[tuple[str, str], None] = {}

    def walk(node, parent: str) -> None:
        if node.type in ("call_expression", "new_expression"):
            fn = node.child_by_field_name("function") or \
                node.child_by_field_name("constructor")
            if fn is not None:
                text = _text(fn, src)
                if _CALL_NAME.match(text):
                    out[(text, parent)] = None
        for i in range(node.named_child_count):
            walk(node.named_child(i), parent)

    for i in range(root.named_child_count):
        node = root.named_child(i)
        target = node
        if node.type == "export_statement":
            target = node.child_by_field_name("declaration") or node
        name = ""
        if target.type in ("function_declaration", "class_declaration",
                           "abstract_class_declaration"):
            n = target.child_by_field_name("name")
            name = _text(n, src) if n is not None else ""
        elif target.type == "lexical_declaration":
            for j in range(target.named_child_count):
                dec = target.named_child(j)
                if dec.type == "variable_declarator":
                    n = dec.child_by_field_name("name")
                    name = _text(n, src) if n is not None else ""
                    break
        walk(node, name)
    return [Call(name=k[0], parent=k[1]) for k in out]


def _module_docs(root, src: bytes) -> tuple[str, list[str]]:
    """Leading block comment as the module doc, remaining top-level comment
    blocks as commentary — the shape the Python lane already produces."""
    module_doc = ""
    blocks: list[str] = []
    total = 0
    for i in range(root.named_child_count):
        node = root.named_child(i)
        if node.type != "comment":
            continue
        text = _text(node, src)
        cleaned = (_block_comment_text(node, src) if text.startswith("/*")
                   else clean_comment_block(text.lstrip("/").strip()))
        if not cleaned:
            continue
        if not module_doc and i == 0 and text.startswith("/*"):
            module_doc = cleaned
            continue
        if total < _COMMENT_CAP_LINES:
            blocks.append(cleaned)
            total += len(cleaned.splitlines())
    return module_doc, blocks


def _class_members(node, src: bytes, cls_name: str, symbols: list[Symbol]) -> None:
    body = node.child_by_field_name("body")
    for i in range(body.named_child_count if body is not None else 0):
        child = body.named_child(i)
        if child.type != "method_definition":
            continue
        mname = child.child_by_field_name("name")
        doc = _doc(child, src)
        symbols.append(Symbol(
            kind="method",
            name=_text(mname, src) if mname is not None else "?",
            signature=_signature(child, src),
            doc=_first_line(doc), doc_full=doc, parent=cls_name,
        ))


def _ts_extract(node, src: bytes, imports: list[str], symbols: list[Symbol],
                aliases: dict[str, str] | None = None,
                exported: list[str] | None = None,
                doc_node=None) -> None:
    """One top-level node. `exported` collects the module's public names, the
    TS answer to `__all__`: not exported means module-private, which is the
    distinction the digest's doc budget runs on."""
    if node.type == "export_statement":
        source = node.child_by_field_name("source")
        if source is not None:      # `export { a } from './m'` — a re-export
            module = _text(source, src).strip("\"'")
            imports.append(module)
            _export_names(node, src, aliases, exported, module)
            return
        decl = node.child_by_field_name("declaration")
        if decl is not None:
            before = len(symbols)
            _ts_extract(decl, src, imports, symbols, aliases, exported,
                        doc_node=node)
            if exported is not None:
                exported.extend(s.name for s in symbols[before:] if not s.parent)
        return
    if node.type == "import_statement":
        source = node.child_by_field_name("source")
        if source is not None:
            module = _text(source, src).strip("\"'")
            imports.append(module)
            if aliases is not None:
                _import_names(node, src, aliases, module)
        return
    doc = _doc(doc_node if doc_node is not None else node, src)
    if node.type == "function_declaration":
        name = node.child_by_field_name("name")
        symbols.append(Symbol(
            kind="function",
            name=_text(name, src) if name is not None else "?",
            signature=_signature(node, src),
            doc=_first_line(doc), doc_full=doc,
        ))
        return
    if node.type == "lexical_declaration":
        # `const f = () => {}` is the dominant TS idiom: without it the walker
        # sees an empty module wherever arrow functions replace declarations
        for i in range(node.named_child_count):
            dec = node.named_child(i)
            if dec.type != "variable_declarator":
                continue
            name = dec.child_by_field_name("name")
            value = dec.child_by_field_name("value")
            if name is None:
                continue
            is_fn = value is not None and value.type in (
                "arrow_function", "function_expression", "function")
            symbols.append(Symbol(
                kind="function" if is_fn else "constant",
                name=_text(name, src),
                signature=_signature(dec, src) if is_fn else _text(name, src),
                doc=_first_line(doc), doc_full=doc,
            ))
        return
    if node.type in ("class_declaration", "abstract_class_declaration"):
        name_node = node.child_by_field_name("name")
        cls_name = _text(name_node, src) if name_node is not None else "?"
        symbols.append(Symbol(kind="class", name=cls_name,
                              signature=_signature(node, src),
                              doc=_first_line(doc), doc_full=doc))
        _class_members(node, src, cls_name, symbols)
        return
    if node.type in ("interface_declaration", "type_alias_declaration",
                     "enum_declaration"):
        name_node = node.child_by_field_name("name")
        symbols.append(Symbol(
            kind="type",
            name=_text(name_node, src) if name_node is not None else "?",
            signature=_signature(node, src).split("=")[0].strip(),
            doc=_first_line(doc), doc_full=doc,
        ))
        return


def extract(root, src: bytes, path: str, language: str) -> ModuleSkeleton:
    imports: list[str] = []
    symbols: list[Symbol] = []
    aliases: dict[str, str] = {}
    exported: list[str] = []
    for i in range(root.named_child_count):
        _ts_extract(root.named_child(i), src, imports, symbols, aliases, exported)
    module_doc, module_comments = _module_docs(root, src)
    return ModuleSkeleton(
        path=path, language=language, imports=imports, symbols=symbols,
        module_doc=module_doc, module_comments=module_comments,
        dunder_all=exported or None, calls=_calls(root, src),
        import_aliases=aliases,
    )
