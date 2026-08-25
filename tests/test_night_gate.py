"""Golden decision table for the night-gate hook (v4, authority-only).

Drives dot_claude/hooks/executable_night-gate.sh in hook mode exactly as
Claude Code does: tool-call JSON on stdin, decision JSON (or silence = allow)
on stdout. The rule semantics live HERE as a table; the script is the
implementation. NIGHT_DIR is the testability seam for the flag file.
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
    # T3 through the shim: releases ride on Contents:write — the one hole the
    # capability layer cannot close, so the gate line is load-bearing here
    "~/.claude/night/bin/night gh release create v1.0.0",
    "~/.claude/night/bin/night gh api -X POST repos/o/r/releases -f tag_name=v1",
    # a shim-shaped word ANYWHERE in the string must not bless the other
    # segments — the decision is per-segment, not per-string
    "echo night && gh release create v1",
    'git commit -m "the night shift work" && gh pr create',
    "ls ~/.claude/night && hcloud server delete 42",
    # paths and alias-escapes still resolve to the program (cheap, and the
    # rule they dodge is a liveness reroute worth keeping)
    "/usr/bin/ssh host uptime",
    "\\gh api user",
    # git push: only the unmistakable history-rewrite flags. Destination
    # discipline (claude/* only) is the server-side ruleset's job (YUZ-74).
    "~/.claude/night/bin/night git push --force origin HEAD:refs/heads/main",
    "~/.claude/night/bin/night git push --delete origin refs/tags/v1.0.0",
    "~/.claude/night/bin/night git push --mirror origin",
    # security is whole-program off the night surface (dump/unlock/export
    # read or open the keychain just as well as add/delete/find)
    "security dump-keychain -d",
    "security unlock-keychain",
    "security export -t identities",
    # T3 verb coverage: same irreversibility, sibling verbs
    "wrangler versions upload",
    "kubectl replace -f deploy.yaml",
    "kubectl patch deploy x -p '{}'",
    "kubectl scale deploy x --replicas=0",
    "flyctl apps destroy myapp",
    "docker buildx build --push -t repo/img .",
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
    # gated words as DATA (arguments, quoted strings) must not trip program
    # rules — in a dotfiles repo these are everyday T0 commands
    'git commit -m "docs: update claude settings"',
    "rg claude dot_claude/",
    'git commit -m "fix: ssh config"',
    "rsync -a build/ dist/",  # local-only rsync never touches ssh
    # heredoc bodies are data, not commands
    "cat > notes.md <<'EOF'\ngh api user\nssh host uptime\nEOF",
    # gh api with an explicit GET: -f is a query parameter, not a mutation
    "~/.claude/night/bin/night gh api -X GET search/issues -f q=x",
    # bare push is legitimate T0 on a claude/* branch; the gate cannot see
    # the current branch, and destination discipline is the server-side
    # ruleset's job (YUZ-74) — denying here would be a B1-class false deny
    "git push",
    "git push origin main",
    # KNOWN LEAKS, accepted: the threat model is drift, not an adversary.
    # Wrapped command strings (sh -c, eval, substitutions, variables) are
    # deliberately not chased — a parser can always be smuggled past, so the
    # real boundary is capability starvation + server-side rulesets. These
    # pin the honest behavior: the gate must not pretend otherwise.
    'bash -c "gh pr create"',
    'eval "gh api user"',
    "V=`gh release list`",
    'echo "before $(gh release create v2) after"',
    "n=~/.claude/night/bin/night && $n gh release create",
]


@pytest.mark.parametrize("command", DENY_BASH)
def test_bash_denied(tmp_path: Path, command: str) -> None:
    assert bash_decision(tmp_path, command) == "deny"


@pytest.mark.parametrize("command", ALLOW_BASH)
def test_bash_allowed(tmp_path: Path, command: str) -> None:
    assert bash_decision(tmp_path, command) == "allow"


# --- MCP rules -------------------------------------------------------------

DENY_MCP = [
    # Linear is allow-listed (reads + task-state), not allow-all: merging PRs,
    # submitting reviews, and publishing issues are T2/T3 outbound acts
    "mcp__linear-server__merge_diff",
    "mcp__linear-server__submit_diff_review",
    "mcp__linear-server__share_issue",
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
        input=json.dumps({"tool_name": "Bash", "tool_input": {"command": "fnox get X"}}),
        capture_output=True,
        text=True,
        env={"PATH": str(bindir), "HOME": os.environ["HOME"], "NIGHT_DIR": str(night)},
        timeout=15,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert "fnox" in out["hookSpecificOutput"]["permissionDecisionReason"]


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
