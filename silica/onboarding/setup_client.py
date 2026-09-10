# SPDX-License-Identifier: MIT
# Copyright (C) 2026 Alessandro Carosia

"""`silica setup <client>` — wire the MCP server into a coding agent's config.

The read side of the vault needs no model and no key, so serving it over MCP is
the shortest path from install to something useful. What stood between the two
was hand-pasting a JSON or TOML block into a file whose location the user has to
look up. This writes that block instead.

Nearly every harness in docs/agent-harnesses.md wants the same object at a
different path under a different key, so the clients are a table (CLIENTS) read
by one writer, not one function each. Only four need their own: Claude Code
(ships `claude mcp add`), DeepSeek Harness (a list of Cordis patches, not a map
of servers), Agent Zero (a map serialised into a *string* field) and the TOML
files, which are appended as text.

Never clobbers: an existing silica-core entry is left alone (the user may have tuned
it), the file is backed up before any write, and `--dry-run` prints what would
change. A file that does not parse is refused rather than overwritten.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

import yaml


# Everything this module prints carries a payload full of square brackets: the
# `[mcp]` extra, TOML table headers, a parser error quoting a `]`. rich reads a
# bracketed word as a style tag and drops it, which silently turned the printed
# install command into `--from silica-core` — a command that runs and installs
# the wrong thing. Every interpolated value goes through escape(), and the
# config block (which has no styling of its own) prints with markup off.

# What every client is told to run. uvx keeps the server at one command with no
# install step of its own, which is the whole point of the generated block.
MCP_COMMAND = ["uvx", "--from", "silica-core[mcp]", "silica", "mcp"]
FROM_SPEC = MCP_COMMAND[2]  # `silica setup <client> --from SPEC` swaps the release for a checkout or a pin

NAME = "silica-core"

# The read tools take no action and cost no tokens beyond their reply, so a
# client that can pre-approve them should: a confirmation click per search is
# what makes an evidence loop unusable.
AUTO_APPROVE = ["silica_files", "silica_search", "silica_read", "silica_code_pack"]


def mcp_command() -> list[str]:
    return [MCP_COMMAND[0], "--from", FROM_SPEC, *MCP_COMMAND[3:]]

# No SILICA_VAULT in the generated block, deliberately. Claude Code, Codex and
# opencode are CLIs launched from a project and spawn the stdio server with
# that working directory, which is already the answer (`cli.resolve_cwd_vault`),
# so the server serves the project you opened — the Claude Code model, one
# vault per place. Writing the vault that happened to be active at `silica
# setup` time would pin it into every project afterwards, which is what
# `_activate_repo_mode` warns against. A fixed vault is still expressible:
# export SILICA_VAULT, or add the env block by hand to the file this wrote.
# DeepSeek Harness is the one client where that pin is worth considering: its
# `dsh web` process spawns the server once, in its own launch folder, for
# every session it serves (see `_setup_dsh`).

# Codex gives a stdio server `startup_timeout_sec` (default 10) to answer
# `initialize`. A cold `uvx` resolves and installs silica-core[mcp] first,
# which takes longer than that on a first run, and a server that misses the
# window is simply absent for that session, with one line in a log nobody
# reads.
CODEX_STARTUP_TIMEOUT_SEC = 60


def escape(text: str) -> str:  # rich is gone; the name stays for the call sites
    return text


def _say(msg: str, **_kw) -> None:
    print(msg)


# ---------------------------------------------------------------- where files live

def _app_support(app: str) -> Path:
    """The per-user config root a desktop app uses on this OS."""
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / app
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / app
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / app


def _vscode_global(extension: str) -> Path:
    """A VS Code extension's globalStorage settings folder."""
    return _app_support("Code") / "User" / "globalStorage" / extension / "settings"


def _home(*parts: str) -> Callable[[], Path]:
    return lambda: Path.home().joinpath(*parts)


