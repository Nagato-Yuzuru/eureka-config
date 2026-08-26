"""Golden decision table for the night-gate hook (v5, tool-name gate only).

Drives dot_claude/hooks/executable_night-gate.sh exactly as Claude Code
does: event JSON on stdin, decision JSON (or silence = allow) on stdout.
The rule semantics live HERE as a table; the script is the implementation.
NIGHT_DIR is the testability seam for the flag file.

Bash strings are NEVER parsed (a hook sees the actor's own description of
an action, not the action). Command enforcement lives at the issuance
layer (GitHub App / fnox lease), the shim argv checks
(test_night_shim.py), and the hygiene env (liveness tests below). This
hook gates ONLY tool names, the one ambient authority no lease can
starve: browser sessions and claude.ai connectors.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "dot_claude" / "hooks" / "executable_night-gate.sh"
ENV_SH = REPO_ROOT / "dot_claude" / "night" / "env.sh"
SSH_CONF = REPO_ROOT / "dot_claude" / "night" / "ssh-night.conf"


def run_gate(
    tmp_path: Path,
    tool: str,
    tool_input: dict,
    *,
    flag: bool = True,
    mode: str | None = None,
) -> str:
    night = tmp_path / "night"
    night.mkdir(exist_ok=True)
    active = night / "active.json"
    if flag:
        active.write_text('{"since": 1, "mode": "full"}')
    elif active.exists():
        active.unlink()
    args = [] if mode is None else [mode]
    proc = subprocess.run(
        ["bash", str(GATE), *args],
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


# --- Bash: zero parsing, pinned --------------------------------------------

BASH_IS_NEVER_PARSED = [
    # command shapes a string parser would be tempted to deny, pinned as
    # allow: their enforcement lives at issuance / shim / hygiene, and a
    # parser verdict here would be theater or a false deny
    "gh pr list",
    "ssh host uptime",
    "fnox get OP_SERVICE_ACCOUNT_TOKEN",
    "npm publish",
    "terraform apply -auto-approve",
    "security dump-keychain -d",
    "git push --force origin main",
    "claude -p 'do things'",
    "echo hello",
]


@pytest.mark.parametrize("command", BASH_IS_NEVER_PARSED)
def test_bash_never_parsed(tmp_path: Path, command: str) -> None:
    assert run_gate(tmp_path, "Bash", {"command": command}) == "allow"


# --- MCP tool names ---------------------------------------------------------

DENY_MCP = [
    # Linear is allow-listed (reads + task-state), not allow-all
    "mcp__linear-server__merge_diff",
    "mcp__linear-server__submit_diff_review",
    "mcp__linear-server__share_issue",
    "mcp__linear-server__save_document",
    # claude.ai connectors: whole prefix, reads included (a prompt-injection
    # channel)
    "mcp__claude_ai_Gmail__get_message",
    "mcp__claude_ai_Gmail__send_message",
    "mcp__claude_ai_Google_Calendar__list_events",
    "mcp__claude_ai_Google_Drive__share_file",
    "mcp__claude_ai_Interactive_Brokers_IBKR__get_account_positions",
    "mcp__claude_ai_Todoist__find-tasks",
    # Chrome: read ALLOWLIST; writes and unknowns deny by default
    "mcp__claude-in-chrome__computer",
    "mcp__claude-in-chrome__javascript_tool",
    "mcp__claude-in-chrome__form_input",
    "mcp__claude-in-chrome__file_upload",
    "mcp__claude-in-chrome__upload_image",
    "mcp__claude-in-chrome__browser_batch",
    "mcp__claude-in-chrome__shortcuts_execute",
    "mcp__claude-in-chrome__some_future_tool",  # fail-CLOSED for new tools
    # playwright: read allowlist, same posture
    "mcp__plugin_playwright_playwright__browser_click",
    "mcp__plugin_playwright_playwright__browser_type",
    "mcp__plugin_playwright_playwright__browser_fill_form",
    "mcp__plugin_playwright_playwright__browser_file_upload",
    "mcp__plugin_playwright_playwright__browser_run_code_unsafe",
    "mcp__plugin_playwright_playwright__browser_evaluate",
    "mcp__plugin_playwright_playwright__browser_press_key",
    "mcp__plugin_playwright_playwright__browser_drag",
    "mcp__plugin_playwright_playwright__browser_drop",
    "mcp__plugin_playwright_playwright__browser_select_option",
    "mcp__plugin_playwright_playwright__browser_handle_dialog",
    "mcp__plugin_playwright_playwright__browser_network_request",  # singular: performs a request
    "mcp__plugin_playwright_playwright__browser_hover",  # interaction, off-list
    "mcp__plugin_playwright_playwright__browser_new_shiny_tool",  # fail-closed
]

ALLOW_MCP = [
    # Linear is the night task tracker (separate MCP, not a claude.ai connector)
    "mcp__linear-server__save_issue",
    "mcp__linear-server__save_comment",
    "mcp__linear-server__list_issues",
    "mcp__linear-server__get_issue",
    # read-only browsing stays open
    "mcp__claude-in-chrome__navigate",
    "mcp__claude-in-chrome__read_page",
    "mcp__claude-in-chrome__get_page_text",
    "mcp__claude-in-chrome__find",
    "mcp__claude-in-chrome__screenshot",
    "mcp__claude-in-chrome__tabs_context_mcp",
    "mcp__claude-in-chrome__read_console_messages",
    "mcp__plugin_playwright_playwright__browser_snapshot",
    "mcp__plugin_playwright_playwright__browser_navigate",
    "mcp__plugin_playwright_playwright__browser_navigate_back",
    "mcp__plugin_playwright_playwright__browser_take_screenshot",
    "mcp__plugin_playwright_playwright__browser_console_messages",
    "mcp__plugin_playwright_playwright__browser_network_requests",
    "mcp__plugin_playwright_playwright__browser_find",
    "mcp__plugin_playwright_playwright__browser_wait_for",
    "mcp__plugin_playwright_playwright__browser_tabs",
    "mcp__plugin_playwright_playwright__browser_close",
    # non-browser, non-connector MCP servers are not this gate's business
    "mcp__some-other-server__whatever_tool",
]


@pytest.mark.parametrize("tool", DENY_MCP)
def test_mcp_denied(tmp_path: Path, tool: str) -> None:
    assert run_gate(tmp_path, tool, {}) == "deny"


@pytest.mark.parametrize("tool", ALLOW_MCP)
def test_mcp_allowed(tmp_path: Path, tool: str) -> None:
    assert run_gate(tmp_path, tool, {}) == "allow"


# --- user-prompt: live sessions learn the flag fell -------------------------


def run_user_prompt(tmp_path: Path, sid: str, *, flag: bool = True) -> dict | None:
    night = tmp_path / "night"
    night.mkdir(exist_ok=True)
    active = night / "active.json"
    if flag:
        active.write_text('{"since": 1, "mode": "full"}')
    elif active.exists():
        active.unlink()
    proc = subprocess.run(
        ["bash", str(GATE), "user-prompt"],
        input=json.dumps({"session_id": sid, "prompt": "carry on"}),
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "NIGHT_DIR": str(night)},
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return None
    return json.loads(proc.stdout)


def test_user_prompt_injects_once_per_session(tmp_path: Path) -> None:
    first = run_user_prompt(tmp_path, "abc-123")
    assert first is not None
    ctx = first["hookSpecificOutput"]["additionalContext"]
    assert "night-shift skill" in ctx
    assert first["hookSpecificOutput"]["hookEventName"] == "UserPromptSubmit"
    # same session: silent from now on
    assert run_user_prompt(tmp_path, "abc-123") is None
    # a different session still gets its one injection
    assert run_user_prompt(tmp_path, "other-9") is not None


def test_user_prompt_silent_without_flag(tmp_path: Path) -> None:
    assert run_user_prompt(tmp_path, "abc-123", flag=False) is None


def test_off_resets_session_markers(tmp_path: Path) -> None:
    night = tmp_path / "night"
    assert run_user_prompt(tmp_path, "abc-123") is not None
    subprocess.run(
        ["bash", str(GATE), "off"],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "NIGHT_DIR": str(night)},
        timeout=15,
        check=True,
    )
    # next night, the same session id is notified again
    assert run_user_prompt(tmp_path, "abc-123") is not None


# --- regime & robustness ----------------------------------------------------


def test_inert_without_flag(tmp_path: Path) -> None:
    assert run_gate(tmp_path, "mcp__claude-in-chrome__computer", {}, flag=False) == "allow"
    assert run_gate(tmp_path, "mcp__claude_ai_Gmail__send_message", {}, flag=False) == "allow"


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


def test_non_mcp_tools_untouched(tmp_path: Path) -> None:
    # the matcher registers only mcp__.*, but even if invoked: allow
    assert run_gate(tmp_path, "Read", {"file_path": "/etc/hosts"}) == "allow"


def test_deny_without_grep_on_path(tmp_path: Path) -> None:
    # decisions are pure bash + jq: a PATH missing grep (or any other
    # external) must never fail open
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("bash", "jq", "cat"):
        src = shutil.which(tool)
        assert src, f"{tool} not on PATH"
        (bindir / tool).symlink_to(src)
    night = tmp_path / "night"
    night.mkdir()
    (night / "active.json").write_text('{"since": 1, "mode": "full"}')
    proc = subprocess.run(
        [str(bindir / "bash"), str(GATE)],
        input=json.dumps({"tool_name": "mcp__claude_ai_Gmail__get_message", "tool_input": {}}),
        capture_output=True,
        text=True,
        env={"PATH": str(bindir), "HOME": os.environ["HOME"], "NIGHT_DIR": str(night)},
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "connector" in out["hookSpecificOutput"]["permissionDecisionReason"]


# --- hygiene channel: liveness goldens --------------------------------------
# The feedback layer's promise is that credential-shaped bare commands die
# FAST with a readable error. Test the real property against the real
# binaries.

SENTINEL = "ops_NIGHT_SENTINEL_invalid_on_purpose_use_night_op"


def night_env(tmp_path: Path) -> dict[str, str]:
    """The night hygiene env exactly as env.sh produces it, plus a bare HOME."""
    proc = subprocess.run(
        ["zsh", "-c", f'source "{ENV_SH}"; env'],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    return dict(line.split("=", 1) for line in proc.stdout.splitlines() if "=" in line)


def test_env_sh_values(tmp_path: Path) -> None:
    env = night_env(tmp_path)
    assert env["SSH_AUTH_SOCK"] == ""
    assert env["GIT_TERMINAL_PROMPT"] == "0"
    assert env["FNOX_NON_INTERACTIVE"] == "1"
    assert env["GH_CONFIG_DIR"] == f"{tmp_path}/.claude/night/gh-null"
    assert env["OP_SERVICE_ACCOUNT_TOKEN"] == SENTINEL
    assert env["GIT_CONFIG_COUNT"] == "7"
    assert env["GIT_CONFIG_VALUE_0"] == "false"  # commit.gpgsign
    assert env["GIT_CONFIG_VALUE_5"] == "/usr/bin/false"  # core.sshCommand


@pytest.mark.skipif(shutil.which("gh") is None, reason="gh not installed")
def test_bare_gh_dies_fast_under_night_env(tmp_path: Path) -> None:
    (tmp_path / ".claude" / "night" / "gh-null").mkdir(parents=True)
    env = night_env(tmp_path)
    env["HOME"] = str(tmp_path)
    proc = subprocess.run(
        ["gh", "api", "user"],
        capture_output=True,
        text=True,
        env=env,
        timeout=5,  # fast is the property under test
    )
    assert proc.returncode != 0
    # the error must teach: gh names the auth problem, skill maps it to
    # "not granted tonight -> park"
    assert "auth" in (proc.stderr + proc.stdout).lower()


@pytest.mark.skipif(shutil.which("op") is None, reason="op not installed")
def test_op_dies_fast_on_sentinel(tmp_path: Path) -> None:
    proc = subprocess.run(
        ["op", "vault", "list"],
        capture_output=True,
        text=True,
        env={
            "PATH": os.environ["PATH"],
            "HOME": os.environ["HOME"],
            "OP_SERVICE_ACCOUNT_TOKEN": SENTINEL,
        },
        timeout=5,  # never the desktop-app hang path
    )
    assert proc.returncode != 0
    out = (proc.stderr + proc.stdout).lower()
    # op names the credential problem ("DecodeSACredentials" / token parse)
    assert "credential" in out or "token" in out


@pytest.mark.skipif(shutil.which("ssh") is None, reason="ssh not installed")
def test_ssh_night_conf_kills_agent_when_flag_on(tmp_path: Path) -> None:
    night = tmp_path / ".claude" / "night"
    night.mkdir(parents=True)
    conf = tmp_path / "config"
    conf.write_text(SSH_CONF.read_text() + "\nHost *\n  IdentityAgent ~/day-agent.sock\n")

    def effective(flag: bool) -> str:
        active = night / "active.json"
        if flag:
            active.write_text("{}")
        elif active.exists():
            active.unlink()
        proc = subprocess.run(
            ["ssh", "-F", str(conf), "-G", "example.com"],
            capture_output=True,
            text=True,
            env={"PATH": os.environ["PATH"], "HOME": str(tmp_path)},
            timeout=15,
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout

    on = effective(flag=True)
    assert "identityagent none" in on
    assert "batchmode yes" in on
    # day: the later Host block's agent wins again (first-value semantics
    # only bite while the Match matches). ssh -G prints ~ expanded via
    # pw_dir, so match on the suffix.
    off = effective(flag=False)
    assert "identityagent none" not in off
    assert "day-agent.sock" in off


# --- zshenv guard ------------------------------------------------------------


def _run_zshenv_guard(tmp_home: Path, line: str, *, claudecode: bool) -> str:
    env = {"PATH": os.environ["PATH"], "HOME": str(tmp_home)}
    if claudecode:
        env["CLAUDECODE"] = "1"
    proc = subprocess.run(
        ["zsh", "-c", line + '\nprint -r -- "$NIGHT_MARKER"'],
        capture_output=True,
        text=True,
        env=env,
        timeout=15,
    )
    return proc.stdout.strip()


def test_zshenv_guard_line(tmp_path: Path) -> None:
    # the preflight only greps that this line EXISTS; its logic is verified
    # here, evaluated verbatim from the zshenv source against a fake HOME
    src = (REPO_ROOT / "dot_zshenv.tmpl").read_text()
    line = next(
        ln for ln in src.splitlines() if "night/env.sh" in ln and not ln.lstrip().startswith("#")
    )
    home = tmp_path / "home"
    night = home / ".claude" / "night"
    night.mkdir(parents=True)
    (night / "env.sh").write_text("export NIGHT_MARKER=lit\n")
    # flag absent: hygiene stays off
    assert _run_zshenv_guard(home, line, claudecode=True) == ""
    # flag + Claude shell: env.sh sourced
    (night / "active.json").write_text("{}")
    assert _run_zshenv_guard(home, line, claudecode=True) == "lit"
    # owner's own (non-Claude) shells keep the day environment
    assert _run_zshenv_guard(home, line, claudecode=False) == ""


# --- zshrc wiring: the lines other channels depend on ------------------------


def test_zshrc_night_wiring_present() -> None:
    src = (REPO_ROOT / "dot_zshrc.tmpl").read_text()
    # op plugins skipped while the flag exists (night-born shells)
    plugin_line = next(ln for ln in src.splitlines() if "op/plugins.sh" in ln)
    assert "night/active.json" in plugin_line
    # claude launches from a deactivate-cleansed subshell
    assert "claude() {" in src
    assert "command fnox deactivate" in src
