<div align="center">

<picture>
  <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/banner.svg" />
  <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/banner-light.svg" alt="Silica Core" width="590" />
</picture>

**Lightweight, local evidence retrieval tools for code and research**

Silica locates the source, symbol, page, or passage you and your agents need. Maximum signal, minimum machinery.

<p align="center">
  <a href="https://pypi.org/project/silica-core/"><img src="https://img.shields.io/pypi/v/silica-core?style=flat&labelColor=000000&color=000000" alt="PyPI" /></a>
  <a href="https://github.com/kiycoh/silica-core/blob/main/LICENSE"><img src="https://img.shields.io/github/license/kiycoh/silica-core?style=flat&labelColor=000000&color=000000" alt="License" /></a>
</p>

</div>

<!-- The MCP registry proves ownership of the PyPI package by finding this
     string in the description PyPI renders, which is this file. -->
<!-- mcp-name: io.github.kiycoh/silica-core -->

Silica indexes the markdown, code, PDFs and office files under one root and
serves them to [Claude Code, Cursor, Hermes, Codex, OpenCode and every other popular harness](#harnesses), as MCP tools or as shell commands that print the same
JSON. A hit is a path, a section, a line, a window of text and the numbers
to judge it by. The harness owns the loop.

<p align="center">
  <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/quickstart.gif" alt="the quickstart recorded end to end: uv tool install, silica init reporting nine indexed documents, silica setup claude registering the MCP server, then Claude Code answering a question about the LSM compaction design space by calling silica-core and citing the PDF with its page and its line" width="900" />
</p>

Three commands, then a question asked the way you would ask any other. The
harness calls `silica_search`, and the answer carries the file, the page and
the line it came from.

## Install

```bash
uv tool install 'silica-core[mcp]'         # BM25 over documents and their sections (faster, lighter)
uv tool install 'silica-core[mcp,dense]'   # in addition the dense leg: numpy, a static model (still fast, more precise)
```

The second line adds the dense leg: `numpy` and a static model2vec model, no
torch and no GPU. It stays inert until the model is named and the sections
are embedded — the last stanza of the Quickstart. Take it when the questions
are paraphrases that share no words with the text; an exact term or an
identifier is answered by the lexical leg either way, and only that leg
reports `terms_absent`.

`pipx` works the same. The package is `silica-core`, the command is
`silica`, the tools are `silica_*`.

## Quickstart

In any folder of markdown, code, PDFs or office files:

```bash
silica init                               # adopt the folder: ignore file, first index
silica search "leveled compaction" -k 5  # the best located passages
silica setup claude                      # register the MCP server, write the guidance block into ~/.claude/CLAUDE.md

# optional, with the [dense] extra: the dense leg, a static model, nothing leaves the machine
export SILICA_EMBEDDING_MODEL=model2vec/minishlab/potion-retrieval-32M
silica index --embed
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

| Arm | file hit@5 · @10 | file MRR | symbol hit@10 | symbol recall | chars returned |
|---|---:|---:|---:|---:|---:|
| **Silica**, hybrid | **0.85 · 0.90** | **0.68** | **0.75** | **0.24** | 8,266 |
| Silica, vectors | 0.80 · 0.85 | 0.67 | 0.65 | 0.21 | **6,225** |
| Silica, lexical | 0.65 · 0.75 | 0.47 | 0.45 | 0.14 | 8,194 |
| zvec-grep 0.2.2, hybrid | 0.65 · 0.75 | 0.54 | 0.55 | 0.17 | 7,326 |
| zvec-grep 0.2.2, vector | 0.60 · 0.80 | 0.61 | 0.55 | 0.18 | 6,821 |
| zvec-grep 0.2.2, FTS | 0.45 · 0.60 | 0.36 | 0.45 | 0.11 | 6,690 |

On BEIR the two hybrids tie at the 95% interval: the same vectors rank the
same, with no daemon and no vector store. On code, Silica's hybrid file MRR
is +0.135 over zvec-grep's hybrid (95% interval +0.01 to +0.27), paired per
question. The fusion also gains +0.21 MRR and +0.30 symbol hit over Silica's
lexical arm; no reranker or graph expansion is involved.

On this measured scope, Silica is a compact, local, SOTA-competitive
retriever: it matches zvec-grep on BEIR and leads the paired SWE-QA
code-localization replay with the same embedder.

Retrieval matters only if the agent does less work without losing the answer.
These are separate experiments and are not pooled:

| Workload and arm | Runs | Quality | Search used | Turns | Tool calls | Seconds | Warm cost |
|---|---:|---:|---:|---:|---:|---:|---:|
| Repository, search-first contract | 20 | Judge 59.7 | 17/20 | **4.7** | — | **24** | $0.197 |
| Repository, same plugin without contract | 20 | Judge 50.6 | 0/20 | 6.5 | — | 28 | **$0.180** |
| Documents, resident Silica tools | 12 tasks | 12/12 correct | 12/12 | **3.9** | **2.9** | — | **$0.20** |
| Documents, no plugin | 12 tasks | 12/12 correct | — | 5.0 | 4.0 | — | $0.22 |

The repository result is one repetition: turns improve by 1.75 (95% interval
0.55 to 3.05 fewer), while Judge and cost remain inconclusive. The document
rows belong to a 144-run study over twelve questions and a 5.5M-token corpus.
They establish less work on that workload, not a universal agent claim.

Corpora, intervals, per-task exceptions and reproduction commands are in
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
