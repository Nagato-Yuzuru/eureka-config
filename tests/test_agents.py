"""Executable spec for the agent definitions (dot_claude/agents/*.md) and their wiring.

Invariants every valid agent config satisfies:
- an agent declaring ``memory:`` does not disallow Write or Edit — the injected memory
  instructions use the Write tool, so blocking it makes the memory feature inert
  (a ``tools:`` allowlist is fine: Claude Code re-adds Write/Edit for memory agents)
- frontmatter ``name`` equals the file stem, case included — APFS hides case renames
- every hook command a claude-managed.d fragment references exists as an ``executable_``
  source under dot_claude/hooks/

Behavior of require-agent-model.sh, driven as a subprocess with a fake $HOME (and a fake
project dir) holding synthetic agent files: an Agent/Task call passes only with an
explicit ``model``, a built-in exempt type, or a local agent whose frontmatter provably
pins a model. The hook may be stricter than Claude Code's agent loader, never looser.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "dot_claude" / "agents"
HOOKS_DIR = REPO_ROOT / "dot_claude" / "hooks"
MANAGED_DIR = REPO_ROOT / "dot_claude" / "exact_claude-managed.d"
MODEL_HOOK = HOOKS_DIR / "executable_require-agent-model.sh"

WRITE_TOOLS = frozenset({"Write", "Edit"})


def parse_frontmatter(text: str) -> dict[str, Any]:
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError("no frontmatter block")
    fields = yaml.safe_load(match.group(1))
    if not isinstance(fields, dict):
        raise ValueError(f"frontmatter is not a mapping: {fields!r}")
    return fields


def frontmatter(path: Path) -> dict[str, Any]:
    return parse_frontmatter(path.read_text())


def tool_set(value: object) -> set[str]:
    """A tools field as Claude Code accepts it: comma-separated string or YAML list."""
    if value is None:
        return set()
    items = value.split(",") if isinstance(value, str) else value
    assert isinstance(items, list), f"tools field must be a string or list, got {value!r}"
    return {str(t).strip() for t in items if str(t).strip()}


def memory_write_blockers(fields: dict[str, Any]) -> set[str]:
    """Write tools a memory-declaring agent disallows; empty when it disallows neither."""
    if "memory" not in fields:
        return set()
    return tool_set(fields.get("disallowedTools")) & WRITE_TOOLS


def agent_files() -> list[Path]:
    files = sorted(AGENTS_DIR.glob("*.md"))
    assert files, f"no agent definitions under {AGENTS_DIR}"
    return files


# --- invariants over the real agent definitions ------------------------------------


def test_agent_name_matches_file_stem():
    for path in agent_files():
        assert frontmatter(path).get("name") == path.stem, (
            f"{path.name}: frontmatter name must equal the file stem exactly (case included)"
        )


def test_memory_agents_keep_write_tools():
    for path in agent_files():
        blocked = memory_write_blockers(frontmatter(path))
        assert not blocked, (
            f"{path.name}: declares memory: but disallows {sorted(blocked)};"
            " the memory instructions write with the Write tool, so the feature would be inert"
        )


def test_managed_hook_commands_exist():
    referenced: set[str] = set()
    for fragment in sorted(MANAGED_DIR.glob("*.json*")):
        referenced.update(re.findall(r"/\.claude/hooks/([A-Za-z0-9_.-]+)", fragment.read_text()))
    missing = sorted(name for name in referenced if not (HOOKS_DIR / f"executable_{name}").exists())
    assert not missing, f"fragments reference hooks with no executable_ source: {missing}"


# --- the memory/write-tools check itself -------------------------------------------


@pytest.mark.parametrize(
    ("frontmatter_text", "expected"),
    [
        ("memory: project\ndisallowedTools: Agent, NotebookEdit\n", set()),
        ("memory: project\ndisallowedTools:\n  - Agent\n  - NotebookEdit\n", set()),
        ("memory: project\n", set()),
        ("memory: project\ntools: Read, Grep\n", set()),  # Claude Code re-adds Write/Edit
        ("disallowedTools: Write, Edit\n", set()),  # no memory: nothing to protect
        ("memory: project\ndisallowedTools: Agent, Write\n", {"Write"}),
        ("memory: project\ndisallowedTools:\n  - Edit\n  - Write\n", {"Write", "Edit"}),
        ("memory: project\ndisallowedTools: [Edit]\n", {"Edit"}),
    ],
)
def test_memory_write_blockers(frontmatter_text: str, expected: set[str]):
    fields = parse_frontmatter(f"---\n{frontmatter_text}---\nbody\n")
    assert memory_write_blockers(fields) == expected


# --- require-agent-model.sh behavior -----------------------------------------------


@pytest.fixture()
def home(tmp_path: Path) -> Path:
    h = tmp_path / "home"
    (h / ".claude" / "agents").mkdir(parents=True)
    return h


@pytest.fixture()
def project(tmp_path: Path) -> Path:
    p = tmp_path / "proj"
    (p / ".claude" / "agents").mkdir(parents=True)
    return p


def agents_dir(root: Path) -> Path:
    return root / ".claude" / "agents"


def write_agent(root: Path, filename: str, text: str) -> Path:
    path = agents_dir(root) / filename
    path.write_bytes(text.encode())
    return path


def agent_text(name: str = "pinned", model_line: str = "model: sonnet", extra: str = "") -> str:
    return f"---\nname: {name}\n{extra}{model_line}\n---\nbody\n"


def run_hook_raw(home: Path, stdin: str, path: str | None = None) -> str:
    env = {**os.environ, "HOME": str(home)}
    if path is not None:
        env["PATH"] = path
    result = subprocess.run(
        # The shebang interpreter: the source is not +x until chezmoi applies it, and
        # /bin/bash is 3.2 on macOS, the oldest bash the hook must run under.
        ["/bin/bash", str(MODEL_HOOK)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        check=True,
    )
    assert result.stderr == ""
    return result.stdout


def run_hook(home: Path, payload: object) -> str:
    return run_hook_raw(home, json.dumps(payload))


def agent_call(
    tool_input: dict[str, object], tool: str = "Agent", cwd: Path | None = None
) -> dict[str, Any]:
    call: dict[str, Any] = {"tool_name": tool, "tool_input": {"prompt": "p", **tool_input}}
    if cwd is not None:
        call["cwd"] = str(cwd)
    return call


def deny_reason(stdout: str) -> str:
    out = json.loads(stdout)["hookSpecificOutput"]
    assert out["hookEventName"] == "PreToolUse"
    assert out["permissionDecision"] == "deny"
    return out["permissionDecisionReason"]


def decide(home: Path, text: str, filename: str = "pinned.md", atype: str = "pinned") -> str:
    write_agent(home, filename, text)
    return run_hook(home, agent_call({"subagent_type": atype}))


# explicit model, pass-through, built-ins


@pytest.mark.parametrize("tool", ["Agent", "Task"])
def test_explicit_model_allows_any_type(home: Path, tool: str):
    call = agent_call({"subagent_type": "nonexistent", "model": "haiku"}, tool)
    assert run_hook(home, call) == ""


@pytest.mark.parametrize("model", ["", "   ", 1, {"a": 1}, ["opus"], None, True])
def test_non_string_or_blank_model_is_not_explicit(home: Path, model: object):
    reason = deny_reason(run_hook(home, agent_call({"subagent_type": "ghost", "model": model})))
    assert "subagent_type 'ghost' has no agent definition" in reason


@pytest.mark.parametrize("tool", ["Bash", "Read", ""])
def test_non_agent_tool_passes_through(home: Path, tool: str):
    assert run_hook(home, {"tool_name": tool, "tool_input": {}}) == ""


@pytest.mark.parametrize("atype", ["fork", "claude-code-guide", "statusline-setup"])
def test_builtin_exempt_types_allow_without_agent_file(home: Path, atype: str):
    assert run_hook(home, agent_call({"subagent_type": atype})) == ""


# hook input


@pytest.mark.parametrize(
    "stdin", ["", "not json", '{"tool_name": "Agent", "tool_input": {', "[]", '"Agent"', "null"]
)
def test_unparseable_input_fails_closed(home: Path, stdin: str):
    assert "the hook input is not a JSON object" in deny_reason(run_hook_raw(home, stdin))


def test_missing_jq_fails_closed(home: Path, tmp_path: Path):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    out = run_hook_raw(home, json.dumps(agent_call({"subagent_type": "x"})), path=str(empty))
    assert "jq is unavailable" in deny_reason(out)


@pytest.mark.parametrize("tool_input", [{}, {"subagent_type": ""}, {"subagent_type": 7}])
def test_missing_type_denies(home: Path, tool_input: dict[str, object]):
    assert "no subagent_type was given" in deny_reason(run_hook(home, agent_call(tool_input)))


@pytest.mark.parametrize("atype", ["../escape", "plugin:agent", "pinned/../pinned", "a b"])
def test_non_plain_type_never_resolves_to_a_file(home: Path, atype: str):
    write_agent(home, "pinned.md", agent_text())
    (home / ".claude" / "escape.md").write_text(agent_text("escape"))
    reason = deny_reason(run_hook(home, agent_call({"subagent_type": atype})))
    assert f"subagent_type '{atype}' is not a local agent name" in reason


def test_unknown_type_denies(home: Path):
    reason = deny_reason(run_hook(home, agent_call({"subagent_type": "ghost"})))
    assert "subagent_type 'ghost' has no agent definition at .claude/agents/ghost.md" in reason


# model value: a whitelist, not a blacklist


@pytest.mark.parametrize(
    "text",
    [
        agent_text(),
        agent_text(model_line="model: opus   "),
        agent_text(model_line="model:\thaiku"),
        agent_text(model_line="model: claude-opus-5-5[1m]"),
        agent_text(model_line="model: inherit-lite"),
        agent_text(model_line="model: sonnet", extra="skills:\n  - engineering\n"),
        agent_text().replace("\n", "\r\n"),
        "---\nname: pinned\nmodel: sonnet\n---",  # no trailing newline
    ],
)
def test_pinned_model_allows(home: Path, text: str):
    assert decide(home, text) == ""


@pytest.mark.parametrize(
    "model_line",
    [
        "model:",
        "model: inherit",
        "model: Inherit",
        "model: INHERIT",
        "model: ~",
        "model: null",
        "model: Null",
        "model: true",
        "model: no",
        "model: 123",
        "model: 1e3",
        "model: [opus]",
        "model: {}",
        'model: "   "',
        'model: " inherit "',
        'model: "sonnet"',
        "model: 'sonnet'",
        'model: "\\x69nherit"',
        "model: *a",
        "model: |\n  inherit",
        "model: >-\n  sonnet",
        "model: opus\n  inherit",  # plain-scalar continuation line
        "model: opus # pinned",
        "hooks:\n  model: opus",  # nested, not top-level
    ],
)
def test_unpinned_or_unprovable_model_denies(home: Path, model_line: str):
    reason = deny_reason(decide(home, agent_text(model_line=model_line)))
    assert f"{agents_dir(home)}/pinned.md does not pin a model" in reason


def test_model_only_in_body_denies(home: Path):
    reason = deny_reason(decide(home, "---\nname: pinned\n---\nmodel: opus\n"))
    assert f"{agents_dir(home)}/pinned.md does not pin a model" in reason


@pytest.mark.parametrize(
    "duplicate",
    [
        "model: opus",
        "model : inherit",
        '"model": inherit',
        "'model': inherit",
        "? model\n: inherit",
    ],
)
def test_duplicate_model_key_in_any_spelling_denies(home: Path, duplicate: str):
    text = agent_text(model_line=f"model: sonnet\n{duplicate}")
    reason = deny_reason(decide(home, text))
    assert f"{agents_dir(home)}/pinned.md does not pin a model" in reason


def test_escaped_quoted_key_denies(home: Path):
    # `"mod\x65l"` is `model` to a YAML parser; an escaped key could be any key, so the
    # first one checked (name) already refuses it.
    text = agent_text(model_line='model: sonnet\n"mod\\x65l": inherit')
    reason = deny_reason(decide(home, text))
    assert f"{agents_dir(home)}/pinned.md does not declare exactly 'name: pinned'" in reason


# frontmatter structure


@pytest.mark.parametrize(
    "text",
    [
        "",
        "name: pinned\nmodel: sonnet\n",  # no opening delimiter
        "\n---\nname: pinned\nmodel: sonnet\n---\n",  # delimiter not on line 1
        "﻿---\nname: pinned\nmodel: sonnet\n---\n",  # BOM
        "--- \nname: pinned\nmodel: sonnet\n---\n",  # opening with trailing blank
        "---\nname: pinned\nmodel: sonnet\n--- \nbody\n",  # closing with trailing blank
        "---\nname: pinned\nmodel: sonnet\n",  # never closed
        "---\nname: pinned\ndescription: a --- b\nmodel: sonnet\n---\n",  # mid-line close
        "---\nname: pinned\nx: ---\nmodel: inherit\n---\n",
    ],
)
def test_malformed_frontmatter_fails_closed(home: Path, text: str):
    reason = deny_reason(decide(home, text))
    assert f"{agents_dir(home)}/pinned.md has malformed frontmatter" in reason


def frontmatter_with_filler(filler_lines: int) -> str:
    filler = "".join(f"k{i}: v\n" for i in range(filler_lines))
    return f"---\nname: pinned\nmodel: sonnet\n{filler}---\nbody\n"


def test_frontmatter_closing_on_line_200_allows(home: Path):
    # line 1 `---`, 2 name, 3 model, 196 filler lines, line 200 `---`
    assert decide(home, frontmatter_with_filler(196)) == ""


def test_frontmatter_closing_past_line_200_denies(home: Path):
    reason = deny_reason(decide(home, frontmatter_with_filler(197)))
    assert f"{agents_dir(home)}/pinned.md has malformed frontmatter" in reason


# name and file resolution


@pytest.mark.parametrize(
    "name_line",
    [
        "name: other",
        "name: Pinned",
        'name: "pinned"',
        "name: pinned # x",
        "description: no name here",
        "name: pinned\nname: pinned",
        "name: pinned\n'name': other",
    ],
)
def test_name_must_equal_type_exactly(home: Path, name_line: str):
    text = f"---\n{name_line}\nmodel: sonnet\n---\nbody\n"
    reason = deny_reason(decide(home, text))
    assert f"{agents_dir(home)}/pinned.md does not declare exactly 'name: pinned'" in reason


def test_type_matches_file_name_case_exactly(home: Path):
    write_agent(home, "pinned.md", agent_text())
    reason = deny_reason(run_hook(home, agent_call({"subagent_type": "Pinned"})))
    assert "subagent_type 'Pinned' has no agent definition" in reason


@pytest.mark.parametrize("other_name", ["name: pinned", 'name: "pinned"', "'name': pinned"])
def test_second_file_claiming_the_name_denies(home: Path, other_name: str):
    write_agent(home, "pinned.md", agent_text())
    other = write_agent(home, "zz.md", f"---\n{other_name}\n---\nbody\n")
    reason = deny_reason(run_hook(home, agent_call({"subagent_type": "pinned"})))
    assert f"is not defined by exactly {agents_dir(home)}/pinned.md" in reason
    assert str(other) in reason


def test_file_named_differently_claiming_the_name_denies(home: Path):
    write_agent(home, "alias.md", agent_text())
    reason = deny_reason(run_hook(home, agent_call({"subagent_type": "pinned"})))
    assert f"claimed by: {agents_dir(home)}/alias.md" in reason


def test_similar_name_in_another_file_does_not_claim(home: Path):
    write_agent(home, "pinned.md", agent_text())
    write_agent(home, "pinned-lite.md", agent_text("pinned-lite", "model: haiku"))
    assert run_hook(home, agent_call({"subagent_type": "pinned"})) == ""


def test_project_agent_takes_precedence_and_denies(home: Path, project: Path):
    write_agent(home, "pinned.md", agent_text())
    write_agent(project, "pinned.md", agent_text(model_line="model: inherit"))
    reason = deny_reason(run_hook(home, agent_call({"subagent_type": "pinned"}, cwd=project)))
    assert f"{agents_dir(project)}/pinned.md does not pin a model" in reason


def test_project_agent_takes_precedence_and_allows(home: Path, project: Path):
    write_agent(home, "pinned.md", agent_text(model_line="model: inherit"))
    write_agent(project, "pinned.md", agent_text())
    assert run_hook(home, agent_call({"subagent_type": "pinned"}, cwd=project)) == ""


def test_project_alias_shadows_user_agent(home: Path, project: Path):
    write_agent(home, "pinned.md", agent_text())
    write_agent(project, "alias.md", agent_text(model_line="model: inherit"))
    reason = deny_reason(run_hook(home, agent_call({"subagent_type": "pinned"}, cwd=project)))
    assert f"claimed by: {agents_dir(project)}/alias.md" in reason


@pytest.mark.parametrize("has_agents_dir", [True, False])
def test_user_agent_governs_without_project_definition(
    home: Path, tmp_path: Path, has_agents_dir: bool
):
    cwd = tmp_path / "elsewhere"
    (cwd / ".claude" / "agents" if has_agents_dir else cwd).mkdir(parents=True)
    write_agent(home, "pinned.md", agent_text())
    assert run_hook(home, agent_call({"subagent_type": "pinned"}, cwd=cwd)) == ""
