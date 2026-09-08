<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/banner-light.svg" />
    <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/banner.svg" alt="Silica" width="440" />
  </picture>
</p>

<p align="center">Retrieval tools for coding agents. No model in the loop.</p>

---

Point Silica at a folder of markdown, code, PDFs or office files. The harness
you already use (Claude Code, Codex, opencode, DeepSeek Harness, or a shell)
gets five tools that return located evidence: a path, a section, a line, a
window of text, and the numbers to judge it by. Silica never answers,
summarises, plans or remembers for you. The harness owns the loop.

| Tool | Returns |
|---|---|
| `silica_files` | the inventory and what the index did with each file: `indexed`, `changed`, `excluded`, `failed`, `unconverted` |
| `silica_search` | ranked passages: path, section, line, raw BM25, matched terms, `coverage`, and the query terms absent from the corpus |
| `silica_read` | a slice by lines or by heading, the outline, and a `version` to carry forward |
| `silica_code_pack` | an AST context pack for one source file inside a character budget |
| `silica_write_note` | one atomic write, linted for structure and unresolved wikilinks |

The contract, the reply shapes and the acceptance checks are in
[TOOLS.md](TOOLS.md). Nothing on that page needs an API key, a model or a
network.

## Install

```bash
uv tool install 'silica-core[mcp]'   # or: pipx install 'silica-core[mcp]'
silica setup claude                  # or: codex, opencode, dsh
```

The package is `silica-core`, the command is `silica`, the tools are
`silica_*`: the distribution carries the name, the code keeps the namespace.

`setup` registers the MCP server in the client's own config at user scope.
Every session then serves the folder the client was opened in. To serve
another root, pass `--vault DIR` in the server entry, or export
`SILICA_VAULT` in the client's `env` block; a `.env` file is not enough on
purpose.

Extras: `[connect]` adds the Obsidian bridge, `[all]` both. The converters
for PDF (text layer), DOCX, EPUB, FB2, RTF, XLS and ODF need no extra.
Scanned PDFs, images, PPTX and XLSX go through [MinerU](https://github.com/opendatalab/MinerU)
when it is on your PATH; audio and video need `ffmpeg` and a speech-to-text
endpoint (`SILICA_STT_BASE_URL`). `silica doctor` says which lanes you have.

## From the shell

Every subcommand prints the same JSON the MCP tool returns, so a harness
with a shell needs no MCP at all.

```bash
silica index                                   # build or refresh the index (searches do it on first use)
silica search "incremental index updates" -k 5
silica read papers/lsm-trees.md --section "3 Compaction"
silica files --status unconverted              # what the index could not read
silica import papers/lsm-trees.pdf             # writes papers/lsm-trees.md beside the PDF
silica write-note notes/decision.md --body-file - < decision.md
silica code-pack src/search/index.py --budget 12000
silica mcp --extended                          # also serve the tables and link tools
```

## How search says no

A search returns hits even when the corpus does not answer, because a
ranked list always has a top. What tells the two apart:

- `coverage`: the share of the query's idf mass the hit's matched terms
  carry. Near 1, every rare term matched; near 0, only the common words did.
- `terms_absent`: query terms that occur nowhere in the corpus.
- `matched_terms`: which words this hit actually contains.

Measured on 254 converted papers (22 MB): the answered questions scored
0.69 to 1.00 on their top hit, a question the corpus does not cover scored
0.44. No boolean is derivable from lexical signals alone, so Silica exposes
the numbers and the harness decides to stop, read, or rephrase.

Ranking is BM25 over documents, then over the heading sections of the top
documents, at most two sections per document, with each hit's densest
window. The index is one JSON file per root under `~/.silica/index`, built
in seconds and refreshed by mtime.

## The optional REPL

`silica repl` is the reference harness: a plain agent loop over the same
tools, for a folder where no coding agent is running. It is the only
surface that needs a model, and the model is any OpenAI-compatible chat
endpoint:

```bash
export SILICA_MODEL=openrouter/deepseek/deepseek-chat OPENROUTER_API_KEY=…   # hosted
export SILICA_MODEL=lmstudio/qwen3-14b                                      # or local: lmstudio/…, ollama/…
silica repl
```

`SILICA_PROVIDER_BASE_URL` and `SILICA_PROVIDER_API_KEY` point a bare model
id at any other endpoint. `silica mcp` never needs any of this.

## Extended tools

`silica mcp --extended` adds the tabular census (`silica_tables`,
`silica_query_table`: DuckDB over CSV, XLSX and friends, schema in every
reply) and the wikilink tools (`silica_links`, `silica_backlinks`,
`silica_orphans`, `silica_unresolved`) over the same root.

## Obsidian

`silica connect` hosts the bridge the Obsidian plugin dials into, so
writes can land through Obsidian's own vault API while the app is open.
The five core tools do not need it: they read and write the folder
directly.

## Not included, on purpose

No LLM client, no memory lane, no session capture, no hook that injects
text into a prompt, no summaries, no undo journal. Undo is git. The
private product this core is cut from keeps those lanes.

## Development

```bash
git clone https://github.com/kiycoh/silica-core.git && cd silica-core
uv sync --extra dev --extra mcp
uv run pytest -q
SILICA_BENCH_CORPUS=/path/to/markdown uv run pytest tests/test_retrieval_check.py -s   # the acceptance check
uv run lint-imports && uv run mypy silica && uv run ruff check silica tests
```

## License

AGPL-3.0-or-later. A commercial licence is available, see
[LICENSE-COMMERCIAL.md](LICENSE-COMMERCIAL.md).
