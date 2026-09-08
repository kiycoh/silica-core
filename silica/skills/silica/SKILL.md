---
name: silica
description: Search a folder of documents or code with located evidence instead of grep. Use when the user asks what a corpus, a set of papers, the docs or a repository say about something, when a question needs a passage you can cite by path and line, or when the answer may not be in the corpus at all and you need to know that before reading.
---

# Silica: located evidence, no model

The `silica-core` MCP server indexes the folder the session was opened in and
serves five tools named `silica_*`. If they are deferred, load them with
ToolSearch. If they are missing, say so and give the install line:

```bash
uv tool install 'silica-core[mcp]' && silica setup claude   # or codex, opencode, dsh
```

## The loop

1. **Search with the corpus's words, not a question.** `silica_search`
   returns ranked passages: `path`, `section`, `line`, `score` (raw BM25,
   comparable within one call only), `matched_terms`, `coverage`, and the
   `documents` ranking behind the hits. Prefer it to grep when you want the
   passage and not the file, or when a whole-document ranking matters.
2. **Read the reply before the hits.** `terms_absent` lists query words the
   corpus never contains; `coverage` is the share of the query's rare-term
   mass a hit carries. A discriminating term in `terms_absent`, or a top
   `coverage` under about 0.5, means the corpus does not answer: stop or
   rephrase, do not read the hits into an answer. `index.state = cold`
   means nothing was indexed yet; the first search builds the index.
3. **Read before you cite.** `silica_read(path, section=…)` or
   `(path, start, end)` serves the slice with the outline and a `version`.
   Cite path and line. Carry `version` into `expect_version` on a later
   read so a changed file is refused instead of quoted under an old
   citation.
4. **Check coverage when the task is exhaustive.** `silica_files` lists
   every file with what the index did to it; `status=unconverted` names
   the PDFs and office files with no extracted text, `status=failed` the
   ones that could not be read. Top-k does not certify coverage.
5. **Write only when asked.** `silica_write_note(path, body)` writes one
   note as given and lints it. Nothing is captured or summarised for you;
   undo is git.

## Code

`silica_code_pack(target, budget_chars)` gives one source file with its
supertypes, extenders, the signatures it names, external dependencies and
importers inside a budget. Use it before rewriting or porting a file,
instead of ten greps. Check `truncated` before treating the target as
complete.

## What Silica is not

It does not answer, summarise, plan or remember. It has no model and no
key. A number of indexed documents is not a number of documents read.
