#!/usr/bin/env bash
# Post-apply hook: Ensure Claude Code settings.json has correct hook paths
# This ensures hooks point to the right location regardless of $HOME
# Only updates if missing or incorrect; preserves other settings

set -euo pipefail

SETTINGS_FILE="${HOME}/.claude/settings.json"
EXPECTED_COMMAND="${HOME}/.local/bin/claude-block-npm"

if [[ ! -f "$SETTINGS_FILE" ]]; then
  exit 0
fi

# Check if PreToolUse hook already has the correct command
CURRENT_COMMAND=$(jq -r '.hooks.PreToolUse[0].hooks[0].command // "missing"' "$SETTINGS_FILE" 2>/dev/null || echo "missing")

if [[ "$CURRENT_COMMAND" == "$EXPECTED_COMMAND" ]]; then
  exit 0
fi

# Need to update: either missing or wrong path
if [[ "$CURRENT_COMMAND" == "missing" ]]; then
  # PreToolUse section doesn't exist or is incomplete — add it
  jq \
    --arg home "$HOME" \
    '.hooks.PreToolUse = [
      {
        "matcher": "Bash",
        "hooks": [
          {
            "type": "command",
            "command": ($home + "/.local/bin/claude-block-npm"),
            "timeout": 5
          }
        ]
      }
    ]' \
    "$SETTINGS_FILE" > "${SETTINGS_FILE}.tmp" && mv "${SETTINGS_FILE}.tmp" "$SETTINGS_FILE"
  echo "✓ Added PreToolUse hook to settings.json"
else
  # PreToolUse exists but path is wrong — update it
  jq \
    --arg home "$HOME" \
    '.hooks.PreToolUse[0].hooks[0].command = ($home + "/.local/bin/claude-block-npm")' \
    "$SETTINGS_FILE" > "${SETTINGS_FILE}.tmp" && mv "${SETTINGS_FILE}.tmp" "$SETTINGS_FILE"
  echo "✓ Updated PreToolUse hook path in settings.json"
fi
