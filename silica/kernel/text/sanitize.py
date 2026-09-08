# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

import json
import logging
import re

logger = logging.getLogger(__name__)

# Inline ($...$) must not cross newlines; block ($$...$$) may.
_MATH = re.compile(r"\$\$.*?\$\$|\$[^\n$]+?\$", re.DOTALL)


def replace_outside_math(text: str, old: str, new: str) -> str:
    """`text.replace(old, new)` everywhere EXCEPT inside `$...$` / `$$...$$` spans.

    Lets the distiller post-processor turn double-escaped prose newlines into real
    ones without shredding `\\nabla`/`\\neq` or splitting inline math.
    """
    out: list[str] = []
    last = 0
    for m in _MATH.finditer(text):
        out.append(text[last:m.start()].replace(old, new))
        out.append(m.group(0))  # math span: verbatim
        last = m.end()
    out.append(text[last:].replace(old, new))
    return "".join(out)


# Matches [[any/path/to/Note.md]] or [[Note.md]] (with optional #anchor and |alias)
_MD_EXT_WIKILINK_RE = re.compile(
    r'\[\[([^\]#|]+?)\.md((?:#[^\]#|]*)?)(\|[^\]]*)?\]\]',
    re.IGNORECASE,
)

# Characters illegal in filesystem filenames
_ILLEGAL_FILENAME_CHARS_RE = re.compile(r'[/\\:*?"<>|]')

# Degenerate run: 5+ consecutive identical characters (LLM output garbage).
# Excludes markdown-structural chars (# = - * _ ` ~): they legitimately repeat
# — ATX headings up to ######, thematic breaks / setext underlines, emphasis,
# code fences — and collapsing them corrupts real document structure (the golden
# integrity probe caught `##### Heading` → `# Heading` on nucleate).
# Also excludes digits (\d): they are data, not garbage — "100000" is a number,
# not a degenerate run, and collapsing it silently corrupts the value.
_DEGENERATE_RUN_RE = re.compile(r'([^\n\d#*_=~`-])\1{4,}')

# Nested wikilink brackets: 3+ consecutive '[' or ']'. A valid wikilink uses
# exactly two; 3+ only appears when an already-bracketed name was wrapped again
# (e.g. the distiller emits "[[X]]" and a renderer makes "[[[[X]]]]"). Single and
# double brackets — including code like x[[1]] — are left untouched.
_NESTED_WIKILINK_RE = re.compile(r'\[{3,}|\]{3,}')


# An inline code span holding nothing but a line break: `\n` written as the
# subject of a sentence, emitted as a real break instead. Nobody writes that on
# purpose, so it is corruption recognisable without asking the model anything.
# Blockquote markers count as nothing: a break mid-quote resumes with `> `.
# Deliberately not "any newline inside a code span" — over the 718-note vault
# that reading pairs 375 unrelated backticks and welds real lines together.
_CODE_SPAN_NEWLINE_RE = re.compile(r"`[^\S\n]*\n[ \t]*(?:>[ \t]*)*`")


def repair_code_span_newlines(text: str) -> str:
    """Turn a code span that contains only a line break back into `\\n`."""
    return _CODE_SPAN_NEWLINE_RE.sub(lambda _m: "`\\n`", text)


# A run of doubled backslashes in text that never travelled through JSON:
# there is no escaping to do out there, so the doubling is the model
# over-escaping the very sequence it was told to copy verbatim (measured on
# the body pass: 12 bodies out of 12). Whole runs, not just `\\` before a
# letter: a model that leaves `\pmb` alone still writes a LaTeX row break
# `\\` as `\\\\`, because THAT is the sequence it reads as an escape — 25
# quadrupled row breaks and 23 `\\{`/`\\}` landed in the vault on 2026-08-05,
# the doubling that survived the body-appendix path once JSON decoding was no
# longer there to halve it for free.
# With the source in hand the decision is per-site: a run the source itself
# contains (a real `\\` row break, prose genuinely discussing double escaping)
# is the model copying faithfully — kept. Any other run is over-escape —
# halved. Without a source only the unambiguous sites go: 4+ backslashes,
# which LaTeX has no construct for, and a doubling glued to a letter or brace.
# ponytail: the needle is searched across the WHOLE source, so a doubling
# that excerpt A contains preserves an identical over-escape in a body drawn
# from excerpt B of the same chunk — per-op excerpt attribution if that ever
# bites.
_OVER_ESCAPED_RE = re.compile(r"(?<!\\)(?:\\\\)+(?!\\)")
# What follows the run is half the needle: the whole word for a macro
# (`\\pmb` vs `\pmb`), otherwise the single next character — a row break's
# trailing space is exactly what tells a faithful `\\ ` apart from an
# over-escaped `\ `.
_AFTER_RE = re.compile(r"[A-Za-z]+|.", re.DOTALL)
_UNAMBIGUOUS_AFTER_RE = re.compile(r"[A-Za-z{}]")


