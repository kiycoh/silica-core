<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/banner.svg" />
  <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/banner-light.svg" alt="Silica Core" width="590" />
</picture>

**Retrieval tools for coding agents. No model in the loop.**

Point it at a folder and the agent you already use gets five tools that return located evidence, never answers.

<p align="center">
  <a href="https://pypi.org/project/silica-core/"><img src="https://img.shields.io/pypi/v/silica-core?style=flat&labelColor=000000&color=000000" alt="PyPI" /></a>
  <a href="https://github.com/kiycoh/silica-core/blob/main/LICENSE"><img src="https://img.shields.io/github/license/kiycoh/silica-core?style=flat&labelColor=000000&color=000000" alt="License" /></a>
</p>

</div>

<!-- The MCP registry proves ownership of the PyPI package by finding this
     string in the description PyPI renders, which is this file. -->
<!-- mcp-name: io.github.kiycoh/silica-core -->

Silica works over the markdown, code, PDFs and office files under one root and
serves them to [Claude Code, Cursor, Zed, Codex and every other harness it
knows](#harnesses), as MCP tools or as shell commands that print the same JSON.
A hit is a path, a section, a line, a window of text and the numbers to judge
it by. Silica never answers, summarises, plans or remembers for you: the
harness owns the loop.

## Install

```bash
uv tool install 'silica-core[mcp]'
```

`pipx install 'silica-core[mcp]'` works the same. The package is `silica-core`,
the command is `silica`, the tools are `silica_*`: the distribution carries the
name, the code keeps the namespace.

## Quickstart

In any folder of markdown, code, PDFs or office files:

```bash
silica init                               # adopt the folder and build the first index
silica search "leveled compaction" -k 5  # return the best located passages
silica setup claude                      # register the same tools as an MCP server
```

<p align="center">
  <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/search-hit.png" alt="silica search &quot;leveled compaction&quot; over nine LSM papers: the hit carries the file, p. 5 and the passage; beside it the PDF is open on that page with the same passage highlighted" width="900" />
</p>

Nine arXiv papers in a folder, indexed in 2.1 s. The hit names the file, the
page and the passage, and the page beside it is the check, not a promise.

## Benchmarks

Silica's closest comparisons are zvec-grep and ck: local retrieval tools over
files. Each arm received the same BEIR SciFact and NFCorpus documents and
queries. Scores are nDCG@10 (higher is better), shown as SciFact · NFCorpus.

| Mode | Silica | zvec-grep 0.2.2 | ck 0.7.11 |
|---|---:|---:|---:|
| Lexical | **0.662 · 0.311** | 0.649 · 0.297 | 0.630 · 0.289 |
| Hybrid, same `potion-retrieval-32M` embedder | **0.675 · 0.328** | 0.672 · 0.330 | — |

Silica's lexical index needs no model; the competitors serve lexical search
from an index that also includes embeddings. BEIR's published BM25 baselines
are 0.665 · 0.325. The hybrid difference between Silica and zvec-grep is
statistical noise at the 95% interval. See [benchmarks](docs/benchmarks.md) for
corpora, methods, intervals and commands.

## Tools

| Tool | Shell | Returns |
|---|---|---|
| `silica_files` | `silica files` | the inventory and what the index did with each file: `indexed`, `changed`, `excluded`, `failed`, `unconverted` |
| `silica_search` | `silica search` | ranked passages: path, section, line, raw BM25, matched terms, `coverage`, and the query terms absent from the corpus |
| `silica_read` | `silica read` | a slice by lines or by heading (a page, in a PDF), the outline, and a `version` to carry forward |
| `silica_code_pack` | `silica code-pack` | an AST context pack for one source file inside a character budget |
| `silica_write_note` | `silica write-note` | one atomic write, linted for structure and unresolved wikilinks |

Search indexes markdown and the PDFs that carry a text layer. Source files are
not in the index: `silica_files` lists a `.py` as `excluded`, `silica_read`
serves it by line and `silica_code_pack` by file. To find a symbol in a
repository, grep wins. The contract, the reply shapes and the acceptance checks
are in [TOOLS.md](TOOLS.md); nothing there needs an API key, a model or a
network.

## How search says no

A ranked list always has a top, even when the corpus does not answer. Three
fields show how much the result is worth:

- `coverage`: the share of the query's idf mass the hit's matched terms carry.
  Near 1, every rare term matched; near 0, only common words did.
- `terms_absent`: query terms that occur nowhere in the corpus.
- `matched_terms`: which words this hit actually contains.

<p align="center">
  <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/search-says-no.png" alt="silica search &quot;raft consensus log replication&quot; over the same nine papers: terms_absent lists raft and consensus, coverage falls to 0.19, and the top hit is a passage about data replication rather than Raft" width="900" />
</p>

`raft consensus log replication` over the same nine papers: `raft` and
`consensus` occur in none of them, `coverage` falls to 0.19, and the top hit
is about data replication, not Raft. Silica exposes the signals and lets the
harness decide whether to stop, read or rephrase.

Ranking is BM25 over documents, then over the heading sections of the top
documents, returning each hit's densest window. `--also QUERY` ranks another
query group separately and fuses the results, useful for searching a concept
and its exact identifiers together. Searches refresh small changes inline;
larger ones return `stale` with `pending` until `silica index` catches up, and
interrupted builds resume. Use `<root>/.silicaignore` to exclude content.

### The dense leg

Optional section embeddings catch paraphrases that share no rare words with
the answer. After they are built, every search fuses them with BM25. Use any
OpenAI-compatible `/v1/embeddings` endpoint:

```bash
ollama pull nomic-embed-text
export SILICA_EMBEDDING_BASE_URL=http://localhost:11434/v1 SILICA_EMBEDDING_MODEL=nomic-embed-text
export SILICA_EMBEDDING_DOC_PREFIX='search_document: ' SILICA_EMBEDDING_QUERY_PREFIX='search_query: '  # what nomic's card asks for
silica index --embed
```

Or use a local static model, with no server:

```bash
uv tool install 'silica-core[mcp,dense]'
export SILICA_EMBEDDING_MODEL=model2vec/minishlab/potion-retrieval-32M
silica index --embed
```

Text leaves the machine only for an endpoint that is not on it, and only after
`silica index --embed --allow-remote` grants that host once. Until then search
stays lexical. The reply always says whether the dense leg ran.

## What it reads

- **PDF with a text layer:** searched directly, one section per page; no conversion or sidecar.
- **Scanned PDF, image, PPTX, XLSX:** `silica import` uses [MinerU](https://github.com/opendatalab/MinerU) to create a guarded `.md` sidecar. Silica reports it as stale if the source changes.
- **DOCX, EPUB, FB2, RTF, XLS, ODF:** converted with no extra.
- **Audio and video:** `ffmpeg` plus `SILICA_STT_BASE_URL`.
- **CSV and other data files:** readable by line but excluded from search.

`silica doctor` says which lanes this machine has. Extras: `[connect]` adds the
Obsidian bridge, `[dense]` the local embedder, `[all]` everything.

## Harnesses

`silica setup <client>` writes the registration into the client's own config.
It backs up existing config and refuses malformed files. `silica setup --list`
shows paths for `claude`, `codex`, `cursor`, `windsurf`, `zed`, `cline`, `roo`,
`continue`, `goose`, `opencode`, `openhands`, `gemini`, `dsh`, `hermes`,
`openclaw`, `agent-zero`, `claude-desktop`, `lmstudio`, `anythingllm` and
`librechat`; `shell`, `python` and `generic` print recipes for anything else. By default the server serves the
folder the client opens in; use `--vault DIR` or `SILICA_VAULT` for a fixed root.

`npx skills add kiycoh/silica-core` installs the skill that tells an agent
when to reach for the tools. It installs nothing else: on its own it leaves an
agent holding instructions for tools that are not there. Setup details, shell
recipes, Docker and the optional REPL are in [docs/harnesses.md](docs/harnesses.md).

## Other surfaces

- **Extended tools:** `silica mcp --extended` adds the wikilink tools, `silica_links`, `silica_backlinks`, `silica_orphans` and `silica_unresolved`, over the same root.
- **Obsidian:** `silica connect` hosts the bridge the Obsidian plugin dials into, so writes land through the vault API while the app is open. The five core tools do not need it.
- **REPL:** `silica repl` runs a small reference agent over the same tools. It is the only surface that needs a model (`SILICA_MODEL`).

The core keeps no memory lane, prompt injection, summaries or undo journal.
Undo is git.

## License

MIT. See [LICENSE](LICENSE).
