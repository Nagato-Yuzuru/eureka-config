"""Executable spec for the agent definitions (dot_claude/agents/*.md.tmpl) and their wiring.

Invariants every valid agent config satisfies:
- an agent declaring ``memory:`` does not disallow Write or Edit — the injected memory
  instructions use the Write tool, so blocking it makes the memory feature inert
  (a ``tools:`` allowlist is fine: Claude Code re-adds Write/Edit for memory agents)
- frontmatter ``name`` equals the file stem, case included — APFS hides case renames
- every hook command a claude-managed.d fragment references exists as an ``executable_``
  source under dot_claude/hooks/
- routing has one source, .chezmoidata/claude.toml: every agent is the role of exactly one
  routing class and renders that class's model and effort, and no other file under
  dot_claude/ spells a model name the routing declares

Behavior of require-agent-model.sh, driven as a subprocess with a fake $HOME (and a fake
project dir) holding synthetic agent files: an Agent/Task call passes only with an
explicit ``model``, a built-in exempt type, or a local agent whose frontmatter provably
pins a model. The hook may be stricter than Claude Code's agent loader, never looser.
Its deny message, like require-workflow-routing.sh's, carries the rendered routing table.

Behavior of require-workflow-routing.sh, a textual screen rather than a JS parse: a
Workflow whose inline script (or, absent one, readable scriptPath file) calls agent() is
denied only when no ``model:`` or ``agentType:`` token appears anywhere in it; named
workflows and scripts without agent() pass. Like require-agent-model.sh, it denies when jq
is missing or the input is not a JSON object.
"""

from __future__ import annotations

import functools
import json
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any

import pytest
import yaml
from conftest import chezmoi_source
from pydantic import ValidationError

from inventory.model import ClaudeDoc

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "dot_claude" / "agents"
HOOKS_DIR = REPO_ROOT / "dot_claude" / "hooks"
MANAGED_DIR = REPO_ROOT / "claude-managed.d"
MODEL_HOOK = HOOKS_DIR / "executable_require-agent-model.sh"
WORKFLOW_HOOK = HOOKS_DIR / "executable_require-workflow-routing.sh"
ROUTING_TEXT = HOOKS_DIR / "agent-routing.txt.tmpl"
ROUTING_DATA = REPO_ROOT / ".chezmoidata" / "claude.toml"

WRITE_TOOLS = frozenset({"Write", "Edit"})


def parse_frontmatter(text: str) -> dict[str, Any]:
    match = re.match(r"\A---\n(.*?)\n---\n", text, re.DOTALL)
    if not match:
        raise ValueError("no frontmatter block")
    fields = yaml.safe_load(match.group(1))
    if not isinstance(fields, dict):
        raise ValueError(f"frontmatter is not a mapping: {fields!r}")
    return fields