def _xdg(*parts: str) -> Callable[[], Path]:
    # Zed and Goose read ~/.config on macOS too, so this is not _app_support.
    return lambda: Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config").joinpath(*parts)


def _codex_skills() -> Path:
    """Codex reads $CODEX_HOME/skills, ~/.codex/skills when that is unset.

    Verified against the codex-cli 0.153.4 binary, which carries both
    "Installs into `$CODEX_HOME/skills/<skill-name>` (defaults to
    `~/.codex/skills`)" and "create discoverable skills in `$CODEX_HOME/skills`".
    `.agents/skills` occurs in that binary only among the external-agent
    migration strings, next to `.cursorrules` and `claude-code` — a root it
    reads *from*, not one it serves skills out of.
    """
    return Path(os.environ.get("CODEX_HOME") or Path.home() / ".codex") / "skills"


def _dsh_path() -> Path:
    # resolveDshHome in the harness: $DSH_HOME, else ~/.dsh. The home-level
    # patch file is the layer applied over every profile.
    return Path(os.environ.get("DSH_HOME") or Path.home() / ".dsh") / "cordis.patch.yml"


def _hermes_home() -> Path:
    # get_hermes_home() in hermes_constants: $HERMES_HOME, else ~/.hermes.
    # config.yaml holds the servers, skills/ the SKILL.md tree it scans.
    return Path(os.environ.get("HERMES_HOME") or Path.home() / ".hermes")


def _openclaw_path() -> Path:
    # The gateway reads ~/.openclaw/openclaw.json and watches it, unless
    # OPENCLAW_CONFIG_PATH moves the file.
    return Path(os.environ.get("OPENCLAW_CONFIG_PATH") or Path.home() / ".openclaw" / "openclaw.json")


def _agent_zero_path() -> Path:
    # SETTINGS_FILE in its helpers/settings.py is usr/settings.json under the
    # install root, which is the folder Agent Zero runs from.
    return Path("usr") / "settings.json"


# ---------------------------------------------------------------- what goes in them

def _stdio() -> dict:
    """The block nine clients out of ten want, verbatim."""
    cmd = mcp_command()
    return {"command": cmd[0], "args": cmd[1:]}


def _cline_entry() -> dict:
    return {**_stdio(), "disabled": False, "autoApprove": list(AUTO_APPROVE)}


def _openclaw_entry() -> dict:
    # transport and enabled are what the gateway checks before it dials: a
    # definition missing either is stored and never connected.
    return {**_stdio(), "transport": "stdio", "enabled": True}


def _goose_entry() -> dict:
    cmd = mcp_command()
    return {"type": "stdio", "name": NAME, "cmd": cmd[0], "args": cmd[1:], "enabled": True, "timeout": 300}


def _codex_block() -> str:
    cmd = mcp_command()
    args = ", ".join(f'"{a}"' for a in cmd[1:])
    return (
        f"\n[mcp_servers.{NAME}]\n"
        f'command = "{cmd[0]}"\n'
        f"args = [{args}]\n"
        f"startup_timeout_sec = {CODEX_STARTUP_TIMEOUT_SEC}\n"
    )


def _openhands_block() -> str:
    cmd = mcp_command()
    args = ", ".join(f'"{a}"' for a in cmd[1:])
    return (
        "\n[[mcp.stdio_servers]]\n"
        f'name = "{NAME}"\n'
        f'command = "{cmd[0]}"\n'
        f"args = [{args}]\n"
    )


@dataclass(frozen=True)
class Spec:
    """One harness: where its config lives, and what shape the entry takes."""
    label: str
    fmt: str                                   # json | yaml | toml
    path: Callable[[], Path]
    key: tuple[str, ...]                       # walk to the container of servers
    entry: Callable[[], dict] | None = None    # json/yaml: the value to merge in
    block: Callable[[], str] | None = None     # toml: text to append instead
    kind: str = "map"                          # map -> keyed by NAME; list -> appended
    skills: Callable[[], Path] | None = None   # this client's skill root, if it reads one
    note: str = ""                             # one line printed after a write


