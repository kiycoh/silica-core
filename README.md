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
| `silica_read` | a slice by lines or by heading (a page, in a PDF), the outline, and a `version` to carry forward |
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

A PDF with a text layer is searched as it is: the index reads the layer
itself, one section per page, so a hit reads `p. 7` and `silica_read` serves
that page alone. No conversion, no `.md` written beside it. The reply also
carries `extract_path`, the extracted text on disk, for `grep` and the
harness's own reader — the line numbers there are the ones `silica_read`
serves. A scan has no text layer and reads as `unconverted` until you
convert it.

Extras: `[connect]` adds the Obsidian bridge, `[all]` both. The converters
for PDF (headings, figures), DOCX, EPUB, FB2, RTF, XLS and ODF need no extra.
Scanned PDFs, images, PPTX and XLSX go through [MinerU](https://github.com/opendatalab/MinerU)
when it is on your PATH; audio and video need `ffmpeg` and a speech-to-text
endpoint (`SILICA_STT_BASE_URL`). `silica doctor` says which lanes you have.

### Docker

No image is published. The `Dockerfile` builds one, and CI builds and smoke-tests
it on every push, so it stays runnable:

```bash
docker build -t silica-core .
docker run --rm -i -v /path/to/vault:/vault silica-core                 # the MCP server over stdio
docker run --rm -v /path/to/vault:/vault silica-core search "compaction" -k 5
```

The container serves `/vault` and keeps everything it derives — index, ledger,
checkpoints — under `/data`, which is `$HOME` inside the image: mount a volume
there or the index is rebuilt on every run. The lanes that need a system binary
(MinerU, ffmpeg, soffice) are deliberately not in the image; `docker run --rm
silica-core doctor` reports them missing, and the comments in the `Dockerfile`
say where to add the ones you use.

## From the shell

Every subcommand prints the same JSON the MCP tool returns, so a harness
with a shell needs no MCP at all.

```bash
silica index                                   # build or refresh the index (searches do it on first use)
silica search "incremental index updates" -k 5
silica read papers/lsm-trees.md --section "3 Compaction"
silica files --status unconverted              # what the index could not read
silica import papers/lsm-trees.pdf             # writes papers/lsm-trees.md beside the PDF (which then wins over the text layer)
silica write-note notes/decision.md --body-file - < decision.md
silica code-pack src/search/index.py --budget 12000
silica mcp --extended                          # also serve the wikilink tools
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

The same column, read down the hits, says whether one document or several
are in contention. Best section per document on that corpus: `0.99 · 0.67 ·
0.60` is one paper, `0.97 · 0.94 · 0.67` is two, `0.79 · 0.73 · 0.72` is
spread, `0.44 · 0.29 · 0.28` is nothing. Only the absolute level tells the
first shape from the last, so Silica reports the column and no ratio between
its neighbours: a ratio scores the first and the last shape alike.

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

`silica mcp --extended` adds the wikilink tools (`silica_links`,
`silica_backlinks`, `silica_orphans`, `silica_unresolved`) over the same
root.

There is no SQL tool and no data-file converter. A `.csv` stays a file:
`silica_files` lists it as `excluded` with the reason, and `silica_read`
serves its lines by number. Search does not index it. Anything past that is
a query engine, which is a different product from located evidence.

## Obsidian

`silica connect` hosts the bridge the Obsidian plugin dials into, so
writes can land through Obsidian's own vault API while the app is open.
The five core tools do not need it: they read and write the folder
directly.

## Not included, on purpose

No LLM client, no memory lane, no session capture, no hook that injects
text into a prompt, no summaries, no undo journal, no SQL over your data
files. Undo is git. The private product this core is cut from keeps those
lanes.

## Development

```bash
git clone https://github.com/kiycoh/silica-core.git && cd silica-core
uv sync --extra dev --extra mcp
uv run pytest -q
SILICA_BENCH_CORPUS=/path/to/markdown uv run pytest tests/test_retrieval_check.py -s   # the acceptance check
uv run lint-imports && uv run mypy silica && uv run ruff check silica tests
```

## License

MIT. See [LICENSE](LICENSE).
