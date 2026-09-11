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

Silica indexes the markdown, code, PDFs and office files under one root and
serves them to [Claude Code, Cursor, Zed, Codex and every other harness it
knows](#harnesses), as MCP tools or as shell commands that print the same
JSON. A hit is a path, a section, a line, a window of text and the numbers
to judge it by. The harness owns the loop.

## Install

```bash
uv tool install 'silica-core[mcp,dense]'
```

`pipx` works the same. The package is `silica-core`, the command is
`silica`, the tools are `silica_*`.

## Quickstart

In any folder of markdown, code, PDFs or office files:

```bash
silica init                               # adopt the folder: ignore file, first index
silica search "leveled compaction" -k 5  # the best located passages
silica setup claude                      # register the MCP server, write the guidance block into ~/.claude/CLAUDE.md
```

<p align="center">
  <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/search-hit.png" alt="silica search &quot;leveled compaction&quot; over nine LSM papers: the hit carries the file, p. 5 and the passage; beside it the PDF is open on that page with the same passage highlighted" width="900" />
</p>

Nine arXiv papers, indexed in 2.1 s. The hit names the file, the page and
the passage; the page beside it is the check.

## Tools

| Tool | Shell | Returns |
|---|---|---|
| `silica_files` | `silica files` | the inventory and what the index did with each file: `indexed`, `changed`, `excluded`, `failed`, `unconverted` |
| `silica_search` | `silica search` | ranked passages: path, section, line, BM25, matched terms, `coverage`, and the query terms absent from the corpus |
| `silica_read` | `silica read` | a slice by lines or by heading (a page, in a PDF), the outline, and a `version` to carry forward |
| `silica_code_pack` | `silica code-pack` | an AST context pack for one source file inside a character budget |
| `silica_write_note` | `silica write-note` | one atomic write, linted for structure and unresolved wikilinks |

In a source tree every function, method, class and constant is its own unit:
a hit's `section` is the symbol, `span` its lines, and
`silica_read(path, section=…)` serves the body. For a symbol whose name is
known, grep wins; for a question that names none, the search comes first,
and the plugin's prompt hook asks the model to say so before it greps. The
contract, the reply shapes and the acceptance checks are in
[TOOLS.md](TOOLS.md). Nothing needs an API key or a network.

## How search says no

A ranked list always has a top, even when the corpus does not answer. Three
fields say how much the result is worth:

- `coverage`: the share of the query's idf mass the hit's matched terms carry. Near 1, every rare term matched; near 0, only common words did.
- `terms_absent`: query terms that occur nowhere in the corpus.
- `matched_terms`: the words this hit actually contains.

<p align="center">
  <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/search-says-no.png" alt="silica search &quot;raft consensus log replication&quot; over the same nine papers: terms_absent lists raft and consensus, coverage falls to 0.19, and the top hit is a passage about data replication rather than Raft" width="900" />
</p>

`raft consensus log replication` over the same nine papers: `raft` and
`consensus` occur in none of them, `coverage` falls to 0.19, and the top hit
is about data replication. On 254 papers the top hit of an answered question
carries 0.69 to 1.00; a question the corpus does not cover, 0.44. Silica
exposes the signals; the harness decides whether to stop, read or rephrase.

## Benchmarks

nDCG@10 on BEIR SciFact · NFCorpus, the same documents and queries for every
arm. BEIR's published BM25 baselines are 0.665 · 0.325. Silica's lexical index
needs no model; the others serve lexical search from an index that also holds
embeddings.

| Mode | Silica | zvec-grep 0.2.2 | ck 0.7.11 |
|---|---:|---:|---:|
| Lexical | **0.662 · 0.311** | 0.649 · 0.297 | 0.630 · 0.289 |
| Hybrid, same `potion-retrieval-32M` embedder | **0.675 · 0.328** | 0.672 · 0.330 | |

Code, on the twenty SWE-QA questions zvec-grep publishes for its own
benchmark, same embedder, k = 10, scored on the files and symbols the
reference answer rests on.

| Arm | file hit@5 · @10 | file MRR | symbol hit@10 | symbol recall |
|---|---:|---:|---:|---:|
| **Silica**, hybrid | **0.85 · 0.90** | **0.68** | **0.75** | **0.24** |
| zvec-grep 0.2.2, hybrid | 0.65 · 0.75 | 0.54 | 0.55 | 0.17 |

On BEIR the two hybrids tie at the 95% interval: the same vectors rank the
same, with no daemon and no vector store. On code, file MRR is +0.135 (95%
interval +0.01 to +0.27) paired per question. Asked the way the plugin asks,
Sonnet ran the search in 17 of 20 SWE-QA runs and closed the question in 4.7
turns instead of 6.5, cost unchanged within its interval; zvec-grep's MCP
search, with the guidance `zg install` writes, was chosen once in twenty.
Corpora, intervals, the `silica_code_pack` cost runs and the commands are in
[benchmarks](public/benchmarks.md).

## Harnesses

`silica setup <client>` writes the registration into the client's own config
and backs up what was there; for `claude` it also puts a guidance block, when
to search before grep, into `~/.claude/CLAUDE.md`. `silica setup --list`
names the clients: `claude`, `codex`, `cursor`, `windsurf`, `zed`, `cline`,
`roo`, `continue`, `goose`, `opencode`, `openhands`, `gemini`, `dsh`,
`hermes`, `openclaw`, `agent-zero`, `claude-desktop`, `lmstudio`,
`anythingllm` and `librechat`; `shell`, `python` and `generic` print recipes
for anything else. The server serves the folder the client opens in;
`--vault DIR` or `SILICA_VAULT` fixes the root.

Every written block, and the Claude Code plugin, run `silica mcp --retrieval
local-hybrid`: `potion-retrieval-32M` in the server process, index and
vectors built in the background at start, the search lexical and `dense:
warming` until they land. Nothing leaves the machine; the one download is
the model, once. `npx skills add kiycoh/silica-core` installs the skill that
tells an agent when to reach for the tools, and nothing else. Shell recipes,
Docker and the REPL are in [public/harnesses.md](public/harnesses.md).

## Notes

- **What it reads:** markdown, `.txt`, `.rst` and PDFs with a text layer directly, one PDF page per section; DOCX, EPUB, FB2, RTF, XLS and ODF converted with no extra; scanned PDFs, images, PPTX and XLSX through `silica import` with [MinerU](https://github.com/opendatalab/MinerU); audio and video with `ffmpeg` plus `SILICA_STT_BASE_URL`; CSV readable by line, excluded from search. In a source tree the code lane adds source files and their `json`, `yaml`, `toml`, `cfg` and `ini`. `silica doctor` says which lanes this machine has.
- **The dense leg:** section embeddings that catch a paraphrase sharing no rare word with the answer. `uv tool install` stays lexical until `silica index --embed`, with the `[dense]` extra's local model or any OpenAI-compatible `/v1/embeddings` endpoint. Text leaves the machine only for a remote endpoint, and only after `silica index --embed --allow-remote` grants that host once. Variables and reply states in [TOOLS.md](TOOLS.md), the ollama recipe in [public/harnesses.md](public/harnesses.md).
- **More surfaces:** `silica mcp --extended` adds the wikilink tools; `silica connect` (extra `[connect]`) hosts the bridge the Obsidian plugin dials into, so writes land through the vault API while the app is open; `silica repl` runs a small reference agent over the same tools, the one surface that needs a model (`SILICA_MODEL`).
- **Not in the core:** no memory lane, prompt injection, summaries or undo journal. Undo is git.

## License

MIT. See [LICENSE](LICENSE).
