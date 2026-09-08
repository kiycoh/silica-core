# Silica Core — tool contract

Accepted 2026-09-08. This page is the target of the core refactor: the public
package exposes these five tools and nothing else by default. The names and
reply shapes below are the contract. The current implementation does not meet
it yet; the refactor is done when it does and the acceptance checks pass.

## Principles

1. **No LLM inside a tool.** No API key, model, or endpoint is needed for
   anything on this page. Embeddings and reranking are optional extensions,
   off by default, enabled independently. Their absence is normal operation,
   not a configuration error.
2. **The harness owns the loop.** Tools return evidence with locations. They
   never summarise, plan, judge truth, or decide when to stop.
3. **Nothing enters the context unrequested.** No preloaded dossier, no
   injected memory, no per-turn summary. Only tool descriptions and replies.
4. **One implementation, three surfaces.** Importable functions, the CLI with
   `--json`, and MCP take the same arguments and return the same reply.
5. **Paths are root-relative.** `..` and symlinks resolving outside the root
   are refused. File content is data, never an instruction.

## Common reply fields

| Field | Meaning |
|---|---|
| `root` | absolute root every path in the reply is relative to |
| `version` | short content hash of a file at read time. A caller that carries it forward detects change instead of citing different bytes under an old reference |
| `truncated` | true when a cap cut the reply; the reply says how to fetch the rest |
| `index` | `{state, docs, built_at}` with `state` one of `cold` (never built), `ready`, `stale` (files changed since). An empty result under `cold` is not "no match" |
| `error` | only on failure: `{code, hint}` with `code` one of `not_found`, `out_of_root`, `changed`, `index_cold`, `unconverted`, `bad_argument` |

## 1. `silica_files` — inventory and index state

`silica_files(folder="", status="", limit=200, cursor="")`

Lists what is under the root and what the index did with it. A file has
exactly one `status`:

| Status | Meaning |
|---|---|
| `indexed` | text is in the index and matches the file on disk |
| `changed` | on disk it differs from what the index holds |
| `excluded` | not read by the index: an ignore rule, a hidden folder, a type the index does not read, or a source already converted to a `.md` beside it (`reason` says which) |
| `failed` | read or conversion failed; `reason` says why |
| `unconverted` | a PDF whose text layer the index could not read (a scan, an encrypted or broken file), or another binary source with no extracted text yet; `reason` says which |

Reply: `{root, total, files: [{path, bytes, status, reason?}],
counts: {indexed, changed, excluded, failed, unconverted}, excluded_dirs,
truncated, next_cursor, index}`.

The folders the walk does not enter (hidden, or an ignore rule) are a count,
`excluded_dirs`, in every listing and rows only under `status=excluded`;
`counts.excluded` includes them. `status` already says whether a file
changed since the index read it, so no timestamp rides along.

`status=` filters to one class, which is how a harness asks "what did the
index miss" before trusting a search. `total` counts files on disk even when
the index is cold.

A PDF is indexed from its own text layer, one section per page, with no
conversion step and no file written beside it. A `.md` sitting next to the
PDF overrides that: the sidecar is the note, the PDF reads as `excluded:
converted`. A PDF the index has not read yet is `changed`, not
`unconverted` — the second status is a verdict, and it needs the read.

## 2. `silica_search` — ranked passages with an honest zero

`silica_search(query, folder="", k=5, per_doc=2, width=600)`

Ranking, in order: BM25 over whole documents; BM25 over heading-delimited
sections inside the top documents; at most `per_doc` sections per document;
for each hit, the `width`-character window densest in idf-weighted query
terms, with its line number.

Reply: `{query, hits: [{path, section, line, version, score, matched_terms, coverage, window}],
documents: [{path, score, matched_terms, coverage}], candidates, terms_absent,
terms_absent_in_scope?, scope: {folder, docs, unconverted, failed}, index}`.