CLIENTS: dict[str, Spec] = {
    # --- terminal agents ------------------------------------------------------
    "codex": Spec("Codex CLI", "toml", _home(".codex", "config.toml"),
                  ("mcp_servers",), block=_codex_block, skills=_codex_skills),
    "opencode": Spec("opencode", "json", _xdg("opencode", "opencode.json"), ("mcp",),
                     entry=lambda: {"type": "local", "command": mcp_command(), "enabled": True}),
    "goose": Spec("Goose", "yaml", _xdg("goose", "config.yaml"), ("extensions",), entry=_goose_entry),
    "openhands": Spec("OpenHands", "toml", lambda: Path("config.toml"),
                      ("mcp", "stdio_servers"), block=_openhands_block, kind="list",
                      note="config.toml is read from the folder OpenHands runs in — "
                           "pass --config for another one"),
    "gemini": Spec("Gemini CLI", "json", _home(".gemini", "settings.json"), ("mcpServers",), entry=_stdio),
    "hermes": Spec("Hermes Agent", "yaml", lambda: _hermes_home() / "config.yaml", ("mcp_servers",),
                   entry=_stdio, skills=lambda: _hermes_home() / "skills",
                   note="Hermes watches config.yaml and reconnects MCP on change — no restart"),
    # openclaw.json is JSON5 by convention (comments, trailing commas). Plain
    # JSON is the common case and round-trips here; a file that uses the JSON5
    # part is refused by _setup_config rather than flattened, and
    # `openclaw mcp add silica-core --command uvx --arg …` writes the same entry.
    "openclaw": Spec("OpenClaw", "json", _openclaw_path, ("mcp", "servers"), entry=_openclaw_entry,
                     note="the gateway hot-reloads the file; "
                          "`openclaw mcp doctor silica-core --probe` proves the server answers"),
    # --- IDEs and editors -----------------------------------------------------
    "cursor": Spec("Cursor", "json", _home(".cursor", "mcp.json"), ("mcpServers",), entry=_stdio,
                   note="for the agent rule too: copy silica/skills/silica/SKILL.md "
                        "to .cursor/rules/silica.mdc in your project"),
    "windsurf": Spec("Windsurf", "json", _home(".codeium", "windsurf", "mcp_config.json"),
                     ("mcpServers",), entry=_stdio,
                     note="for the agent rule too: copy silica/skills/silica/SKILL.md to .windsurfrules"),
    "cline": Spec("Cline", "json", lambda: _vscode_global("saoudrizwan.claude-dev") / "cline_mcp_settings.json",
                  ("mcpServers",), entry=_cline_entry),
    "roo": Spec("Roo Code", "json", lambda: _vscode_global("rooveterinaryinc.roo-cline") / "mcp_settings.json",
                ("mcpServers",), entry=_cline_entry),
    "continue": Spec("Continue.dev", "yaml", _home(".continue", "config.yaml"), ("mcpServers",),
                     entry=lambda: {"name": NAME, **_stdio()}, kind="list"),
    "zed": Spec("Zed", "json", _xdg("zed", "settings.json"), ("context_servers",),
                entry=lambda: {"source": "custom", **_stdio()}),
    # --- desktop and web clients ---------------------------------------------
    "claude-desktop": Spec("Claude Desktop", "json",
                           lambda: _app_support("Claude") / "claude_desktop_config.json",
                           ("mcpServers",), entry=_stdio),
    "lmstudio": Spec("LM Studio", "json", _home(".lmstudio", "mcp.json"), ("mcpServers",), entry=_stdio),
    "anythingllm": Spec("AnythingLLM", "json",
                        lambda: _app_support("anythingllm-desktop") / "storage" / "plugins"
                        / "anythingllm_mcp_servers.json",
                        ("mcpServers",), entry=_stdio),
    "librechat": Spec("LibreChat", "yaml", lambda: Path("librechat.yaml"), ("mcpServers",), entry=_stdio,
                      note="librechat.yaml is the deployment's own file — pass --config for another one"),
}