def collapse_over_escaped_backslashes(text: str, source: str | None = None) -> str:
    """`\\\\top` -> `\\top`, `\\\\\\\\` -> `\\\\` in text that was never JSON-escaped.

    With `source`, halve only the runs whose doubled form (the run + the
    following word, or its single next character) the source does not itself
    contain."""
    def _site(m: re.Match) -> str:
        run = m.group(0)
        after = _AFTER_RE.match(m.string, m.end())
        tail = after.group(0) if after else ""
        if source is None:
            keep = len(run) == 2 and not _UNAMBIGUOUS_AFTER_RE.match(tail)
        else:
            keep = (run + tail) in source
        return run if keep else "\\" * (len(run) // 2)

    return _OVER_ESCAPED_RE.sub(_site, text)


def collapse_nested_wikilinks(text: str) -> str:
    """Collapse [[[[X]]]] (and deeper) down to a single [[X]] wikilink."""
    return _NESTED_WIKILINK_RE.sub(lambda m: m.group(0)[:2], text)


def strip_degenerate_runs(text: str) -> str:
    """Collapse runs of 5+ identical characters to a single instance.

    Lines are preserved; only in-line repetitions are collapsed.
    """
    return _DEGENERATE_RUN_RE.sub(r'\1', text)


def _strip_md_ext(text: str) -> str:
    """Remove .md extension from inside wikilinks: [[Note.md]] → [[Note]]."""
    return _MD_EXT_WIKILINK_RE.sub(
        lambda m: f"[[{m.group(1)}{m.group(2)}{m.group(3) or ''}]]",
        text,
    )


# Marks an op whose body reached us outside the JSON string — body appendix or
# body pass. Such a body was never escaped, so it needs no unescaping either:
# a `\n` in it is the escape sequence as subject, written on purpose. Set by
# the two producers of external bodies, consumed and removed here.
VERBATIM_BODY = "_verbatim_body"


def normalize_ops(ops: list, *, verbatim_source: str | None = None) -> list:
    """Post-process a list of op dicts to fix common distiller output errors.

    Applied normalizations:
    1. Strip .md extension from wikilinks in `snippet`, `content`, and `related`.
    2. Strip filesystem-illegal characters from `title` when present.
    3. Undo JSON double-escaping in prose — skipped for VERBATIM_BODY ops,
       where the text never travelled through JSON to begin with.

    `verbatim_source` is the chunk's own inbox text; when given, the
    over-escape collapse on VERBATIM_BODY ops anchors per-site on it instead
    of collapsing blanket.
    """
    if not isinstance(ops, list):
        return ops

    cleaned: list = []
    for op in ops:
        if not isinstance(op, dict):
            cleaned.append(op)
            continue
        op = dict(op)  # shallow copy — don't mutate in place
        verbatim = bool(op.pop(VERBATIM_BODY, False))

        for field in ("snippet", "content"):
            if isinstance(op.get(field), str):
                val = op[field]
                val = val.rstrip()
                while not verbatim and val.endswith("\\n"):
                    val = val[:-2].rstrip()
                parts = val.split("```")
                for i in range(0, len(parts), 2):  # prose parts only
                    if verbatim:
                        parts[i] = collapse_over_escaped_backslashes(
                            parts[i], source=verbatim_source)
                    else:
                        if "\\n" in parts[i]:  # never inside math spans
                            parts[i] = replace_outside_math(parts[i], "\\n", "\n")
                        # JSON decoding does not make the text clean: a model
                        # that over-escapes INSIDE the JSON string delivers
                        # `\\dots` / `\\{a_c\\}` after decoding, and nothing
                        # downstream repaired it (8 committed notes, 2026-08-05).
                        # Anchored-only: without a source there is no way to
                        # tell over-escape from a faithful copy, so no-source
                        # callers keep today's behavior.
                        if verbatim_source:
                            parts[i] = collapse_over_escaped_backslashes(
                                parts[i], source=verbatim_source)
                    # After the expansion, never before: the expansion would
                    # turn the repaired `\n` straight back into a line break.
                    parts[i] = repair_code_span_newlines(parts[i])
                val = "```".join(parts)
                val = strip_degenerate_runs(val)
                val = collapse_nested_wikilinks(val)
                op[field] = _strip_md_ext(val)

        if isinstance(op.get("related"), list):
            op["related"] = [
                _strip_md_ext(r) if isinstance(r, str) else r
                for r in op["related"]
            ]

        if isinstance(op.get("title"), str):
            op["title"] = _ILLEGAL_FILENAME_CHARS_RE.sub("", op["title"]).strip()
            if not op["title"]:
                op["title"] = None

        cleaned.append(op)

    return cleaned


# Bodies carried outside the JSON string, keyed by integer ref. Line-anchored,
# non-prose sentinel so distilled markdown/LaTeX won't collide with it.
# Collides only if a body literally contains a `===SILICA-BODY N===` line —
# vanishingly rare, and the split-tolerant variant below already absorbs the
# real-world failure mode.
_BODY_MARKER = re.compile(r"^===SILICA-BODY (\d+)===$", re.MULTILINE)
# Split-tolerant variant: models occasionally garble the sentinel word
# (`===SILICA-BILY 5===`, observed 2026-08-15). A strict-only split then folds
# the garbled marker line AND the whole next body into the PREVIOUS body — the
# one corruption the write gate exists to prevent. Any `===SILICA-<word> N===`
# line is unmistakably a marker attempt, never prose, so splitting on the
# loose form recovers both bodies intact.
_BODY_MARKER_LOOSE = re.compile(r"^===\s*SILICA[-_ ][A-Z]{2,8}\s+(\d+)\s*===$", re.MULTILINE | re.IGNORECASE)


def extract_body_appendix(raw: str) -> tuple[str, dict[int, str]]:
    """Split a `<json>\\n===SILICA-BODY N===\\n<body>...` payload.

    Returns the JSON text (everything before the first marker) and a {ref: body}
    map. Bodies are verbatim — no JSON unescaping ever touches them, so LaTeX
    backslashes survive (`\\top` stays `\\top`, never decodes to a TAB). No
    markers → (raw, {}), i.e. legacy single-blob JSON output is untouched.

    Splitting is loose (garbled sentinel words still split, see
    `_BODY_MARKER_LOOSE`) but ACTIVATION is strict: at least one exact
    `===SILICA-BODY N===` marker must be present, so ordinary prose that
    happens to contain a `===SILICA-… N===`-shaped line can never flip a
    legacy single-blob payload into appendix mode.
    """
    if not _BODY_MARKER.search(raw):
        return raw, {}
    markers = list(_BODY_MARKER_LOOSE.finditer(raw))
    if not markers:
        return raw, {}
    json_text = raw[: markers[0].start()]
    bodies: dict[int, str] = {}
    for i, m in enumerate(markers):
        start = m.end()
        if raw[start : start + 1] == "\n":
            start += 1  # drop the newline ending the marker line
        end = markers[i + 1].start() if i + 1 < len(markers) else len(raw)
        body = raw[start:end]
        if body.endswith("\n"):
            body = body[:-1]  # drop the newline preceding the next marker
        bodies[int(m.group(1))] = body
    return json_text, bodies


def _resolve_op_refs(op, bodies: dict[int, str]) -> None:
    if not isinstance(op, dict):
        return
    for ref_key, field in (("snippet_ref", "snippet"), ("content_ref", "content")):
        if ref_key in op:
            ref = op.pop(ref_key)
            if isinstance(ref, int) and ref in bodies:
                op[field] = bodies[ref]
                op[VERBATIM_BODY] = True
            else:
                # Dangling ref: the model emitted `snippet_ref: N` but never wrote
                # the matching `===SILICA-BODY N===` block. Leaving `field` empty
                # here silently produces a 0-char snippet that validate later
                # rejects as "too short" with no clue why — surface it instead.
                logger.warning(
                    "sanitize: dangling %s=%r for op %r (available bodies: %s) — "
                    "%s left empty",
                    ref_key, ref, op.get("path") or op.get("heading") or "?",
                    sorted(bodies), field,
                )


def _inject_external_bodies(parsed, bodies: dict[int, str]) -> None:
    """Replace `snippet_ref`/`content_ref` ints with their external body string."""
    if isinstance(parsed, list):
        ops = parsed
    elif isinstance(parsed, dict) and isinstance(parsed.get("updates"), list):
        ops = parsed["updates"]
    elif isinstance(parsed, dict):
        ops = [parsed]
    else:
        return
    for op in ops:
        _resolve_op_refs(op, bodies)


class TruncatedArray(ValueError):
    """A JSON array was cut mid-stream, but its leading objects were recovered.

    Raised by parse_json instead of the bare JSONDecodeError when the payload
    looks like an op array whose tail is missing (max_tokens truncation). The
    complete leading objects are in `.ops`, the unrecoverable remainder in
    `.tail`. Subclassing ValueError keeps every existing `except Exception`
    call-site failing exactly as before — partial data is only ever used by
    callers that catch TruncatedArray by name.
    """

    def __init__(self, ops: list, tail: str):
        super().__init__(
            f"truncated JSON array: recovered {len(ops)} complete leading "
            f"objects, {len(tail)} chars unrecoverable"
        )
        self.ops = ops
        self.tail = tail


def _salvage_array(text: str) -> tuple[list, str] | None:
    """Recover the complete leading `{...}` objects of a truncated JSON array.

    String-aware scan (a `}` inside a string value never closes an object).
    Collection stops at the first object that fails to parse on its own — the
    recovered prefix is always a clean prefix of the intended array. Returns
    (objects, unconsumed_tail), or None when nothing was recoverable.
    """
    start = text.find('[')
    if start == -1:
        return None
    ops: list = []
    in_string = escape = False
    depth = 0
    obj_start = -1
    end_of_last = start
    for i in range(start + 1, len(text)):
        ch = text[i]
        if in_string:
            if escape:
                escape = False
            elif ch == '\\':
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch in '{[':
            if depth == 0 and ch == '{':
                obj_start = i
            depth += 1
        elif ch in '}]':
            if depth == 0:
                break  # the array closed by itself; anything further is tail
            depth -= 1
            if depth == 0 and obj_start != -1:
                try:
                    ops.append(json.loads(text[obj_start:i + 1]))
                except json.JSONDecodeError:
                    return (ops, text[obj_start:]) if ops else None
                end_of_last = i
                obj_start = -1
    tail = text[end_of_last + 1:]
    return (ops, tail) if ops else None


def parse_json(raw: str, strict: bool = False):
    raw, _bodies = extract_body_appendix(raw)
    cleaned = raw.strip()
    if cleaned.startswith('\ufeff'):
        cleaned = cleaned[1:]
    
    fence_pattern = re.compile(r'^```(?:json)?\s*\n(.*?)\n```$', re.DOTALL | re.IGNORECASE)
    inner_fence_pattern = re.compile(r'```(?:json)?\s*\n(.*?)\n```', re.DOTALL | re.IGNORECASE)
    
    was_strict_clean = True
    processed = cleaned
    
    m = fence_pattern.match(cleaned)
    if m:
        processed = m.group(1).strip()
        was_strict_clean = False
    else:
        m = inner_fence_pattern.search(cleaned)
        if m:
            processed = m.group(1).strip()
            was_strict_clean = False
            
    parsed = None
    parse_err = None
    try:
        parsed = json.loads(processed)
    except json.JSONDecodeError as e:
        start_idx = -1
        for idx, ch in enumerate(raw):
            if ch in '{[':
                start_idx = idx
                break
        end_idx = -1
        for idx in range(len(raw) - 1, -1, -1):
            if raw[idx] in '}]':
                end_idx = idx
                break
        
        if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
            candidate = raw[start_idx:end_idx+1]
            try:
                parsed = json.loads(candidate)
                was_strict_clean = False
            except json.JSONDecodeError as inner_e:
                parse_err = inner_e
        else:
            parse_err = e

    if parsed is None:
        salvage = _salvage_array(processed)
        if salvage is not None:
            ops, tail = salvage
            if _bodies:
                _inject_external_bodies(ops, _bodies)
            raise TruncatedArray(ops, tail)
        if parse_err is not None:
            raise parse_err
        raise ValueError("JSON Parse Error")

    if strict and not was_strict_clean:
        raise ValueError("Strict mode violation: markdown fences, preambles, or postambles were stripped from the output.")

    if _bodies:
        _inject_external_bodies(parsed, _bodies)

    return parsed, was_strict_clean
