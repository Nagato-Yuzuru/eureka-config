"""Executable spec for dot_claude/hooks/executable_block-package-managers.sh, the PreToolUse
hook that refuses npm/npx (pnpm and deno replace them) and the Python installers — pip,
virtualenv, poetry, python -m pip/venv/ensurepip (uv replaces them) — in Bash commands.

- a blocked name in command position is blocked (exit 2): at the start, after a separator or a
  subshell/substitution opener, behind the common wrappers (sudo, command, env, exec, time,
  nohup) and variable assignments, or spelled as a path
- the same words anywhere else pass: inside quotes, in comments, as parts of other words, and
  in the body of a heredoc whose delimiter is quoted; an unquoted heredoc's body, and every
  line after a terminator, are still checked
- python itself passes, whatever it runs, unless it runs an installer module
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
HOOK = REPO_ROOT / "dot_claude" / "hooks" / "executable_block-package-managers.sh"
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


HEREDOC_ALLOWED = [
    "git commit -F - <<'EOF'\nfix: stop calling `npx tsc`\n\nnpm i is gone too\nEOF",
    'cat > notes.md <<"END"\n- npx x\nEND',
    "cat <<\\EOF\npip install x\nEOF",
    "cat <<-'EOF'\n\tnpx x\n\tEOF",
    "cat <<'A' && cat <<'B'\nnpx\nA\nB",  # the second body is checked, and holds no command
]

HEREDOC_BLOCKED = [
    # Unquoted: the shell runs $(...) inside the body.
    "cat <<EOF\n$(npm i)\nEOF",
    # The command after the terminator is a command again.
    "cat <<'EOF'\ntext\nEOF\nnpx x",
    # A here-string is not a heredoc; the next line is a command.
    "cat <<<'x'\nnpx y",
    # A body line that only contains the delimiter does not end the heredoc early.
    "cat <<'EOF'\n EOF\nEOF\nnpm i",
]


@pytest.mark.parametrize("command", HEREDOC_ALLOWED)
def test_quoted_heredoc_bodies_are_text(command: str):
    result = run(command)
    assert (result.returncode, result.stderr) == (0, ""), command


@pytest.mark.parametrize("command", HEREDOC_BLOCKED)
def test_commands_around_and_inside_expanded_heredocs_are_checked(command: str):
    result = run(command)
    assert result.returncode == 2, command
    assert "BLOCKED: Do not use npm or npx." in result.stderr


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


PYTHON_BLOCKED = [
    "python3.12 -m venv .venv",
    "python -m pip install x",
    "python3 -mpip install x",
    "python3 -I -m pip install x",
    "sudo python3 -m pip install x",
    "env FOO=1 python3 -m ensurepip",
    ".venv/bin/python -m pip install x",
    "cd app && python -m venv .venv",
    "pip install requests",
    "pip3 install x",
    "sudo pip install x",
    ".venv/bin/pip install x",
    "pip list",
    "virtualenv .venv",
    "poetry add x",
]

PYTHON_ALLOWED = [
    "python script.py",
    "python3 -c 'print(1)'",
    "/usr/bin/python3 x.py",
    ".venv/bin/python x.py",
    "cd app && python manage.py",
    "FOO=1 python x.py",
    "echo $(python -V)",
    "python3 -m json.tool data.json",
    "python3 -m pipx_like_module",
    "python3 -c 'import pip'",
    "uv run python x.py",
    "uv run x.py",
    "uv add requests",
    "uvx ruff check",
    "uv run --with deptry deptry .",
    "echo 'python x.py'",
    "ls python/",
    "cat pip.conf",
    "pipx list",
    "python-config --help",
    "git log -- pyproject.toml",
    "grep -r poetry docs",
]


@pytest.mark.parametrize("command", PYTHON_BLOCKED)
def test_python_installers_in_command_position_are_blocked(command: str):
    result = run(command)
    assert result.returncode == 2, command
    assert "BLOCKED: Python packages and environments go through uv" in result.stderr


@pytest.mark.parametrize("command", PYTHON_ALLOWED)
def test_python_itself_uv_and_names_outside_command_position_pass(command: str):
    result = run(command)
    assert (result.returncode, result.stderr) == (0, ""), command