# Harnesses with no config file to write: they either run shell commands, import
# Python, or take the block through a settings UI. The install is a recipe, so
# `silica setup <name>` prints it instead of failing.
RECIPES = {
    "shell": (
        "Aider, SWE-agent, Plandex, Devin, SWE-bench, and any harness with a bash tool:",
        "  pip install 'silica-core[mcp]'   # or add it to the image / setup playbook",
        "  silica search 'query' -k 5",
        "  silica read path/file.md --section 'Section'",
        "  silica code-pack src/module.py --budget 10000",
        "Every command answers with the same JSON the MCP tools return, on stdout.",
        "Tell the agent to run those before opening files (SKILL.md is the wording).",
    ),
    "python": (
        "LangGraph, smolagents, CrewAI, AutoGen, Inspect, METR — spawn the server:",
        "  from langchain_mcp_adapters.client import MultiServerMCPClient",
        "  client = MultiServerMCPClient({'silica': {",
        "      'command': 'uvx', 'args': ['--from', 'silica-core[mcp]', 'silica', 'mcp']}})",
        "or skip MCP and call the library directly:",
        "  from silica.core import search, read   # same payloads, no subprocess",
    ),
    "generic": (
        "Any other MCP client (Void, Jan, Factory Droid, Antigravity) —",
        "paste this into its server registry:",
        '  {"command": "uvx", "args": ["--from", "silica-core[mcp]", "silica", "mcp"]}',
        "Harnesses that also read skills: copy silica/skills/silica/SKILL.md into their",
        "skill root (~/.agents/skills/silica/SKILL.md serves Codex and DSH; Antigravity",
        "uses ~/.gemini/antigravity-cli/skills/).",
    ),
}

# Kept for the usage line and for callers that only want the automated ones.
CLIENT_NAMES = ("claude", "dsh", "agent-zero", *sorted(CLIENTS), *RECIPES)


def skill_path() -> Path:
    """The one SKILL.md. It ships inside the package so an installed `silica`
    can copy it into a client's skill root, and the plugin manifests point at
    the same file, so there is nothing to keep in step."""
    return Path(str(files("silica") / "skills" / "silica" / "SKILL.md"))


# DeepSeek Harness scans the shared ~/.agents root. Codex does not, whatever
# an earlier version of this file claimed: its own root is $CODEX_HOME/skills.
def _dsh_skills() -> Path:
    return Path.home() / ".agents" / "skills"


def install_skill(root: Path) -> Path:
    """Copy the skill into one client's skill root. Claude Code gets it from
    the plugin instead. A copy and not a symlink: the package path moves on
    upgrade and symlinks need privileges on Windows; rerunning setup refreshes
    it. For every other harness `npx skills add kiycoh/silica-core` knows more
    roots than this file ever will."""
    dest = root / "silica" / "SKILL.md"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(skill_path(), dest)
    return dest


def _default_path(client: str) -> Path:
    if client == "dsh":
        return _dsh_path()
    if client == "agent-zero":
        return _agent_zero_path()
    return CLIENTS[client].path()


def _backup(path: Path) -> Path:
    """Timestamped copy beside the original, returned for the report."""
    dest = path.with_suffix(path.suffix + f".bak.{time.strftime('%Y%m%d-%H%M%S')}")
    shutil.copy2(path, dest)
    return dest


def _report(path: Path, block: str, dry_run: bool, backup: Path | None) -> int:
    if dry_run:
        _say(f"  would write to {escape(str(path))}:")
        _say(block, markup=False)
        return 0
    _say(f"  ✓ wrote {escape(str(path))}")
    if backup:
        _say(f"  backup: {escape(str(backup))}")
    return 0


# ---------------------------------------------------------------- the one writer

PARSE_ERRORS = (json.JSONDecodeError, yaml.YAMLError, tomllib.TOMLDecodeError)


