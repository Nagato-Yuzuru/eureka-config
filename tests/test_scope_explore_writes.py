"""Executable spec for dot_claude/hooks/executable_scope-explore-writes.sh, the PreToolUse
hook the explore agent registers in its own frontmatter, so it runs only inside explore.

- Write/Edit pass only for a path under one of explore's two agent-memory directories:
  $HOME/.claude/agent-memory/explore/ and $CLAUDE_PROJECT_DIR/.claude/agent-memory/explore/
- every other path is denied, including another project's memory directory, a path that
  climbs out with `..`, and a relative path; an unset CLAUDE_PROJECT_DIR leaves only the
  home directory writable
- other tools pass through; without jq, or on unparseable input, the hook denies
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "dot_claude" / "hooks" / "executable_scope-explore-writes.sh"
BASH = "/bin/bash"  # the oldest bash the hook must run under (3.2 on macOS)

HOME = "/Users/u"
PROJECT = "/work/proj"


def run(
    payload: object,
    project: str | None = PROJECT,
    path: str | None = None,
) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k != "CLAUDE_PROJECT_DIR"}
    env["HOME"] = HOME
    if project is not None:
        env["CLAUDE_PROJECT_DIR"] = project
    if path is not None:
        env["PATH"] = path
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return subprocess.run([BASH, str(HOOK)], input=stdin, capture_output=True, text=True, env=env)


def write(file_path: str, tool: str = "Write") -> dict[str, object]:
    return {"tool_name": tool, "tool_input": {"file_path": file_path}}


def denied(proc: subprocess.CompletedProcess) -> bool:
    if proc.returncode == 2:
        return True
    assert proc.returncode == 0, proc.stderr
    if not proc.stdout.strip():
        return False
    return json.loads(proc.stdout)["hookSpecificOutput"]["permissionDecision"] == "deny"


# Write and Edit share one `case` arm, so the cases alternate between them
ALLOWED = [
    (f"{HOME}/.claude/agent-memory/explore/MEMORY.md", "Write"),
    (f"{HOME}/.claude/agent-memory/explore/sub/entry.md", "Edit"),
    (f"{PROJECT}/.claude/agent-memory/explore/MEMORY.md", "Write"),
]


@pytest.mark.parametrize(("file_path", "tool"), ALLOWED)
def test_memory_directories_are_writable(file_path: str, tool: str):
    assert not denied(run(write(file_path, tool)))


DENIED = [
    (f"{PROJECT}/src/main.py", "Write"),
    (f"{PROJECT}/.claude/agent-memory/explore/", "Edit"),
    ("/other/proj/.claude/agent-memory/explore/MEMORY.md", "Write"),
    (f"{HOME}/.claude/agent-memory/explore/../../settings.json", "Edit"),
    (f"{HOME}/.claude/agent-memory/explore-evil/x.md", "Write"),
    (".claude/agent-memory/explore/MEMORY.md", "Edit"),
    ("", "Write"),
]


@pytest.mark.parametrize(("file_path", "tool"), DENIED)
def test_other_paths_are_denied(file_path: str, tool: str):
    proc = run(write(file_path, tool))
    assert denied(proc)
    assert "agent-memory/explore/" in proc.stdout


def test_unset_project_dir_leaves_only_home():
    assert denied(run(write(f"{PROJECT}/.claude/agent-memory/explore/MEMORY.md"), project=None))
    assert not denied(run(write(f"{HOME}/.claude/agent-memory/explore/MEMORY.md"), project=None))


@pytest.mark.parametrize("project", ["", "/", "relative/dir"])
def test_non_absolute_project_dir_opens_nothing(project: str):
    for file_path in (
        "/.claude/agent-memory/explore/MEMORY.md",
        "//.claude/agent-memory/explore/x",
    ):
        assert denied(run(write(file_path), project=project))
    assert denied(run(write("relative/dir/.claude/agent-memory/explore/x"), project=project))


def test_other_tools_pass():
    assert not denied(run({"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}}))


@pytest.mark.parametrize("stdin", ["", "not json", "[]"])
def test_unparseable_input_denies(stdin: str):
    assert denied(run(stdin))


def test_missing_jq_denies(tmp_path: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    for tool in ("cat",):
        (bindir / tool).symlink_to(shutil.which(tool))
    proc = run(write(ALLOWED[0]), path=str(bindir))
    assert proc.returncode == 2
    assert "jq" in proc.stderr
