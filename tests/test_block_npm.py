"""Executable spec for dot_claude/hooks/executable_block-npm.sh, the PreToolUse hook that
refuses npm/npx in Bash commands (pnpm and deno replace them).

- npm/npx in command position is blocked (exit 2): at the start, after a separator or a
  subshell/substitution opener, behind the common wrappers (sudo, command, env, exec, time,
  nohup) and variable assignments, or spelled as a path
- the same words anywhere else pass: inside quotes, in comments, as parts of other words
- matching needs nothing beyond bash and jq, so it cannot silently pass where GNU grep is
  missing; without jq the hook blocks rather than guess
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
HOOK = REPO_ROOT / "dot_claude" / "hooks" / "executable_block-npm.sh"
BASH = "/bin/bash"  # the oldest bash the hook must run under (3.2 on macOS)


def run(command: str, tool: str = "Bash", path: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    if path is not None:
        env["PATH"] = path
    payload = {"tool_name": tool, "tool_input": {"command": command}}
    return subprocess.run(
        [BASH, str(HOOK)], input=json.dumps(payload), capture_output=True, text=True, env=env
    )


BLOCKED = [
    "npm install",
    "npx create-thing",
    "cd web && npm i",
    "make || npm test",
    "ls; npx tsc",
    "cat package.json | npm pkg get",
    "(npm i)",
    "echo $(npm bin)",
    "echo `npm bin`",
    "sudo npm i -g x",
    "command npm i",
    "env npx tsc",
    "exec npm start",
    "time npm test",
    "nohup npm start &",
    "FOO=1 npm i",
    "A=1 B=2 npx tsc",
    "env FOO=1 npm i",
    "/usr/local/bin/npm i",
    "./node_modules/.bin/npx tsc",
    "ls\nnpm i",
    "npm",
]

ALLOWED = [
    "pnpm install",
    "pnpm dlx create-thing",
    "deno run npm:prettier",
    "echo 'npm install'",
    'echo "run npx later"',
    "git commit -m 'drop npm'",
    "ls  # npm would be wrong here",
    "cat .npmrc",
    "ls node_modules/npm-run-all",
    "npmx --version",
    "echo npm",
    "grep -r npx src",
]


@pytest.mark.parametrize("command", BLOCKED)
def test_npm_in_command_position_is_blocked(command: str):
    result = run(command)
    assert result.returncode == 2, command
    assert "BLOCKED: Do not use npm or npx." in result.stderr


@pytest.mark.parametrize("command", ALLOWED)
def test_npm_outside_command_position_passes(command: str):
    result = run(command)
    assert (result.returncode, result.stderr) == (0, ""), command


def test_other_tools_pass():
    assert run("npm i", tool="Read").returncode == 0


def test_blocks_without_gnu_grep(tmp_path: Path):
    """Only bash builtins, jq, and sed on PATH: no grep of any flavor."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    for tool in ("jq", "sed", "cat"):
        found = shutil.which(tool)
        assert found, tool
        (bin_dir / tool).symlink_to(found)
    result = run("npm install", path=str(bin_dir))
    assert result.returncode == 2
    assert "BLOCKED: Do not use npm or npx." in result.stderr


def test_missing_jq_blocks(tmp_path: Path):
    empty = tmp_path / "empty"
    empty.mkdir()
    result = run("ls", path=str(empty))
    assert result.returncode == 2
    assert "jq is unavailable" in result.stderr
