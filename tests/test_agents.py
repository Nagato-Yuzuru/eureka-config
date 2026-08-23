"""Executable spec for the agent definitions (dot_claude/agents/*.md) and their wiring.

Each invariant is a fact the 2026-08 harness eval showed can break silently:
- an agent declaring ``memory:`` keeps Write and Edit available — the injected memory
  instructions use the Write tool, so disallowing it makes the memory feature inert
- frontmatter ``name`` equals the file stem, case included — APFS hides case renames,
  the exempt list in require-agent-model.sh does not
- the model-pin exempt list in require-agent-model.sh stays in sync with the agent files
- every hook command a claude-managed.d fragment references exists as an ``executable_``
  source under dot_claude/hooks/
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
AGENTS_DIR = REPO_ROOT / "dot_claude" / "agents"
HOOKS_DIR = REPO_ROOT / "dot_claude" / "hooks"
MANAGED_DIR = REPO_ROOT / "dot_claude" / "exact_claude-managed.d"
MODEL_HOOK = HOOKS_DIR / "executable_require-agent-model.sh"
# Exempt names that are built-in agent types, not files in dot_claude/agents/.
BUILTIN_EXEMPT = frozenset({"fork", "claude-code-guide", "statusline-setup"})


def frontmatter(path: Path) -> dict[str, str]:
    """Flat scalar keys of the YAML frontmatter; nested lines (lists, maps) are skipped."""
    match = re.match(r"\A---\n(.*?)\n---\n", path.read_text(), re.DOTALL)
    assert match, f"{path.name}: no frontmatter block"
    fields: dict[str, str] = {}
    for line in match.group(1).splitlines():
        if not line or line[0] in " \t-":
            continue
        key, sep, value = line.partition(":")
        if sep:
            fields[key.strip()] = value.strip()
    return fields


def disallowed_tools(fields: dict[str, str]) -> set[str]:
    return {t.strip() for t in fields.get("disallowedTools", "").split(",") if t.strip()}


def agent_files() -> list[Path]:
    files = sorted(AGENTS_DIR.glob("*.md"))
    assert files, f"no agent definitions under {AGENTS_DIR}"
    return files


def exempt_names() -> set[str]:
    """Names in the ``case "$atype" in`` allowlist of require-agent-model.sh."""
    text = MODEL_HOOK.read_text()
    block = re.search(r'case "\$atype" in\n(.*?)\nesac', text, re.DOTALL)
    assert block, "require-agent-model.sh: exempt case block not found"
    names: set[str] = set()
    for line in block.group(1).splitlines():
        line = line.strip()
        if line.endswith(")") and not line.startswith("*"):
            names.update(n.strip() for n in line[:-1].split("|"))
    assert names, "require-agent-model.sh: exempt list is empty"
    return names


def test_agent_name_matches_file_stem():
    for path in agent_files():
        assert frontmatter(path).get("name") == path.stem, (
            f"{path.name}: frontmatter name must equal the file stem exactly (case included)"
        )


def test_memory_agents_keep_write_tools():
    for path in agent_files():
        fields = frontmatter(path)
        if "memory" not in fields:
            continue
        blocked = {"Write", "Edit"} & disallowed_tools(fields)
        assert not blocked, (
            f"{path.name}: declares memory: {fields['memory']} but disallows {sorted(blocked)};"
            " the memory instructions write with the Write tool, so the feature would be inert"
        )


def test_model_pin_exempt_list_matches_agent_files():
    local = {frontmatter(p)["name"]: frontmatter(p) for p in agent_files()}
    pinned = {name for name, fields in local.items() if "model" in fields}
    exempt = exempt_names()
    assert pinned <= exempt, (
        f"model-pinned agents missing from the exempt list: {sorted(pinned - exempt)}"
    )
    stale = exempt - BUILTIN_EXEMPT - set(local)
    assert not stale, f"exempt list names agents that do not exist: {sorted(stale)}"


def test_managed_hook_commands_exist():
    referenced: set[str] = set()
    for fragment in sorted(MANAGED_DIR.glob("*.json*")):
        referenced.update(re.findall(r"/\.claude/hooks/([A-Za-z0-9_.-]+)", fragment.read_text()))
    assert referenced, f"no hook commands referenced under {MANAGED_DIR}"
    missing = sorted(name for name in referenced if not (HOOKS_DIR / f"executable_{name}").exists())
    assert not missing, f"fragments reference hooks with no executable_ source: {missing}"
