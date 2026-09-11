# Harnesses

Where `silica setup` writes, what it prints for the harnesses it cannot write
for, and the three surfaces that need no registration at all: the shell, the
Docker image and the REPL.

## silica setup

`silica setup <client>` writes the registration into the client's own config.
It backs the file up first, leaves an existing `silica-core` entry alone, and
refuses a file that does not parse rather than overwriting it. `--dry-run`
prints the block without writing, `--config PATH` writes somewhere else,
`--from SPEC` swaps the release for a checkout.

The block it writes launches `uvx --from 'silica-core[mcp,dense]' silica mcp
--retrieval local-hybrid`, the same server the Claude Code plugin runs: hybrid
search once the model and the vectors are there, lexical meanwhile, lexical
with `dense: failed` if the model never arrives. For the light server, edit the
written block to `--retrieval lexical` (`silica mcp` on its own is lexical).

| `silica setup …` | Harness | Writes |
|---|---|---|
| `claude` | Claude Code | delegates to `claude mcp add --scope user` |
| `codex` | Codex CLI | `~/.codex/config.toml`, and the skill into `~/.codex/skills` |
| `opencode` | opencode | `~/.config/opencode/opencode.json` |
| `dsh` | DeepSeek Harness | `~/.dsh/cordis.patch.yml`, and the skill into `~/.agents/skills` |
| `goose` | Goose | `~/.config/goose/config.yaml` |
| `openhands` | OpenHands | `config.toml`, in the folder OpenHands runs from |
| `hermes` | Hermes Agent | `~/.hermes/config.yaml`, and the skill into `~/.hermes/skills` |
| `openclaw` | OpenClaw | `~/.openclaw/openclaw.json` |
| `agent-zero` | Agent Zero | `usr/settings.json`, in its install root |
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

The registration is at user scope, so every session serves the folder the
client was opened in. To serve another root, pass `--vault DIR` in the server
entry or export `SILICA_VAULT` in the client's `env` block; a `.env` file is
not enough on purpose.

The rest install by instruction rather than by file, so `setup` prints the recipe
instead of failing:

| `silica setup …` | Covers |
|---|---|
| `shell` | Aider, SWE-agent, Plandex, Devin, SWE-bench, anything with a bash tool |
| `python` | LangGraph, smolagents, CrewAI, AutoGen, Inspect, METR |
| `generic` | Void, Jan, Factory Droid, Antigravity: the MCP block to paste |

Three of those need a word after the write. Hermes watches `config.yaml` and
reconnects on change, so nothing has to be restarted. OpenClaw's file is JSON5
by convention: plain JSON round-trips, but a file that actually uses comments or
trailing commas is refused rather than flattened, and `openclaw mcp add
silica-core --command uvx --arg …` writes the same entry. Agent Zero keeps the
server map serialised into a *string* field of `usr/settings.json`, spawns it
inside its own container, and connects on **Settings → MCP/A2A → Apply now**: so
`silica` has to be in that image, and `SILICA_VAULT` has to name a mounted path
or it indexes the container.

Cursor and Windsurf also read a project rule file (`.cursor/rules/silica.mdc`,
`.windsurfrules`). Those are yours, not your home's, so `setup` names them
rather than writing them; the content is `silica/skills/silica/SKILL.md`.

## The skill

`SKILL.md` is the wording that tells an agent when to reach for the tools.
`setup` installs it for the two harnesses whose skill root it knows (`codex`,
`dsh`). For every other one, [`npx skills`](https://github.com/vercel-labs/skills)
knows 79 of them:

```bash
npx skills add kiycoh/silica-core
```

It installs the skill and nothing else: no package, no server. On its own it
leaves an agent holding instructions for tools that are not there, so it
comes after `uv tool install 'silica-core[mcp,dense]'`, not instead of it.

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
silica mcp --retrieval local-hybrid            # potion in-process, index and vectors warmed up at start (what the plugin and `silica setup` run; needs [dense])
```

The dense leg from an endpoint instead of the in-process model, here ollama
with the prefixes nomic's model card asks for:

```bash
ollama pull nomic-embed-text
export SILICA_EMBEDDING_BASE_URL=http://localhost:11434/v1 SILICA_EMBEDDING_MODEL=nomic-embed-text
export SILICA_EMBEDDING_DOC_PREFIX='search_document: ' SILICA_EMBEDDING_QUERY_PREFIX='search_query: '
silica index --embed
```

A remote endpoint needs `silica index --embed --allow-remote` once; until
then search stays lexical and the reply says so in `dense`.

## Docker

No image is published. The `Dockerfile` builds one, and CI builds and smoke-tests
it on every push, so it stays runnable:

```bash
docker build -t silica-core .
docker run --rm -i -v /path/to/vault:/vault silica-core                 # the MCP server over stdio
docker run --rm -v /path/to/vault:/vault silica-core search "compaction" -k 5
```

The container serves `/vault` and keeps everything it derives (index, ledger,
checkpoints) under `/data`, which is `$HOME` inside the image: mount a volume
there or the index is rebuilt on every run. The lanes that need a system binary
(MinerU, ffmpeg, soffice) are deliberately not in the image; `docker run --rm
silica-core doctor` reports them missing, and the comments in the `Dockerfile`
say where to add the ones you use.

## The REPL

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
