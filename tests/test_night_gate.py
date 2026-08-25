"""Golden decision table for the night-gate hook (v4, authority-only).

Drives dot_claude/hooks/executable_night-gate.sh in hook mode exactly as
Claude Code does: tool-call JSON on stdin, decision JSON (or silence = allow)
on stdout. The rule semantics live HERE as a table; the script is the
implementation. NIGHT_DIR is the testability seam for the flag file.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "dot_claude" / "hooks" / "executable_night-gate.sh"


def run_gate(tmp_path: Path, tool: str, tool_input: dict, *, flag: bool = True) -> str:
    night = tmp_path / "night"
    night.mkdir(exist_ok=True)
    active = night / "active.json"
    if flag:
        active.write_text('{"since": 1, "mode": "full"}')
    elif active.exists():
        active.unlink()
    proc = subprocess.run(
        ["bash", str(GATE)],
        input=json.dumps({"tool_name": tool, "tool_input": tool_input}),
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "NIGHT_DIR": str(night)},
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return "allow"
    out = json.loads(proc.stdout)
    return out["hookSpecificOutput"]["permissionDecision"]


def bash_decision(tmp_path: Path, command: str, *, flag: bool = True) -> str:
    return run_gate(tmp_path, "Bash", {"command": command}, flag=flag)


# --- Bash rules ------------------------------------------------------------

DENY_BASH = [
    # authorization-surface freeze
    "fnox get OP_SERVICE_ACCOUNT_TOKEN",
    "fnox set FOO bar --provider keychain",
    "FNOX_PROFILE=night fnox exec -- gh api user",
    "security find-generic-password -s fnox -w",
    "security add-generic-password -a x -s y -w z",
    "~/.claude/hooks/path-gate.sh grant /tmp rw",
    # ssh-shaped: 1P agent pinned via ssh_config IdentityAgent — locked at night
    "ssh host uptime",
    "scp file host:/tmp/",
    "rsync -av dir/ host:dir/",
    # op plugin shell functions (gh, hcloud) hit the locked desktop app
    "gh pr list",
    "gh api user -q .login",
    "hcloud server list",
    # interactive op (no SA in env) hits the locked desktop app
    "op read op://night/item/field",
    # T3: irreversible / high-risk
    "npm publish",
    "pnpm publish --access public",
    "cargo publish",
    "docker push repo/img:tag",
    "terraform apply -auto-approve",
    "kubectl delete pod x",
    "wrangler deploy",
    "gh release create v1.0.0",
    "gh api -X POST repos/o/r/releases -f tag_name=v1",
    "gh api repos/o/r/contents/f -X PUT --field message=x",
    # nested CLI sessions
    "claude -p 'do things'",
    "claude-night --continue",
    # T3 through the shim: releases ride on Contents:write — the one hole the
    # capability layer cannot close, so the gate line is load-bearing here
    "~/.claude/night/bin/night gh release create v1.0.0",
    "~/.claude/night/bin/night gh api -X POST repos/o/r/releases -f tag_name=v1",
]

ALLOW_BASH = [
    # T0 stays free — hygiene env (not the gate) handles signing/creds
    "git commit -m 'wip'",
    "git push origin claude/probe",  # fails fast on missing creds, not gated
    "git fetch origin",
    "echo hello",
    "cargo build",
    "uv run pytest -q",
    # the night shim is THE sanctioned path (no bare-word match on 'fnox'/'gh')
    "~/.claude/night/bin/night gh api user -q .login",
    "~/.claude/night/bin/night git push origin claude/x",
    "~/.claude/night/bin/night op read op://night/item/field",
    # word-boundary sanity: substrings must not trip the rules
    "echo fnoxlike-string",
    "echo laughs",  # contains 'gh' mid-word
    "grep -r sshd_config docs/",  # 'ssh' mid-word (sshd_config), flag position
]


@pytest.mark.parametrize("command", DENY_BASH)
def test_bash_denied(tmp_path: Path, command: str) -> None:
    assert bash_decision(tmp_path, command) == "deny"


@pytest.mark.parametrize("command", ALLOW_BASH)
def test_bash_allowed(tmp_path: Path, command: str) -> None:
    assert bash_decision(tmp_path, command) == "allow"


# --- MCP rules -------------------------------------------------------------

DENY_MCP = [
    # claude.ai connectors: whole prefix, reads included (injection surface)
    "mcp__claude_ai_Gmail__get_message",
    "mcp__claude_ai_Gmail__send_message",
    "mcp__claude_ai_Google_Calendar__list_events",
    "mcp__claude_ai_Google_Drive__share_file",
    "mcp__claude_ai_Interactive_Brokers_IBKR__get_account_positions",
    "mcp__claude_ai_Todoist__find-tasks",
    # Chrome: write-class tools (browsing stays read-only)
    "mcp__claude-in-chrome__computer",
    "mcp__claude-in-chrome__javascript_tool",
    "mcp__claude-in-chrome__form_input",
    "mcp__claude-in-chrome__file_upload",
    "mcp__claude-in-chrome__upload_image",
    "mcp__claude-in-chrome__browser_batch",
    "mcp__claude-in-chrome__shortcuts_execute",
    # playwright: write-class tools
    "mcp__plugin_playwright_playwright__browser_click",
    "mcp__plugin_playwright_playwright__browser_type",
    "mcp__plugin_playwright_playwright__browser_fill_form",
    "mcp__plugin_playwright_playwright__browser_file_upload",
    "mcp__plugin_playwright_playwright__browser_run_code_unsafe",
    "mcp__plugin_playwright_playwright__browser_evaluate",
    "mcp__plugin_playwright_playwright__browser_press_key",
    "mcp__plugin_playwright_playwright__browser_drag",
    "mcp__plugin_playwright_playwright__browser_select_option",
    "mcp__plugin_playwright_playwright__browser_handle_dialog",
    "mcp__plugin_playwright_playwright__browser_network_request",
]

ALLOW_MCP = [
    # Linear is the night task tracker (separate MCP, not a claude.ai connector)
    "mcp__linear-server__save_issue",
    "mcp__linear-server__list_issues",
    # read-only browsing stays open
    "mcp__claude-in-chrome__navigate",
    "mcp__claude-in-chrome__read_page",
    "mcp__claude-in-chrome__get_page_text",
    "mcp__claude-in-chrome__tabs_context_mcp",
    "mcp__claude-in-chrome__read_console_messages",
    "mcp__plugin_playwright_playwright__browser_snapshot",
    "mcp__plugin_playwright_playwright__browser_navigate",
    "mcp__plugin_playwright_playwright__browser_take_screenshot",
]


@pytest.mark.parametrize("tool", DENY_MCP)
def test_mcp_denied(tmp_path: Path, tool: str) -> None:
    assert run_gate(tmp_path, tool, {}) == "deny"


@pytest.mark.parametrize("tool", ALLOW_MCP)
def test_mcp_allowed(tmp_path: Path, tool: str) -> None:
    assert run_gate(tmp_path, tool, {}) == "allow"


# --- regime & robustness ---------------------------------------------------

def test_inert_without_flag(tmp_path: Path) -> None:
    # every rule stands down when the flag is absent
    assert bash_decision(tmp_path, "ssh host uptime", flag=False) == "allow"
    assert run_gate(tmp_path, "mcp__claude-in-chrome__computer", {}, flag=False) == "allow"
    assert bash_decision(tmp_path, "fnox get X", flag=False) == "allow"


def test_malformed_input_fails_closed(tmp_path: Path) -> None:
    night = tmp_path / "night"
    night.mkdir(exist_ok=True)
    (night / "active.json").write_text("{}")
    proc = subprocess.run(
        ["bash", str(GATE)],
        input="this is not json",
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "NIGHT_DIR": str(night)},
        timeout=15,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"


def test_non_bash_non_mcp_tools_untouched(tmp_path: Path) -> None:
    # matcher registers only Bash|mcp__.* — but even if invoked, allow
    assert run_gate(tmp_path, "Read", {"file_path": "/etc/hosts"}) == "allow"
