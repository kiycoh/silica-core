"""One artifact tree, three harnesses: the plugin manifests must agree on it."""
from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from pathlib import Path

import pytest

from silica.onboarding.setup_client import MCP_COMMAND

ROOT = Path(__file__).resolve().parent.parent


def _manifest(folder: str) -> dict:
    return json.loads((ROOT / folder / "plugin.json").read_text(encoding="utf-8"))


def test_both_mcp_files_launch_a_bare_silica_mcp():
    # Two files because the dialects disagree on the wrapper key only: Claude
    # Code reads `mcpServers`, Codex reads `mcp_servers`. What they must share
    # is the launch SHAPE — `silica mcp` over stdio with no vault pinned.
    # WHICH silica each one launches diverged on purpose 2026-08-29 (see
    # test_mcp_surface.test_plugin_serves_its_own_tree_not_the_published_wheel):
    # Claude Code expands ${CLAUDE_PLUGIN_ROOT} and so can run the checkout it
    # shipped, Codex has no such variable and keeps the published wheel.
    for name, key in (("mcp.json", "mcpServers"), ("mcp.codex.json", "mcp_servers")):
        doc = json.loads((ROOT / name).read_text(encoding="utf-8"))
        assert set(doc) == {key}, name
        srv = doc[key]["silica-core"]
        args = srv["args"]
        assert args[args.index("silica"):args.index("silica") + 2] == ["silica", "mcp"], name
        assert "env" not in srv  # vault = the folder the client opened, never a pin

    # Codex is the install path setup_client also writes by hand, so those two
    # still have to be the same command, prefix-wise: a surface flag may follow.
    codex = json.loads((ROOT / "mcp.codex.json").read_text(encoding="utf-8"))
    srv = codex["mcp_servers"]["silica-core"]
    assert [srv["command"], *srv["args"]][:len(MCP_COMMAND)] == MCP_COMMAND




def test_both_marketplaces_offer_the_same_plugin_from_the_repo_root():
    claude = json.loads((ROOT / ".claude-plugin" / "marketplace.json").read_text(encoding="utf-8"))
    codex = json.loads((ROOT / ".agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8"))
    assert claude["name"] == codex["name"]
    (c_entry,), (x_entry,) = claude["plugins"], codex["plugins"]
    assert c_entry["name"] == x_entry["name"] == _manifest(".codex-plugin")["name"]
    # Both sources are the repo root, where both plugin manifests sit.
    assert c_entry["source"] == "./"
    assert x_entry["source"] in ("./", {"source": "local", "path": "./"})


def test_the_registry_entry_is_launchable_and_owns_its_package():
    # server.json is a fourth manifest of the same launch, and it fails in ways
    # the others cannot. Ownership: the MCP registry proves the PyPI package is
    # ours by finding an `mcp-name` marker in the description PyPI renders,
    # which is README.md, so a README rewrite can revoke the right to publish.
    # Launch: a client builds `uvx <runtimeArguments> <identifier> <args>`, and
    # `identifier` is the PyPI name, so the command it spells is `silica-core`,
    # not `silica` — the alias in [project.scripts] is what keeps it resolvable.
    server = json.loads((ROOT / "server.json").read_text(encoding="utf-8"))
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    (pkg,) = server["packages"]

    assert f"<!-- mcp-name: {server['name']} -->" in (ROOT / "README.md").read_text(encoding="utf-8")
    assert pkg["identifier"] == project["name"]
    assert pkg["identifier"] in project["scripts"]
    # A package version the registry cannot find on PyPI is rejected, and the
    # release job holds both of these to the tag.
    assert server["version"] == pkg["version"]

    # Same shape as the block `silica setup <client>` writes, executable aside.
    named = {a["name"]: a["value"] for a in pkg["runtimeArguments"]}
    assert [pkg["runtimeHint"], "--from", named["--from"]] == MCP_COMMAND[:3]
    assert [a["value"] for a in pkg["packageArguments"]] == MCP_COMMAND[4:]






@pytest.mark.skipif(shutil.which("claude") is None, reason="claude CLI not on PATH")
def test_claude_plugin_validate_passes():
    r = subprocess.run(["claude", "plugin", "validate", str(ROOT)],
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stdout + r.stderr


PLUGIN_FILES = (
    ".claude-plugin/plugin.json", ".claude-plugin/marketplace.json",
    ".codex-plugin/plugin.json", ".agents/plugins/marketplace.json",
    "mcp.json", "mcp.codex.json",
)


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(),
                    reason="not a git checkout")
def test_plugin_files_are_not_gitignored():
    # A plugin file that passes the tests here and is missing from every
    # clone is the one failure the tests above cannot see: `.agents/` was
    # ignored when the Codex marketplace was added.
    ignored = subprocess.run(["git", "check-ignore", *PLUGIN_FILES], cwd=ROOT,
                             capture_output=True, text=True).stdout.split()
    assert not ignored, f"gitignored plugin files: {ignored}"


def test_the_shipped_tree_still_has_a_version_without_git():
    # The marketplace copies this repo into ~/.claude/plugins/cache WITHOUT
    # .git, and mcp.json launches `uv run --project <that copy>`, which builds
    # it there. setuptools-scm reads the version from git tags and RAISES when
    # it finds no repository ("unable to detect version for ..."), so the build
    # dies, the server never answers `initialize`, and the only thing the user
    # sees is "Failed to reconnect to plugin:silica:silica"; every cached
    # version 0.2.0/0.3.0/0.3.1 failed this way, measured 2026-08-29.
    scm = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    fallback = scm["tool"]["setuptools_scm"].get("fallback_version")
    assert fallback, "setuptools-scm must not raise where the plugin cache has no .git"
