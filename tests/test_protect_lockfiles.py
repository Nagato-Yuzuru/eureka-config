"""Executable spec for dot_claude/hooks/executable_protect-lockfiles.sh, the PreToolUse hook
that keeps Write and Edit off lockfiles: each is owned by the tool that generates it.

- Write/Edit on a lockfile, matched by exact basename at any depth, is denied and the reason
  names the command that regenerates it
- other files pass, including names that only contain a lockfile's name
- other tools pass: the package managers themselves write lockfiles through Bash, which a
  permissions.deny rule would block too, since the sandbox merges Edit denies into its own
- without jq, or on unparseable input, the hook denies
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "dot_claude" / "hooks" / "executable_protect-lockfiles.sh"
BASH = "/bin/bash"  # the oldest bash the hook must run under (3.2 on macOS)


def run(payload: object, path: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([BASH, str(HOOK)], input=stdin, capture_output=True, text=True, env=env)


def edit(file_path: str, tool: str = "Edit") -> dict[str, object]:
    return {"tool_name": tool, "tool_input": {"file_path": file_path}}


def deny_reason(proc: subprocess.CompletedProcess) -> str | None:
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return None
    out = json.loads(proc.stdout)["hookSpecificOutput"]
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


LOCKFILES = {
    "uv.lock": "uv lock",
    "pnpm-lock.yaml": "pnpm install",
    "deno.lock": "deno install",
    "Cargo.lock": "cargo update",
    "go.sum": "go mod tidy",
    "mise.lock": "mise lock",
}


@pytest.mark.parametrize("tool", ["Write", "Edit"])
@pytest.mark.parametrize(("name", "command"), LOCKFILES.items())
def test_lockfiles_are_denied_with_their_regenerating_command(name: str, command: str, tool: str):
    for file_path in (f"/work/proj/{name}", f"/work/proj/crates/sub/{name}", name):
        reason = deny_reason(run(edit(file_path, tool)))
        assert reason is not None, file_path
        assert command in reason


@pytest.mark.parametrize(
    "file_path",
    [
        "/work/proj/pyproject.toml",
        "/work/proj/uv.lock.bak",
        "/work/proj/my-uv.lock",
        "/work/proj/go.mod",
        "/work/proj/uv.lock/notes.md",
        "",
    ],
)
def test_other_files_pass(file_path: str):
    assert deny_reason(run(edit(file_path))) is None


@pytest.mark.parametrize("tool", ["Bash", "Read", "NotebookEdit"])
def test_other_tools_pass(tool: str):
    assert deny_reason(run(edit("/work/proj/uv.lock", tool))) is None


@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null"])
def test_unparseable_input_denies(stdin: str):
    assert deny_reason(run(stdin)) is not None


def test_missing_jq_denies(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    proc = run(edit("/work/proj/README.md"), path=str(empty))
    assert proc.returncode == 2
    assert "jq is unavailable" in proc.stderr
