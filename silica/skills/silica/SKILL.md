---
name: silica
description: Search a folder of documents or code with located evidence instead of grep. Use it for a question about what the files in the current folder say, when the answer is a passage and not a string; grep still wins on an exact string or a symbol name. Use when the user asks what a corpus, a set of papers, the docs or a repository say about something, when a question needs a passage you can cite by path and line, when several sources must be compared and each claim tabulated with its citation, or when the answer may not be in the corpus at all and you need to know that before reading.
---

# Silica: located evidence, no model

The `silica-core` MCP server indexes the folder the session was opened in and
serves five tools named `silica_*`. If they are deferred, load them with
ToolSearch. If they are missing, say so and give the install line:

```bash
uv tool install 'silica-core[mcp]' && silica setup claude   # or codex, cursor, zed, … (silica setup --list)
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
   `coverage` under about 0.5, means the corpus does not answer in these
   words: rephrase once with the corpus's vocabulary, keeping names,
   numbers and identifiers as they are, and if the second reply reads the
   same, stop; do not read the hits into an answer. A large `candidates`
   with `coverage` flat down the hits means the query is too broad: narrow
   it with rarer words or a `folder`. `scope` counts what the
   search could see: `unconverted` or `failed` above zero means a relevant
   document may never rank, so check `silica_files(status=…)` before
   calling absence; with a `folder`, `terms_absent_in_scope` is the zero of
   that folder, not of the corpus. `index.state = cold` means nothing was
   indexed yet; the first search builds the index.
3. **Read before you cite.** `silica_read(path, section=…)` or
   `(path, start, end)` serves the slice with the outline and a `version`.
   Cite path and line. Every hit carries the same `version`: pass it as
   `expect_version` on the read, and again on a later read, so a file that
   changed in between is refused instead of quoted under an old citation.
4. **Check coverage when the task is exhaustive.** `silica_files` lists
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
2. Search two or three phrasings in the corpus's words. In each reply,
   `candidates` counts the documents that matched at all, `documents` is
   the head of that ranking (the first k, plus the documents the hits came
   from) and `scope` what the search could see. Keep all three: they bound
   what was considered, and nothing in a reply is a roster of what was
   read.
3. Read every passage you will cite, with the hit's `version` as
   `expect_version`.
4. Answer as a table, one row per claim: claim, path, section or line,
   version. Below it, the contradictions between sources, and what the
   corpus does not say: the queries, their `terms_absent`, and the `scope`
   counts, since absent terms alone do not prove silence while
   `unconverted` or `failed` is above zero.
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

`silica_code_pack(target, budget_chars)` gives one source file with its
supertypes, extenders, the signatures it names, external dependencies and
importers inside a budget. Use it before rewriting or porting a file,
instead of ten greps. Check `truncated` before treating the target as
complete.

## What Silica is not

It does not answer, summarise, plan or remember. It has no model and no
key. A number of indexed documents is not a number of documents read.
