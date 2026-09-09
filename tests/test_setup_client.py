"""`silica setup <client>`: merge, never clobber, and stay idempotent."""
from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest
import yaml

from silica.onboarding import setup_client


@pytest.fixture(autouse=True)
def _home(tmp_path, monkeypatch):
    # codex/dsh setups copy the skill under ~/.agents; the suite must never
    # write into the developer's real home.
    monkeypatch.setenv("HOME", str(tmp_path))


def test_codex_appends_block(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('model = "gpt-5"\n', encoding="utf-8")
    assert setup_client.run_setup(["codex", "--config", str(cfg)]) == 0
    parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert parsed["model"] == "gpt-5"  # existing content survives
    assert parsed["mcp_servers"]["silica-core"]["command"] == "uvx"
    # The [mcp] extra is the point of the block, and rich markup eats it if the
    # payload is ever printed or written through a markup-enabled path.
    assert parsed["mcp_servers"]["silica-core"]["args"] == ["--from", "silica-core[mcp]", "silica", "mcp"]


def test_no_client_gets_a_pinned_vault(tmp_path):
    """The generated config must not carry SILICA_VAULT.

    The server resolves the vault from the working directory its client spawns
    it in, so a pin written once at setup time would serve that one vault to
    every project the user ever opens.
    """
    toml_cfg = tmp_path / "config.toml"
    setup_client.run_setup(["codex", "--config", str(toml_cfg)])
    assert "SILICA_VAULT" not in toml_cfg.read_text(encoding="utf-8")

    json_cfg = tmp_path / "opencode.json"
    setup_client.run_setup(["opencode", "--config", str(json_cfg)])
    assert "SILICA_VAULT" not in json_cfg.read_text(encoding="utf-8")

    yml_cfg = tmp_path / "cordis.patch.yml"
    setup_client.run_setup(["dsh", "--config", str(yml_cfg)])
    assert "SILICA_VAULT" not in yml_cfg.read_text(encoding="utf-8")

    assert "SILICA_VAULT" not in _printed(["claude", "--dry-run"])


def test_codex_is_idempotent(tmp_path):
    cfg = tmp_path / "config.toml"
    assert setup_client.run_setup(["codex", "--config", str(cfg)]) == 0
    once = cfg.read_text(encoding="utf-8")
    assert setup_client.run_setup(["codex", "--config", str(cfg)]) == 0
    assert cfg.read_text(encoding="utf-8") == once


def test_codex_refuses_broken_toml(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text("this is [not toml\n", encoding="utf-8")
    assert setup_client.run_setup(["codex", "--config", str(cfg)]) == 1
    assert cfg.read_text(encoding="utf-8") == "this is [not toml\n"


def test_opencode_merges_into_existing_json(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text(json.dumps({"theme": "dark", "mcp": {"other": {}}}), encoding="utf-8")
    assert setup_client.run_setup(["opencode", "--config", str(cfg)]) == 0
    data = json.loads(cfg.read_text(encoding="utf-8"))
    assert data["theme"] == "dark"
    assert "other" in data["mcp"]
    assert data["mcp"]["silica-core"]["command"][0] == "uvx"


def test_dry_run_writes_nothing(tmp_path):
    cfg = tmp_path / "opencode.json"
    assert setup_client.run_setup(["opencode", "--config", str(cfg), "--dry-run"]) == 0
    assert not cfg.exists()


def test_backup_taken_before_write(tmp_path):
    cfg = tmp_path / "opencode.json"
    cfg.write_text('{"theme": "dark"}', encoding="utf-8")
    setup_client.run_setup(["opencode", "--config", str(cfg)])
    backups = list(tmp_path.glob("opencode.json.bak.*"))
    assert len(backups) == 1
    assert json.loads(backups[0].read_text(encoding="utf-8")) == {"theme": "dark"}


def _printed(args: list[str], capsys=None) -> str:
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        setup_client.run_setup(args)
    return " ".join(buf.getvalue().split())


def test_previews_keep_the_bracketed_tokens(tmp_path, capsys):
    """`[mcp]` and the TOML headers must reach the terminal intact."""
    out = _printed(["codex", "--config", str(tmp_path / "config.toml"), "--dry-run"], capsys)
    assert "silica-core[mcp]" in out
    assert "[mcp_servers.silica-core]" in out
    assert "silica-core[mcp]" in _printed(["claude", "--dry-run"], capsys)
    assert "[--dry-run]" in _printed(["nonsense"], capsys)


def test_claude_command_is_one_pastable_line(capsys):
    setup_client.run_setup(["claude", "--dry-run"])
    assert capsys.readouterr().out.strip().count("\n") == 0


def test_unknown_client_is_an_error(tmp_path):
    assert setup_client.run_setup(["emacs-doctor"]) == 1
    assert setup_client.run_setup([]) == 1


def test_codex_block_outlives_a_cold_uvx(tmp_path):
    # Codex gives a stdio server 10 s to answer `initialize`; a first-run uvx
    # resolve of silica-core[mcp] takes longer than that on a cold cache.
    cfg = tmp_path / "config.toml"
    setup_client.run_setup(["codex", "--config", str(cfg)])
    parsed = tomllib.loads(cfg.read_text(encoding="utf-8"))
    assert parsed["mcp_servers"]["silica-core"]["startup_timeout_sec"] == 60


def test_dsh_inserts_the_mcp_client_row(tmp_path):
    cfg = tmp_path / "cordis.patch.yml"
    assert setup_client.run_setup(["dsh", "--config", str(cfg)]) == 0
    patches = yaml.safe_load(cfg.read_text(encoding="utf-8"))
    rows = [r for p in patches for r in p.get("insert", [])]
    row = next(r for r in rows if r["id"] == "mcp-silica-core")
    assert row["name"] == "@deepseek-ai/dsh-mcp-client"
    c = row["config"]
    assert (c["serverName"], c["transport"]) == ("silica-core", "stdio")
    assert [c["command"], *c["args"]] == setup_client.MCP_COMMAND


def test_dsh_keeps_other_patches_and_is_idempotent(tmp_path):
    cfg = tmp_path / "cordis.patch.yml"
    cfg.write_text("- id: llm\n  config:\n    model: deepseek-v4\n", encoding="utf-8")
    assert setup_client.run_setup(["dsh", "--config", str(cfg)]) == 0
    once = cfg.read_text(encoding="utf-8")
    assert "model: deepseek-v4" in once
    assert setup_client.run_setup(["dsh", "--config", str(cfg)]) == 0
    assert cfg.read_text(encoding="utf-8") == once


def test_dsh_refuses_a_patch_file_that_is_not_a_list(tmp_path):
    cfg = tmp_path / "cordis.patch.yml"
    cfg.write_text("key: value\n", encoding="utf-8")
    assert setup_client.run_setup(["dsh", "--config", str(cfg)]) == 1
    assert cfg.read_text(encoding="utf-8") == "key: value\n"


def test_dsh_default_path_honours_dsh_home(monkeypatch):
    monkeypatch.setenv("DSH_HOME", "/srv/dsh")
    assert setup_client._default_path("dsh") == Path("/srv/dsh/cordis.patch.yml")


def test_skill_lands_in_the_shared_agents_root(tmp_path, monkeypatch):
    # Codex and DeepSeek Harness both discover ~/.agents/skills, so one copy
    # serves both; Claude Code gets the skill from the plugin instead.
    monkeypatch.setenv("HOME", str(tmp_path))
    for client, cfg in (("codex", "config.toml"), ("dsh", "cordis.patch.yml")):
        assert setup_client.run_setup([client, "--config", str(tmp_path / cfg)]) == 0
        installed = tmp_path / ".agents" / "skills" / "silica" / "SKILL.md"
        assert installed.read_text(encoding="utf-8") == setup_client.skill_path().read_text(encoding="utf-8")
        installed.unlink()


def test_dry_run_installs_no_skill(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    setup_client.run_setup(["codex", "--config", str(tmp_path / "config.toml"), "--dry-run"])
    assert not (tmp_path / ".agents").exists()


def test_from_spec_swaps_the_release_for_a_checkout(tmp_path, capsys):
    out = _printed(["codex", "--config", str(tmp_path / "config.toml"), "--dry-run", "--from", "/src/silica[mcp]"], capsys)
    assert '"--from", "/src/silica[mcp]", "silica", "mcp"' in out
    assert "silica-core[mcp]" in _printed(["codex", "--config", str(tmp_path / "c2.toml"), "--dry-run"], capsys)


# --- the table-driven clients ------------------------------------------------
#
# Every harness in docs/agent-harnesses.md that has a config file to write is a
# row in CLIENTS read by one writer, so the suite tests the writer once and then
# asserts each row lands the block its client actually reads.


@pytest.mark.parametrize("client", sorted(setup_client.CLIENTS))
def test_every_client_writes_merges_and_repeats_cleanly(client, tmp_path):
    """One pass per row: writes, keeps what was there, and is a no-op twice."""
    spec = setup_client.CLIENTS[client]
    cfg = tmp_path / spec.path().name
    prior = {"json": '{"unrelated": 1}\n', "yaml": "unrelated: 1\n", "toml": "unrelated = 1\n"}[spec.fmt]
    cfg.write_text(prior, encoding="utf-8")

    assert setup_client.run_setup([client, "--config", str(cfg)]) == 0
    text = cfg.read_text(encoding="utf-8")
    assert "unrelated" in text  # merged, not clobbered
    assert "silica-core[mcp]" in text
    assert "SILICA_VAULT" not in text  # the vault is the folder the client spawns us in

    once = text
    assert setup_client.run_setup([client, "--config", str(cfg)]) == 0
    assert cfg.read_text(encoding="utf-8") == once


@pytest.mark.parametrize("client", sorted(setup_client.CLIENTS))
def test_every_client_refuses_a_file_it_cannot_parse(client, tmp_path):
    spec = setup_client.CLIENTS[client]
    cfg = tmp_path / spec.path().name
    broken = {"json": "{not json", "yaml": "a:\n  - b\n c\n", "toml": "this is [not toml\n"}[spec.fmt]
    cfg.write_text(broken, encoding="utf-8")
    assert setup_client.run_setup([client, "--config", str(cfg)]) == 1
    assert cfg.read_text(encoding="utf-8") == broken


def test_the_entry_lands_where_the_client_looks_for_it(tmp_path):
    """The key path is the whole contract: a valid block under the wrong key is
    a server the harness never starts, and nothing reports it."""
    def written(client, name, loader):
        cfg = tmp_path / f"{client}-{name}"
        assert setup_client.run_setup([client, "--config", str(cfg)]) == 0
        return loader(cfg.read_text(encoding="utf-8"))

    j = lambda s: json.loads(s)
    y = lambda s: yaml.safe_load(s)

    for client in ("cursor", "windsurf", "claude-desktop", "lmstudio", "gemini", "anythingllm"):
        entry = written(client, "mcp.json", j)["mcpServers"]["silica-core"]
        assert [entry["command"], *entry["args"]] == setup_client.MCP_COMMAND

    for client in ("cline", "roo"):
        entry = written(client, "mcp.json", j)["mcpServers"]["silica-core"]
        # Without pre-approval the user clicks a confirmation per search.
        assert entry["disabled"] is False
        assert entry["autoApprove"] == setup_client.AUTO_APPROVE

    zed = written("zed", "settings.json", j)["context_servers"]["silica-core"]
    assert zed["source"] == "custom"
    assert [zed["command"], *zed["args"]] == setup_client.MCP_COMMAND

    goose = written("goose", "config.yaml", y)["extensions"]["silica-core"]
    assert (goose["type"], goose["enabled"]) == ("stdio", True)
    assert [goose["cmd"], *goose["args"]] == setup_client.MCP_COMMAND

    lc = written("librechat", "librechat.yaml", y)["mcpServers"]["silica-core"]
    assert [lc["command"], *lc["args"]] == setup_client.MCP_COMMAND


def test_list_shaped_registries_append_instead_of_keying(tmp_path):
    """Continue and OpenHands hold servers in a list, so the entry carries its
    own name and a second run must not add a duplicate row."""
    cfg = tmp_path / "config.yaml"
    cfg.write_text("mcpServers:\n  - name: other\n    command: echo\n", encoding="utf-8")
    assert setup_client.run_setup(["continue", "--config", str(cfg)]) == 0
    servers = yaml.safe_load(cfg.read_text(encoding="utf-8"))["mcpServers"]
    assert [s["name"] for s in servers] == ["other", "silica-core"]

    toml_cfg = tmp_path / "config.toml"
    assert setup_client.run_setup(["openhands", "--config", str(toml_cfg)]) == 0
    assert setup_client.run_setup(["openhands", "--config", str(toml_cfg)]) == 0
    rows = tomllib.loads(toml_cfg.read_text(encoding="utf-8"))["mcp"]["stdio_servers"]
    assert [r["name"] for r in rows] == ["silica-core"]
    assert [rows[0]["command"], *rows[0]["args"]] == setup_client.MCP_COMMAND


def test_a_registry_of_the_wrong_shape_is_refused(tmp_path):
    cfg = tmp_path / "mcp.json"
    cfg.write_text('{"mcpServers": []}', encoding="utf-8")
    assert setup_client.run_setup(["cursor", "--config", str(cfg)]) == 1
    assert cfg.read_text(encoding="utf-8") == '{"mcpServers": []}'


def test_dry_run_writes_nothing_for_any_client(tmp_path):
    for client in sorted(setup_client.CLIENTS):
        cfg = tmp_path / f"{client}.cfg"
        assert setup_client.run_setup([client, "--config", str(cfg), "--dry-run"]) == 0
        assert not cfg.exists()


def test_recipes_answer_the_harnesses_with_no_config_file(capsys):
    """Aider, SWE-agent, LangGraph and friends install by instruction, so
    `silica setup <topic>` prints one rather than failing."""
    assert "silica search" in _printed(["shell"], capsys)
    assert "silica-core[mcp]" in _printed(["python"], capsys)
    assert "silica-core[mcp]" in _printed(["generic"], capsys)


def test_list_names_every_harness_the_docs_claim(capsys):
    out = _printed(["--list"], capsys)
    for client in (*setup_client.CLIENTS, "claude", "dsh", *setup_client.RECIPES):
        assert client in out


@pytest.mark.parametrize("page", ["README.md", "docs/agent-harnesses.md"])
def test_the_harness_census_and_the_command_agree(page):
    """The pages that list the harnesses are the census the table implements.

    Both directions: a client absent from the page is one nobody finds, and a
    `silica setup X` on the page that X does not answer is a broken instruction
    printed to a reader who then has nowhere to go.

    README.md ships; /docs is gitignored, so that half of the check guards the
    drift for whoever holds the file and stands down in a clone that does not.
    """
    import re

    census = Path(__file__).resolve().parent.parent / page
    if not census.exists():
        pytest.skip(f"{page} is not in this checkout")
    doc = census.read_text(encoding="utf-8")
    known = {"claude", "dsh", *setup_client.CLIENTS, *setup_client.RECIPES}
    # The README names clients in table cells, the census in prose, so the
    # forward check takes either; only the unambiguous phrasing can be read
    # backwards, since a page is full of backticks that are not client names.
    spelled = set(re.findall(r"silica setup ([a-z][a-z-]*)", doc))
    named = spelled | set(re.findall(r"`([a-z][a-z-]*)`", doc))
    assert known <= named, f"{page} does not name: {sorted(known - named)}"
    assert spelled <= known, f"{page} promises a client that does not exist: {sorted(spelled - known)}"
