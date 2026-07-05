#!/usr/bin/env bash
# Post-apply: converge Claude Code enforcement config in ~/.claude/settings.json.
#
# settings.json is machine-local and rewritten by Claude Code itself, so chezmoi
# doesn't manage the file directly. This script owns only two slices of it — our
# PreToolUse hook entries and the fable ask-rule — and leaves everything else
# untouched. Idempotent: a silent no-op once the file already matches.

set -euo pipefail

SETTINGS_FILE="${HOME}/.claude/settings.json"
HOOKS_DIR="${HOME}/.claude/hooks"

# Start from an empty object if the file doesn't exist yet, so jq has valid input.
mkdir -p "${HOME}/.claude"
if [[ ! -f "$SETTINGS_FILE" ]]; then
    echo '{}' >"$SETTINGS_FILE"
fi

# The transform, written as a jq program. In plain terms it does three things:
#   1. drop every PreToolUse entry that runs one of our hook scripts (current
#      OR retired), so stale copies never pile up;
#   2. re-add one fresh entry per script we manage today;
#   3. make sure the fable ask-rule is present, exactly once.
# Anything else already in the file is passed through unchanged.
#
# `cat <<'JQ' ... JQ` just means: everything up to the closing JQ is the jq
# program, taken literally (the quotes stop the shell touching $name, \(...), etc.).
converge=$(cat <<'JQ'
# Hook scripts we own -> the tool matcher each one guards.
# chezmoi deploys these to ~/.claude/hooks/; absolute paths get built below.
def managed: {
  "block-npm.sh":                "Bash",
  "require-agent-model.sh":      "^(Agent|Task)$",
  "require-workflow-routing.sh": "^Workflow$"
};

# Old script names we no longer ship: drop their entries, don't re-add them.
def legacy: ["claude-block-npm"];

# "/a/b/foo.sh" -> "foo.sh"
def basename: sub(".*/"; "");

# Does this PreToolUse entry run one of our scripts (managed now, or retired)?
def is_ours:
  any(.hooks[]?; (.command // "" | basename) as $name
      | (managed | has($name)) or (legacy | index($name)));

# One freshly-built PreToolUse entry per managed script.
def managed_entries:
  managed | to_entries | map({
    matcher: .value,
    hooks: [{ type: "command", command: "\($hooks_dir)/\(.key)", timeout: 5 }]
  });

# Keep the entries that aren't ours, then append our fresh ones.
.hooks.PreToolUse =
    [ (.hooks.PreToolUse // [])[] | select(is_ours | not) ] + managed_entries

# Ensure the fable ask-rule is in the list exactly once.
| .permissions.ask =
    ((.permissions.ask // []) + ["Agent(model:*fable*)"] | unique)
JQ
)

merged=$(jq --arg hooks_dir "$HOOKS_DIR" "$converge" "$SETTINGS_FILE")

# Write back only if the transform actually changed something.
if [[ "$merged" != "$(cat "$SETTINGS_FILE")" ]]; then
    printf '%s\n' "$merged" >"${SETTINGS_FILE}.tmp"
    mv "${SETTINGS_FILE}.tmp" "$SETTINGS_FILE"
    echo "✓ Converged Claude Code hook entries and ask rules in settings.json"
fi