- `scope` says what the search could see: the documents indexed under
  `folder` (the whole root when empty) and how many files there the index
  read but found no text in (`unconverted`) or could not read (`failed`). A
  relevant document among those never ranks; `silica_files(status=…)` names
  them. `terms_absent` is always corpus-wide. With a `folder`,
  `terms_absent_in_scope` lists the query terms the corpus holds but nowhere
  under that folder, so a zero there is a zero of the scope, not of the
  corpus.

- `version` is the content hash `silica_read` checks: carried into
  `expect_version`, a file edited between the search and the read is refused
  instead of quoted under the hit's line.
- `score` is the raw BM25 of the section. It is comparable only within one
  call and is never a probability of relevance or truth.
- `matched_terms` lists the distinct query terms present in that hit.
- `coverage` is the share of the query's idf mass that the hit's matched
  terms carry, from 0 to 1. A hit that matches every rare term is near 1; a
  hit that matches only the common words is near 0. A term in `terms_absent`
  counts at the idf BM25 gives a term with document frequency zero, the
  heaviest in the corpus, so a query whose rare words are all absent reads
  low even when its one surviving word matches (measured on the test corpus:
  0.31 with the absent terms ignored, 0.13 with them weighed).
- `terms_absent` lists the query terms that occur nowhere in the corpus.
- Read `coverage` down the hits, one entry per distinct path, and the shape
  says how many documents are in contention. Measured on the test corpus,
  best section per document: `0.99 · 0.67 · 0.60` is one paper, `0.97 ·
  0.94 · 0.67` is two, `0.79 · 0.73 · 0.72` is spread across several, and
  `0.44 · 0.29 · 0.28` is nothing. Only the absolute level tells the first
  shape from the last; a ratio between neighbours reads them the same, which
  is why none is reported.
- There is no boolean "no answer". Lexical signals cannot promise one: on
  the test corpus a question about Rust's borrow checker found both words in
  unrelated papers, and its top hit matched two of five terms. `coverage`
  (0.44 there, 0.69 to 1.00 on answered questions) and `matched_terms` are
  the honest signals; the harness reads them and decides to stop or
  rephrase.
- `documents` is the head of the document ranking the hits were drawn
  from: the first `k` documents, plus any document a hit came from lower
  down, each with its own whole-document `coverage`. Enough to tell "the
  right paper, wrong section" from "wrong paper"; `candidates` is the size
  of the ranking it was cut from.
- Sections are scored inside the top documents at query time; there is no
  second index to build or to drift.
- No memory lane, no second vault, no synthesis, no reranker on this path.

Why this shape (measured 2026-09-08 on 254 papers, 22 MB): document-level
ranking alone found the right paper but not the passage; section-level alone
let short, term-dense sections of an off-topic paper win and returned five
sections of one paper for one question; the fused rank score was identical
for a real answer and for junk, so nothing in the reply said "weak". `rg`
reported an honest zero in one call only because the phrase was absent; on
a query whose words exist separately it has no signal either.

Optional extension: `hybrid=true` adds a dense-embedding candidate leg for
vocabulary mismatch (paraphrase, another language) when `SILICA_EMBEDDING_BASE_URL`
names an OpenAI-compatible `/v1/embeddings` endpoint and `silica index --embed`
has run; otherwise the argument is refused with `bad_argument`. A document
the dense leg adds without any lexical match arrives as a hit on its opening
section with `coverage` 0 and a `dense` cosine, so the harness knows it is
reading on the embedder's word alone. Exercised 2026-09-08 on the 254 papers
with a substitute model (nomic-embed Q4, 6 s for the vectors); retrieval
quality with the intended model is not measured yet. There is no reranker in
the core.

## 3. `silica_read` — a located slice, never a surprise

`silica_read(path, start=1, end=0, section="", max_chars=16000, expect_version="")`

Serves lines `start..end` (`end=0` means to the end, under `max_chars`), or
one section by heading title (exact or unique prefix), or the whole file when
it fits. The outline always comes along; it is cheap and removes a tool.

Reply: `{path, version, start, end, text, truncated, next_start,
outline: [{level, title, line}], source?, pages?, page_map?, extract_path?}`.