def _parse(text: str, fmt: str) -> dict:
    if not text.strip():
        return {}
    if fmt == "json":
        return json.loads(text)
    if fmt == "yaml":
        return yaml.safe_load(text) or {}
    return tomllib.loads(text)


def _dump(data: dict, fmt: str) -> str:
    if fmt == "json":
        return json.dumps(data, indent=2) + "\n"
    return yaml.safe_dump(data, sort_keys=False, allow_unicode=True)


def _container(data: dict, key: tuple[str, ...], kind: str):
    """Walk (and create) the path to the map or list the entry belongs in."""
    node: dict = data
    for k in key[:-1]:
        node = node.setdefault(k, {})
        if not isinstance(node, dict):
            return None
    node.setdefault(key[-1], [] if kind == "list" else {})
    return node[key[-1]]


def _present(container, kind: str) -> bool:
    if kind == "list":
        return any(isinstance(e, dict) and NAME in (e.get("name"), e.get("id"), e.get("serverName"))
                   for e in container)
    return NAME in container


def _setup_config(spec: Spec, path: Path, dry_run: bool) -> int:
    """Merge silica-core into one client's config file.

    ponytail: TOML is appended as text rather than re-serialised. tomllib reads
    but cannot write, and a real TOML writer is a dependency for two blocks —
    appending also preserves the comments and ordering a round-trip flattens.
    The ceiling is that it only ever adds; the day silica has to edit an
    existing entry is the day tomlkit earns its place.
    """
    where = escape(str(path))
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    try:
        data = _parse(existing, spec.fmt)
    except PARSE_ERRORS as e:
        _say(f"  ✗ {where} is not valid {spec.fmt.upper()} ({escape(str(e))}) — not touching it")
        return 1
    if not isinstance(data, dict):
        _say(f"  ✗ {where} is not a {spec.fmt.upper()} object — not touching it")
        return 1
    container = _container(data, spec.key, spec.kind)
    wanted = list if spec.kind == "list" else dict
    if not isinstance(container, wanted):
        _say(f"  ✗ {where}: {'.'.join(spec.key)} is not a {spec.kind} — not touching it")
        return 1
    if _present(container, spec.kind):
        _say(f"  {NAME} is already configured in {where} — nothing to do")
        return 0

    if spec.block:
        block = spec.block()
        new = existing + ("" if not existing or existing.endswith("\n") else "\n") + block
    else:
        entry = spec.entry()  # type: ignore[misc]
        if isinstance(container, list):
            container.append(entry)
        else:
            container[NAME] = entry
        block = new = _dump(data, spec.fmt)

    if dry_run:
        return _report(path, block, True, None)
    backup = _backup(path) if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(new, encoding="utf-8")
    rc = _report(path, block, False, backup)
    if spec.note:
        _say(f"  {spec.note}")
    return rc


# ---------------------------------------------------------------- the exceptions

def _dsh_row() -> dict:
    return {
        "id": "mcp-silica-core",
        "name": "@deepseek-ai/dsh-mcp-client",
        "config": {
            "serverName": NAME,
            "transport": "stdio",
            "command": mcp_command()[0],
            "args": mcp_command()[1:],
        },
    }


