---
name: silica
description: Search local documents for cited passages, compare sources, or investigate whether the corpus supports a claim. With code indexing enabled, use first to locate a behavior or implementation when its symbol name is unknown. Use grep for exact strings or known symbol names; read directly when the relevant path and section are already known.
---

# Silica: located evidence, no model

The `silica-core` MCP server indexes the folder the session was opened in and
serves five tools named `silica_*`. If they are deferred, load them with
ToolSearch. If they are missing, say so and give the install line:

```bash
uv tool install 'silica-core[mcp]' && silica setup claude   # or codex, cursor, zed, … (silica setup --list)
```

## The loop

1. **Search for the concept or behavior.** When its location is unknown,
   start with `silica_search`: a concise natural-language question or
   description belongs in `query`. Keep known names and identifiers exact;
   put additional identifier groups in `queries` when useful (one call,
   fused by rank). Use grep for an exact string or symbol name, and read
   directly when the relevant path and section are already known. Search
   returns ranked passages: `path`, `section`, `line`, `score` (raw BM25,
   comparable within one call only), `matched_terms`, `coverage`, and the
   `documents` ranking behind the hits. When the server has section vectors
   the dense leg runs by itself, so a paraphrase the
   corpus words do not reach can still land; `dense` in the reply says
   `ready` and how many documents it covered, or why it did not run, and
   that is not yours to fix — search lexically and say so.
2. **Judge the passages, using lexical signals as diagnostics.**
   `terms_absent` lists query words the corpus never contains; `coverage`
   is the share of the query's rare-term
   mass a hit carries. Neither proves relevance or absence. A dense hit
   can answer the question with `coverage` 0; inspect its text rather than
   rejecting it at a lexical threshold. If the passages provide no useful
   evidence, rephrase once using vocabulary from plausible hits, preserving
   names, numbers and identifiers. Narrow by `folder` when the scope is
   known. If evidence is still insufficient, report what remains unfound;
   do not infer that the corpus contains no answer. `scope` counts what the
   search could see: `unconverted` or `failed` above zero means a relevant
   document may never rank, so check `silica_files(status=…)` before
   calling absence; with a `folder`, `terms_absent_in_scope` is the zero of
   that folder, not of the corpus. `index.state = cold` means nothing was
   indexed yet; the first search builds the index. `stale` with `pending`
   means the reply came from an index that many documents behind — the
   newest files may be missing from it; say so if the question is about
   recent material.
3. **Read further only when evidence is incomplete.** A returned passage
   that supports the claim with enough context can be cited by path and
   line without another call. If it is cut off, ambiguous or missing the
   relevant context, use `silica_read(path, section=…)` or
   `(path, start, end)`. Pass the hit's `version` as `expect_version` when
   reading, so changed content is refused instead of silently mixed with
   old evidence. Stop retrieving once the question is supported; an answer
   about several files needs evidence for each part.
4. **Check corpus completeness when the task is exhaustive.** `silica_files` lists
   every file with what the index did to it; `status=unconverted` names
   the scans and office files with no extracted text, `status=failed` the
   ones that could not be read. Top-k does not certify coverage. A PDF with
   a text layer is indexed as it is, one section per page: a hit in one
   reads `p. 7`, and `silica_read` serves that page alone. Its
   `extract_path` is the extracted text on disk — grep it with your own
   tools, then read the page you need; do not pull a whole PDF into the
   conversation to look for one line.
5. **Write only when asked.** `silica_write_note(path, body)` writes one
   note as given and lints it. Nothing is captured or summarised for you;
   undo is git.

## Compare sources

For "what do these documents say about X", when a reader will check the
answer:

1. State the question and the selection rule in a line each: which
   documents count (a folder, a date, a keyword), which do not.
2. Search the concept; add alternative phrasings when needed to cover the
   selection rule. In each reply,
   `candidates` counts the documents that matched at all, `documents` is
   the head of that ranking (the first k, plus the documents the hits came
   from) and `scope` what the search could see. Keep all three: they bound
   what was considered, and nothing in a reply is a roster of what was
   read.
3. Inspect every passage you will cite. Search windows count as evidence
   when sufficient; fetch missing context with the hit's `version` as
   `expect_version`.
4. Answer as a table, one row per claim: claim, path, section or line,
   version. Below it, the contradictions between sources, and what the
   search did not establish: the queries, their `terms_absent`, and the
   `scope` counts. Absent terms alone do not prove silence, even when all
   files were indexed.
5. Write the file only when asked. The queries and the versions in the
   table are the record; rerun them to check whether the corpus still
   supports a row.

A source the folder lacks comes in through the harness's own tools (web
search, a download), saved into the corpus and searched like the rest.
Name what was saved: the full text, an abstract, or a report someone
derived from it; they do not cite the same. A `.md` beside a PDF was
converted from it, and reading either path reports `source_state`:
`stale` when the PDF changed after the conversion, `unverifiable` when the
sidecar records no hash, and then the citation is to the `.md`, not to the
PDF.

## Code

When the server indexes source files (`SILICA_INDEX_CODE`), `silica_search`
ranks them one unit per function, method, class or constant beside the
notes: a hit's `section` is the symbol, `span` its lines, and
`silica_read(path, section=<symbol>)` serves the body when the hit lacks
the needed context. Without code indexing, search covers documents;
use native repository tools to locate source code.

`silica_code_pack(target, budget_chars)` gives one source file with its
supertypes, extenders, the signatures it names, external dependencies and
importers inside a budget. Use it for a known target when the task needs
those relationships; a sufficient search hit or direct read needs no pack.
Check `truncated` before treating the target as complete.

## What Silica is not

It does not answer, summarise, plan or remember. It has no model and no
key. A number of indexed documents is not a number of documents read.