@functools.cache
def render(tmpl: Path) -> str:
    """`chezmoi execute-template` over a copy of the real source. An empty config (not the user's): its
    read-source-state hook would run `make` from inside the test run."""
    proc = subprocess.run(
        [
            "chezmoi", "execute-template", "--source", str(chezmoi_source()),
            "--config-format", "toml", "--config", os.devnull, "--file", str(tmpl),
        ],
        text=True,
        capture_output=True,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def frontmatter(path: Path) -> dict[str, Any]:
    return parse_frontmatter(render(path))


def agent_name(path: Path) -> str:
    return path.name.removesuffix(".md.tmpl")


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
    files = sorted(AGENTS_DIR.glob("*.md.tmpl"))
    assert files, f"no agent definitions under {AGENTS_DIR}"
    return files


def routing() -> dict[str, Any]:
    return tomllib.loads(ROUTING_DATA.read_text())["claude"]["routing"]


def classes() -> list[dict[str, Any]]:
    return routing()["classes"]


def routed_models() -> set[str]:
    return {c["model"] for c in classes()} | set(routing().get("gated", []))


# --- invariants over the real agent definitions ------------------------------------


def test_agent_name_matches_file_stem():
    for path in agent_files():
        assert frontmatter(path).get("name") == agent_name(path), (
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
    for source in [*sorted(MANAGED_DIR.glob("*.json*")), *agent_files()]:
        referenced.update(re.findall(r"/\.claude/hooks/([A-Za-z0-9_.-]+)", source.read_text()))
    missing = sorted(name for name in referenced if not (HOOKS_DIR / f"executable_{name}").exists())
    assert not missing, f"fragments or agents reference hooks with no executable_ source: {missing}"


def test_explore_registers_its_write_gate():
    """The gate relies on running only inside explore, so explore registers it itself."""
    entries = frontmatter(AGENTS_DIR / "explore.md.tmpl")["hooks"]["PreToolUse"]
    gated = {
        tool
        for entry in entries
        if any(
            h["command"].endswith("/.claude/hooks/scope-explore-writes.sh") for h in entry["hooks"]
        )
        for tool in entry["matcher"].split("|")
    }
    assert gated >= WRITE_TOOLS
    for fragment in MANAGED_DIR.glob("*.json*"):
        assert "scope-explore-writes" not in fragment.read_text(), (
            f"{fragment.name}: a global registration would gate every agent's writes"
        )


# --- routing: one source -----------------------------------------------------------


def test_every_agent_is_the_role_of_exactly_one_routing_class():
    roles = sorted(r for c in classes() for r in c.get("roles", []))
    assert roles == sorted(agent_name(p) for p in agent_files())


def test_agent_renders_its_class_model_and_effort():
    for c in classes():
        for role in c.get("roles", []):
            fields = frontmatter(AGENTS_DIR / f"{role}.md.tmpl")
            assert fields["model"] == c["model"], role
            if "effort" in c:
                assert fields["effort"] == c["effort"], role


def test_agent_frontmatter_spells_each_rendered_key_once():
    """A class that gains an effort must not meet a hand-written one: YAML keeps the last."""
    for path in agent_files():
        head = render(path).split("\n---\n", 1)[0].splitlines()
        for key in ("model", "effort"):
            assert sum(line.startswith(f"{key}:") for line in head) <= 1, (path.name, key)


def model_names() -> re.Pattern[str]:
    names = sorted(routed_models())
    return re.compile(rf"\b({'|'.join(map(re.escape, names))})\b", re.IGNORECASE)


# Display code turning model ids into names: it names models, it routes nothing.
NOT_ROUTING = ("dot_claude/statusline/",)


def test_model_names_live_only_in_the_routing_data():
    listed = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", "dot_claude"],
        cwd=REPO_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split("\0")  # fmt: skip
    hits = [
        f"{rel}:{n}"
        for rel in listed
        if rel and not rel.startswith(NOT_ROUTING) and (REPO_ROOT / rel).is_file()
        for n, line in enumerate(
            (REPO_ROOT / rel).read_bytes().decode(errors="replace").splitlines(), 1
        )
        if model_names().search(line)
    ]
    assert hits == [], (
        "a model name outside .chezmoidata/claude.toml is a second copy of the routing;"
        " render it from the data (.chezmoitemplates/claude-*)"
    )


def claude_doc(
    classes: list[dict[str, Any]] | None = None, gated: list[str] | None = None
) -> dict[str, Any]:
    if classes is None:
        classes = [
            {"id": "a", "work": "w", "model": "haiku", "effort": "low", "roles": ["x"]},
            {"id": "b", "work": "w", "model": "opus", "roles": ["y"]},
        ]
    return {"claude": {"routing": {"classes": classes, "gated": gated or ["fable"]}}}


def test_claude_doc_accepts_the_baseline():
    ClaudeDoc.model_validate(claude_doc())


@pytest.mark.parametrize(
    ("doc", "why"),
    [
        (claude_doc(classes=[]), "at least 1 item"),
        (claude_doc(classes=[{"id": "a", "work": "w", "model": "haiku"}] * 2), "class ids repeat"),
        (
            claude_doc(
                classes=[
                    {"id": "a", "work": "w", "model": "haiku", "roles": ["x"]},
                    {"id": "b", "work": "w", "model": "opus", "roles": ["x"]},
                ]
            ),
            "more than one class",
        ),
        (claude_doc(gated=["opus"]), "gated models are some class's model: \\['opus'\\]"),
        (claude_doc(classes=[{"id": "a", "work": "w", "model": "gpt"}]), "Input should be"),
        (
            claude_doc(classes=[{"id": "a", "work": "w", "model": "haiku", "effort": "turbo"}]),
            "Input should be",
        ),
    ],
)
def test_claude_doc_rejects(doc: dict[str, Any], why: str):
    with pytest.raises(ValidationError, match=why):
        ClaudeDoc.model_validate(doc)


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


def run_hook_raw(home: Path, stdin: str, path: str | None = None, hook: Path = MODEL_HOOK) -> str:
    env = {**os.environ, "HOME": str(home)}
    if path is not None:
        env["PATH"] = path
    result = subprocess.run(
        # The shebang interpreter: the source is not +x until chezmoi applies it, and
        # /bin/bash is 3.2 on macOS, the oldest bash the hook must run under.
        ["/bin/bash", str(hook)],
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


def install_routing_text(home: Path) -> str:
    """The real rendered routing table, where apply puts it; returns it without the newline."""
    hooks = home / ".claude" / "hooks"
    hooks.mkdir(parents=True, exist_ok=True)
    text = render(ROUTING_TEXT)
    (hooks / "agent-routing.txt").write_text(text)
    return text.rstrip("\n")


WORKFLOW_UNROUTED = {"tool_name": "Workflow", "tool_input": {"script": "agent('x', {})"}}


@pytest.mark.parametrize(
    ("hook", "payload"),
    [(MODEL_HOOK, agent_call({"subagent_type": "ghost"})), (WORKFLOW_HOOK, WORKFLOW_UNROUTED)],
)
def test_deny_carries_the_rendered_routing_table(home: Path, hook: Path, payload: object):
    table = install_routing_text(home)
    reason = deny_reason(run_hook_raw(home, json.dumps(payload), hook=hook))
    assert reason.endswith(f"Routing table: {table}")
    assert all(m in table for m in routed_models())


@pytest.mark.parametrize(
    ("hook", "payload"),
    [(MODEL_HOOK, agent_call({"subagent_type": "ghost"})), (WORKFLOW_HOOK, WORKFLOW_UNROUTED)],
)
def test_deny_names_a_missing_routing_table(home: Path, hook: Path, payload: object):
    reason = deny_reason(run_hook_raw(home, json.dumps(payload), hook=hook))
    assert reason.endswith("(missing: ~/.claude/hooks/agent-routing.txt; chezmoi apply renders it)")


# require-workflow-routing.sh: a textual screen for agent() spawns with no routing at all


def workflow_call(tool_input: dict[str, object]) -> dict[str, Any]:
    return {"tool_name": "Workflow", "tool_input": tool_input}


def workflow_decision(home: Path, payload: object) -> str:
    stdin = payload if isinstance(payload, str) else json.dumps(payload)
    return run_hook_raw(home, stdin, hook=WORKFLOW_HOOK)


ROUTED_SCRIPTS = [
    "agent('x', {model: 'sonnet'})",
    "agent('x', {model : 'sonnet'})",
    "agent('x', {agentType: 'coder'})",
    "await agent(p, opts)\nconst opts = {\n  model:\n 'haiku' }",
]


@pytest.mark.parametrize("script", ROUTED_SCRIPTS)
def test_workflow_with_routing_passes(home: Path, script: str):
    assert workflow_decision(home, workflow_call({"script": script})) == ""


@pytest.mark.parametrize("script", ["return 1", "phase('x')", ""])
def test_workflow_without_agents_passes(home: Path, script: str):
    assert workflow_decision(home, workflow_call({"script": script})) == ""


@pytest.mark.parametrize(
    "script",
    ["agent('x', {})", "await agent(prompt)", "parallel([() => agent('a', {label: 'l'})])"],
)
def test_workflow_spawning_unrouted_agents_denies(home: Path, script: str):
    reason = deny_reason(workflow_decision(home, workflow_call({"script": script})))
    assert "contains no 'model:' or 'agentType:' routing" in reason


def test_workflow_script_path_is_screened(home: Path, tmp_path: Path):
    unrouted = tmp_path / "unrouted.js"
    unrouted.write_text("agent('x', {})\n")
    routed = tmp_path / "routed.js"
    routed.write_text("agent('x', {model: 'sonnet'})\n")
    assert deny_reason(workflow_decision(home, workflow_call({"scriptPath": str(unrouted)})))
    assert workflow_decision(home, workflow_call({"scriptPath": str(routed)})) == ""


def test_inline_script_wins_over_script_path(home: Path, tmp_path: Path):
    routed = tmp_path / "routed.js"
    routed.write_text("agent('x', {model: 'sonnet'})\n")
    call = workflow_call({"script": "agent('x', {})", "scriptPath": str(routed)})
    assert deny_reason(workflow_decision(home, call))


@pytest.mark.parametrize(
    "tool_input", [{"name": "review-changes"}, {"scriptPath": "/nonexistent/wf.js"}, {}]
)
def test_workflow_without_readable_script_passes(home: Path, tool_input: dict[str, object]):
    assert workflow_decision(home, workflow_call(tool_input)) == ""


@pytest.mark.parametrize("tool", ["Agent", "Bash", ""])
def test_workflow_hook_ignores_other_tools(home: Path, tool: str):
    payload = {"tool_name": tool, "tool_input": {"script": "agent('x', {})"}}
    assert workflow_decision(home, payload) == ""


@pytest.mark.parametrize("stdin", ["", "not json", "[]", "null"])
def test_workflow_hook_unparseable_input_fails_closed(home: Path, stdin: str):
    assert "the hook input is not a JSON object" in deny_reason(workflow_decision(home, stdin))


def test_workflow_hook_missing_jq_fails_closed(home: Path, tmp_path: Path):
    empty = tmp_path / "empty-path"
    empty.mkdir()
    payload = json.dumps(workflow_call({"script": "agent('x', {})"}))
    out = run_hook_raw(home, payload, path=str(empty), hook=WORKFLOW_HOOK)
    assert "jq is unavailable" in deny_reason(out)


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