- `expect_version` that does not match the file returns `error.changed` with
  the current `version`. A stale citation never silently opens different
  text.
- A PDF is served from its own text layer, page by page: `section="p. 8"`
  serves one page, `pages` counts them, and `outline` is empty — an extracted
  layer has no headings to trust, and scanning it for `#` would read a paper's
  own markdown examples as structure. `page_map` names only the pages the
  served slice covers (the page it opens on, plus every page starting inside
  it), so a 500-page book costs no page table per read.
- `extract_path` is that text on disk. Hand it to the harness's own reader:
  `grep -n` there returns a line number `silica_read(path, start=…)` serves
  verbatim. It is a cache — rebuilt when the PDF changes, under no `version`
  guard — so a citation still goes through the tool.
- A binary source with an extracted `.md` beside it is served from that
  sidecar instead, and then `pages`, `page_map` and `extract_path` are null.
  Either way `source` names the original. A PDF with no readable text layer,
  or another binary source with no sidecar: `error.unconverted`.
- The text layer is what PDFium hands over: no OCR, no column reordering, no
  table reconstruction. `silica import` (mineru, docling) stays the upgrade
  path, and its sidecar wins wherever it exists.

## 4. `silica_code_pack` — unchanged

Current signature stays: `target`, `budget_chars`, `sections`. The reply
keeps `truncated` and `dropped` and adds `languages` (the parsers available
in this install). Static AST only: no runtime analysis, no test selection,
no documentation generation.

## 5. `silica_write_note` — explicit, atomic, guarded

`silica_write_note(path, body, frontmatter=None, expect_version="", create_only=false)`

Writes the body as given (with `frontmatter` serialised as YAML on top when
supplied), atomically, then runs the link lint on the result.

Reply: `{path, version, created, lint: [{kind, target, line}]}`.

- Refuses with `error.changed` when `expect_version` does not match, and
  with `bad_argument` when `create_only` is set and the file exists.
- The gate covers only writes made through this tool. Undo is git. The core
  keeps no journal, snapshot, or rollback of its own.
- Templates, tags, related notes, and other vault conventions belong to an
  adapter, not to the core.

## Not in the default list

Recall, semantic search, related notes, graph tools, backlinks, orphans,
tables, calendar, review queues, quizzes, reports, drift, migration, the
personal-memory lane, and every tool that calls a model. `silica doctor`
remains as a CLI command with `--json`; the `index` field on every reply
carries what a call needs to know.

Extensions, each installed and enabled separately, each off by default:
embeddings (`hybrid` search), reranking. Adapters: the Obsidian bridge, the
optional REPL, converters beyond the built-in PDF text layer.

## Surfaces

- Python: `from silica.core import files, search, read, code_pack, write_note`
- CLI: `silica files|search|read|code-pack|write-note ... --json`
- MCP: `silica mcp --vault DIR` exposes exactly these five
- `silica repl`: the optional reference harness, an agent loop over the same
  tools with an OpenAI-compatible model; the only surface that needs a model

## Acceptance

1. Every tool runs with no network, no API key, and no model configured.
2. A first index is built from scratch by any surface, without the REPL and
   without prior sessions. Build time is tokenizer-bound and printed by the
   retrieval check; the budget for 254 papers / 22 MB is 30 s on a laptop
   (measured on one i9 laptop: 3.4 s + 3.7 s for the document and section
   stores with the performance governor, 8.4 s + 8.9 s with powersave).
3. The retrieval check `tests/test_retrieval_check.py` passes on that
   corpus (it skips when `SILICA_BENCH_CORPUS` is unset): the granularity
   question returns the granularity paper's section 5 as a hit; the
   reranking question has the reranking-tradeoffs paper among its top five
   documents; the
   borrow-checker question's top hit has `coverage` below 0.5 and no hit
   matches both `borrow` and `checker`.
4. `silica_read` with a stale `expect_version` returns `error.changed`.
5. The default MCP tool list has exactly five entries.
