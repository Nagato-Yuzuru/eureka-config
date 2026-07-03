#!/usr/bin/env bash
# Post-apply: converge Claude Code enforcement config in ~/.claude/settings.json.
#
# settings.json is machine-local and mutated by Claude Code itself, so chezmoi
# does not manage the file. This script converges only the entries we own —
# PreToolUse hook entries (keyed by script basename) and permissions.ask rules —
# and preserves everything else. Idempotent: silent no-op when already converged.

set -euo pipefail

SETTINGS_FILE="${HOME}/.claude/settings.json"
HOOKS_DIR="${HOME}/.claude/hooks"

mkdir -p "${HOME}/.claude"
if [[ ! -f "$SETTINGS_FILE" ]]; then
  echo '{}' > "$SETTINGS_FILE"
fi

merged=$(jq \
  --arg hooks_dir "$HOOKS_DIR" \
  '
  # PreToolUse entries owned by this script: script basename -> matcher.
  # Scripts are deployed by chezmoi to ~/.claude/hooks/.
  def managed: {
    "block-npm.sh":                "Bash",
    "require-agent-model.sh":      "^(Agent|Task)$",
    "require-workflow-routing.sh": "^Workflow$"
  };

  # Basenames whose entries we drop without re-adding (retired layouts).
  def legacy: ["claude-block-npm"];

  def basename: sub(".*/"; "");

  .hooks.PreToolUse = (
    ((.hooks.PreToolUse // [])
      | map(select(
          any(.hooks[]?;
            ((.command // "") | basename) as $b
            | (managed | has($b)) or (legacy | index($b) != null))
          | not)))
    + (managed | to_entries | map({
        matcher: .value,
        hooks: [{type: "command", command: ($hooks_dir + "/" + .key), timeout: 5}]
      }))
  )
  | .permissions.ask = (
      ((.permissions.ask // []) + ["Workflow", "Agent(model:*fable*)"]) | unique
    )
  ' "$SETTINGS_FILE")

if [[ "$merged" != "$(cat "$SETTINGS_FILE")" ]]; then
  printf '%s\n' "$merged" > "${SETTINGS_FILE}.tmp"
  mv "${SETTINGS_FILE}.tmp" "$SETTINGS_FILE"
  echo "✓ Converged Claude Code hook entries and ask rules in settings.json"
fi
