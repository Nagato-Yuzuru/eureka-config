#!/usr/bin/env bash
# PreToolUse hook: block npm/npx in favor of pnpm/deno
# Reads tool input from stdin (JSON with tool_name and tool_input)

set -euo pipefail

INPUT=$(cat)
TOOL_NAME=$(echo "$INPUT" | jq -r '.tool_name // empty')

# Only check Bash tool calls
if [[ "$TOOL_NAME" != "Bash" ]]; then
    exit 0
fi

COMMAND=$(echo "$INPUT" | jq -r '.tool_input.command // empty')

# Check for npm/npx usage in command position only.
# 1. Strip single- and double-quoted strings so text like echo "use npm" is ignored.
# 2. Strip comments (# ...).
# 3. Match npm/npx only at command position: start of line, or after && || ; | ( — optionally preceded by sudo.
SANITIZED=$(echo "$COMMAND" | sed "s/'[^']*'//g; s/\"[^\"]*\"//g; s/#.*//")
if echo "$SANITIZED" | grep -qP '(?:^|&&|\|\|?|;|\()\s*(?:sudo\s+)?\bn(?:pm|px)\b'; then
    cat >&2 <<'EOF'
BLOCKED: Do not use npm or npx.

Use instead:
  npm install  → pnpm install
  npm run X    → pnpm run X
  npm add X    → pnpm add X
  npx X        → pnpm dlx X  (or  deno run npm:X  for non-interactive tools)

This is enforced by user policy. See ~/.claude/CLAUDE.md for details.
EOF
    exit 2
fi

exit 0
