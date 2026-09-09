<p align="center">
  <picture>
    <source media="(prefers-color-scheme: light)" srcset="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/banner-light.svg" />
    <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/banner.svg" alt="Silica Core" width="520" />
  </picture>
</p>

<p align="center">Retrieval tools for coding agents. No model in the loop.</p>

<p align="center">
  <a href="https://pypi.org/project/silica-core/"><img src="https://img.shields.io/pypi/v/silica-core" alt="PyPI" /></a>
  <a href="https://pypi.org/project/silica-core/"><img src="https://img.shields.io/pypi/pyversions/silica-core" alt="Python versions" /></a>
  <a href="https://github.com/kiycoh/silica-core/actions/workflows/ci.yml"><img src="https://img.shields.io/github/actions/workflow/status/kiycoh/silica-core/ci.yml?branch=main&label=ci" alt="CI" /></a>
</p>

---

Point Silica at a folder of markdown, code, PDFs or office files. The harness
you already use — [Claude Code, Cursor, Zed, a bare shell, and every other
one below](#harnesses) — gets five tools that return located evidence: a path, a
section, a line, a window of text, and the numbers to judge it by. Silica
never answers, summarises, plans or remembers for you. The harness owns the
loop.

| Tool | Returns |
|---|---|
| `silica_files` | the inventory and what the index did with each file: `indexed`, `changed`, `excluded`, `failed`, `unconverted` |
| `silica_search` | ranked passages: path, section, line, raw BM25, matched terms, `coverage`, and the query terms absent from the corpus |
| `silica_read` | a slice by lines or by heading (a page, in a PDF), the outline, and a `version` to carry forward |
| `silica_code_pack` | an AST context pack for one source file inside a character budget |
| `silica_write_note` | one atomic write, linted for structure and unresolved wikilinks |

`silica_search` indexes markdown and the PDFs that carry a text layer.
Source files are not in the index: `silica_files` lists a `.py` as
`excluded`, `silica_read` serves it by line and `silica_code_pack` by file.
To find a symbol in a repository, grep wins.

The contract, the reply shapes and the acceptance checks are in
[TOOLS.md](TOOLS.md). Nothing on that page needs an API key, a model or a
network.

## Install

```bash
uv tool install 'silica-core[mcp]'   # the command and the server
silica setup claude                  # register it — see Harnesses
npx skills add kiycoh/silica-core    # optional: the skill
```

`pipx install 'silica-core[mcp]'` works for the first line just as well. The
second registers the server at user scope, so every session serves the folder
the client was opened in; to serve another root, pass `--vault DIR` in the
server entry or export `SILICA_VAULT` in the client's `env` block, since a
`.env` file is not enough on purpose.

The package is `silica-core`, the command is `silica`, the tools are
`silica_*`: the distribution carries the name, the code keeps the namespace.

### Harnesses

`silica setup <client>` writes the registration into the client's own config.
It backs the file up first, leaves an existing `silica-core` entry alone, and
refuses a file that does not parse rather than overwriting it. `--dry-run`
prints the block without writing, `--config PATH` writes somewhere else,
`--from SPEC` swaps the release for a checkout.

| `silica setup …` | Harness | Writes |
|---|---|---|
| `claude` | Claude Code | delegates to `claude mcp add --scope user` |
| `codex` | Codex CLI | `~/.codex/config.toml`, and the skill into `~/.codex/skills` |
| `opencode` | opencode | `~/.config/opencode/opencode.json` |
| `dsh` | DeepSeek Harness | `~/.dsh/cordis.patch.yml`, and the skill into `~/.agents/skills` |
| `goose` | Goose | `~/.config/goose/config.yaml` |
| `openhands` | OpenHands | `config.toml`, in the folder OpenHands runs from |
| `gemini` | Gemini CLI | `~/.gemini/settings.json` |
| `cursor` | Cursor | `~/.cursor/mcp.json` |
| `windsurf` | Windsurf | `~/.codeium/windsurf/mcp_config.json` |
| `cline` | Cline | `cline_mcp_settings.json` in the extension's storage |
| `roo` | Roo Code | `mcp_settings.json` in the extension's storage |
| `continue` | Continue.dev | `~/.continue/config.yaml` |
| `zed` | Zed | `~/.config/zed/settings.json` |
| `claude-desktop` | Claude Desktop | `claude_desktop_config.json` |
| `lmstudio` | LM Studio | `~/.lmstudio/mcp.json` |
| `anythingllm` | AnythingLLM | `anythingllm_mcp_servers.json` |
| `librechat` | LibreChat | `librechat.yaml`, in the current folder |

Desktop paths follow the OS convention: `~/.config` on Linux, `~/Library/Application
Support` on macOS, `%APPDATA%` on Windows. `silica setup --list` prints this table
with every path resolved on your machine.

The rest install by instruction rather than by file, so `setup` prints the recipe
instead of failing:

| `silica setup …` | Covers |
|---|---|
| `shell` | Aider, SWE-agent, Plandex, Devin, SWE-bench — anything with a bash tool |
| `python` | LangGraph, smolagents, CrewAI, AutoGen, Inspect, METR |
| `generic` | Void, Jan, Factory Droid, Antigravity, Hermes — the MCP block to paste |

Cursor and Windsurf also read a project rule file (`.cursor/rules/silica.mdc`,
`.windsurfrules`). Those are yours, not your home's, so `setup` names them
rather than writing them; the content is `silica/skills/silica/SKILL.md`.

### The skill

`SKILL.md` is the wording that tells an agent when to reach for the tools.
`setup` installs it for the two harnesses whose skill root it knows (`codex`,
`dsh`). For every other one, [`npx skills`](https://github.com/vercel-labs/skills)
knows 79 of them:

```bash
npx skills add kiycoh/silica-core
```

It installs the skill and nothing else — no package, no server. So it is the
third line of the install, not a shortcut past the first two: on its own it
leaves an agent holding instructions for tools that are not there.

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

<p align="center">
  <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/search-hit.png" alt="silica search &quot;leveled compaction&quot; over nine LSM papers: the hit carries the file, p. 5 and the passage; beside it the PDF is open on that page with the same passage highlighted" width="900" />
</p>

Nine arXiv papers in a folder, indexed in 2.1 s. The hit names the file, the
page and the passage — and the page beside it is the check, not a promise.

## What it reads

A PDF with a text layer is searched as it is: the index reads the layer
itself, one section per page, so a hit reads `p. 7` and `silica_read` serves
that page alone. No conversion, no `.md` written beside it. The reply also
carries `extract_path`, the extracted text on disk, for `grep` and the
harness's own reader — the line numbers there are the ones `silica_read`
serves. A scan has no text layer and reads as `unconverted` until you
convert it. `silica import` writes that conversion beside the original,
refuses to replace a `.md` it did not write, and records the original's
SHA-256 and the extracting tool's version in the frontmatter; `silica_read`
and `silica_files` then report `source_state: stale` on either path when
the PDF changed under a `.md` that did not.

Extras: `[connect]` adds the Obsidian bridge, `[all]` both. The converters
for PDF (headings, figures), DOCX, EPUB, FB2, RTF, XLS and ODF need no extra.
Scanned PDFs, images, PPTX and XLSX go through [MinerU](https://github.com/opendatalab/MinerU)
when it is on your PATH; audio and video need `ffmpeg` and a speech-to-text
endpoint (`SILICA_STT_BASE_URL`). `silica doctor` says which lanes you have.

## How search says no

A search returns hits even when the corpus does not answer, because a
ranked list always has a top. What tells the two apart:

- `coverage`: the share of the query's idf mass the hit's matched terms
  carry. Near 1, every rare term matched; near 0, only the common words did.
  A term absent from the whole corpus still weighs in, at the idf of a term
  found nowhere, so absence pulls coverage down instead of vanishing from it.
- `terms_absent`: query terms that occur nowhere in the corpus.
- `matched_terms`: which words this hit actually contains.

<p align="center">
  <img src="https://raw.githubusercontent.com/kiycoh/silica-core/main/assets/search-says-no.png" alt="silica search &quot;raft consensus log replication&quot; over the same nine papers: terms_absent lists raft and consensus, coverage falls to 0.19, and the top hit is a passage about data replication rather than Raft" width="900" />
</p>

`raft consensus log replication` over the same nine papers: `raft` and
`consensus` occur in none of them, `coverage` falls to 0.19, and the top hit
matched `log` and `replication` — a passage about replicating data, not about
Raft. The ranking still has a top; the numbers beside it say what it is worth.

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

## What the measurements say

Leave the choice to the harness. Measured with Opus on 2026-09-09, 144 runs,
every run correct: no arm of any experiment separated on the answer, so what
moves is the work spent reaching it. Twelve questions over a 5.5M-token
corpus of papers, Silica reached for spontaneously, against the same harness
without the plugin: 3.9 turns instead of 5.0, 2.9 tool calls instead of 4.0,
$0.20 instead of $0.22 a task at a warm cache. The same answers for a
quarter fewer calls. On code the model never reaches for `silica_search`,
because the questions name identifiers and the descriptions say grep wins
there: the two resident schemas cost 13% on those tasks and return nothing.
Told to use the tools instead of grep it is worse everywhere: 49% over
spontaneous use on the papers, 1.7 times a plain grep on code, 3.2 times at
the worst task.

Put the pair in front of the model instead: `silica mcp` marks
`silica_search` and `silica_read` `anthropic/alwaysLoad`, so a client that
defers MCP tools behind its own tool search keeps those two in the list and
the other three ride behind them. On the same twelve document tasks the model
reached for a deferred search once, and a resident one twelve times. The
marker is declared, never inferred, and it has to reach the wire as `_meta`:
a wrapper that rebuilds the tool list carries it over itself.

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