def _setup_dsh(path: Path, dry_run: bool) -> int:
    """Insert the MCP client row into the harness's user patch file.

    DeepSeek Harness composes one `dsh-mcp-client` row per server and reads
    `cordis.patch.yml` as a top-level list of patches, so the row rides an
    `insert` entry appended to that list. Re-serialised through yaml rather
    than appended as text: the file is one YAML document, and text appended
    after a list can land inside the previous entry's indentation. Comments
    do not survive the round trip, which is what the backup is for.

    The harness spawns this child once per process, in the folder `dsh web`
    was launched from, so on this client the vault is that folder.
    """
    patches: list = []
    existing = path.read_text(encoding="utf-8") if path.exists() else ""
    if existing.strip():
        try:
            patches = yaml.safe_load(existing) or []
        except yaml.YAMLError as e:
            _say(f"  ✗ {escape(str(path))} is not valid YAML ({escape(str(e))}) — not touching it")
            return 1
        if not isinstance(patches, list):
            _say(f"  ✗ {escape(str(path))} is not a list of patches — not touching it")
            return 1
        rows = [r for p in patches if isinstance(p, dict) for r in p.get("insert") or [] if isinstance(r, dict)]
        if any(r.get("id") == "mcp-silica-core" for r in rows):
            _say(f"  {NAME} is already configured in {escape(str(path))} — nothing to do")
            return 0
    patches.append({"insert": [_dsh_row()]})
    block = yaml.safe_dump(patches, sort_keys=False, allow_unicode=True)
    if dry_run:
        return _report(path, block, True, None)
    backup = _backup(path) if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(block, encoding="utf-8")
    return _report(path, block, False, backup)


# What the settings tab starts with, and what a file without the field means.
AGENT_ZERO_EMPTY = '{\n    "mcpServers": {}\n}'


def _setup_agent_zero(path: Path, dry_run: bool) -> int:
    """Add the server to Agent Zero's settings file.

    `mcp_servers` is not a map in settings.json: it is a *string* carrying the
    `{"mcpServers": {...}}` JSON the MCP/A2A tab edits, so the table writer
    cannot reach it — this parses that string, adds the entry, and serialises
    it back. Agent Zero drops keys it does not know and fills the ones it is
    missing from its defaults when it reads the file (normalize_settings), so a
    settings.json carrying nothing but this field is still a valid one.

    ponytail: only the documented `{"mcpServers": {…}}` shape is edited. Their
    parser also takes a bare list of servers; a file in that shape is refused
    rather than converted, because rewriting a user's config into another legal
    shape is the one thing this module never does.
    """
    where = escape(str(path))
    settings: dict = {}
    if path.exists():
        try:
            settings = json.loads(path.read_text(encoding="utf-8") or "{}")
        except json.JSONDecodeError as e:
            _say(f"  ✗ {where} is not valid JSON ({escape(str(e))}) — not touching it")
            return 1
        if not isinstance(settings, dict):
            _say(f"  ✗ {where} is not a JSON object — not touching it")
            return 1
    raw = settings.get("mcp_servers") or AGENT_ZERO_EMPTY
    if not isinstance(raw, str):
        _say(f"  ✗ {where}: mcp_servers is not a JSON string — not touching it")
        return 1
    try:
        config = json.loads(raw)
    except json.JSONDecodeError as e:
        _say(f"  ✗ {where}: mcp_servers does not parse ({escape(str(e))}) — not touching it")
        return 1
    servers = config.get("mcpServers") if isinstance(config, dict) else None
    if not isinstance(servers, dict):
        _say(f'  ✗ {where}: mcp_servers is not a {{"mcpServers": …}} object — not touching it')
        return 1
    if NAME in servers:
        _say(f"  {NAME} is already configured in {where} — nothing to do")
        return 0

    servers[NAME] = _stdio()
    settings["mcp_servers"] = json.dumps(config, indent=4)
    block = json.dumps(settings, indent=4) + "\n"
    if dry_run:
        return _report(path, block, True, None)
    backup = _backup(path) if path.exists() else None
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(block, encoding="utf-8")
    rc = _report(path, block, False, backup)
    _say("  Agent Zero spawns the server inside its own container: install silica in that "
         "image and point SILICA_VAULT at a mounted path, or it indexes the container")
    _say("  then Settings → MCP/A2A → Apply now (or restart) to connect it")
    return rc


