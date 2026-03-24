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

# Check for npm/npx usage (word boundary matching)
# Allow "pnpm" — only block standalone "npm" or "npx"
if echo "$COMMAND" | grep -qE '(^|[;&|( ])n(pm|px)( |$|[;&|)])'; then
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