def _setup_claude(dry_run: bool) -> int:
    """Delegate to `claude mcp add`.

    Claude Code owns its own config format and ships a command for exactly this,
    so writing the file by hand would be a second implementation to keep in sync
    with theirs. When the CLI is absent, printing the command is still the whole
    answer.

    User scope, not the `local` default: the entry names no vault, so the one
    registration serves every project (each resolving its own vault from the cwd
    Claude spawns the server in). Per-project scope would mean re-running this
    in each repo to say the same thing.
    """
    cmd = ["claude", "mcp", "add", "--scope", "user",
           "--transport", "stdio", NAME, "--", *mcp_command()]
    printable = " ".join(cmd)
    if dry_run or not shutil.which("claude"):
        if not dry_run:
            _say("  ⚠ the `claude` CLI is not on PATH — run this yourself:")
        # soft_wrap so rich does not fold the line at the console width: this is
        # a command meant to be copied, and a wrap puts a real newline in the
        # middle of it, so pasting runs a fragment.
        _say(f"  {printable}", markup=False, soft_wrap=True)
        return 0
    result = subprocess.run(cmd)
    if result.returncode != 0:
        _say(f"  ✗ `{escape(printable)}` failed")
        return result.returncode
    _say("  ✓ registered with Claude Code (user scope: every project, vault from its folder)")
    _say(
        "  for the skill and the session hooks too: "
        "claude plugin marketplace add kiycoh/silica-core && "
        "claude plugin install silica-core@silica-core"
    )
    return 0


# ---------------------------------------------------------------- entry point

def _list_clients() -> int:
    _say("  writes the config for you — silica setup <client>:", markup=False)
    _say("    claude            Claude Code (delegates to `claude mcp add`)", markup=False)
    _say("    dsh               DeepSeek Harness (Cordis patch)", markup=False)
    _say(f"    {'agent-zero':<17} Agent Zero — {escape(str(_agent_zero_path()))} under its install root",
         markup=False)
    for name in sorted(CLIENTS):
        spec = CLIENTS[name]
        _say(f"    {name:<17} {spec.label} — {escape(str(spec.path()))}", markup=False)
    _say("", markup=False)
    _say("  prints the recipe — silica setup <topic>:", markup=False)
    for name, lines in RECIPES.items():
        _say(f"    {name:<17} {lines[0]}", markup=False)
    return 0


def run_setup(args: list[str]) -> int:
    """`silica setup <client> [--dry-run] [--config PATH] [--from SPEC]`."""
    if "--list" in args:
        return _list_clients()
    positional = [a for a in args if not a.startswith("-")]
    client = positional[0] if positional else ""
    known = client in ("claude", "dsh", "agent-zero") or client in CLIENTS or client in RECIPES
    if not known:
        _say("  Usage: silica setup <client> [--dry-run] [--config PATH] [--from SPEC]", markup=False)
        _say("         silica setup --list   every harness this knows how to wire up", markup=False)
        return 1
    dry_run = "--dry-run" in args
    global FROM_SPEC
    FROM_SPEC = next((a.split("=", 1)[1] for a in args if a.startswith("--from=")), MCP_COMMAND[2])
    if "--from" in args and args.index("--from") + 1 < len(args):
        FROM_SPEC = args[args.index("--from") + 1]

    if client in RECIPES:
        for line in RECIPES[client]:
            _say(f"  {line}", markup=False)
        return 0
    if client == "claude":
        return _setup_claude(dry_run)

    override = next((a.split("=", 1)[1] for a in args if a.startswith("--config=")), "")
    if not override and "--config" in args:
        i = args.index("--config")
        override = args[i + 1] if i + 1 < len(args) else ""
    path = Path(override).expanduser() if override else _default_path(client)
    skills: Callable[[], Path] | None
    if client == "dsh":
        rc, skills = _setup_dsh(path, dry_run), _dsh_skills
    elif client == "agent-zero":
        rc, skills = _setup_agent_zero(path, dry_run), None
    else:
        spec = CLIENTS[client]
        rc, skills = _setup_config(spec, path, dry_run), spec.skills
    # Also on "already configured": rerunning setup is how the skill copy
    # follows a package upgrade.
    if rc == 0 and not dry_run and skills is not None:
        _say(f"  ✓ skill installed at {escape(str(install_skill(skills())))}")
    return rc
