#!/bin/bash
# PreToolUse gate for Write/Edit: a lockfile belongs to the tool that generates it, so
# Write and Edit never touch one. Bash stays open because the package managers write
# lockfiles there; a permissions.deny rule would block them too, since the sandbox merges
# Edit denies into its own write denies.

deny() {
    jq -cn --arg reason "$1" \
        '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"deny",permissionDecisionReason:$reason}}'
    exit 0
}

if ! command -v jq >/dev/null; then
    echo "BLOCKED: jq is unavailable, so protect-lockfiles.sh cannot check this write." >&2
    exit 2
fi

input=$(cat)
jq -e 'type == "object"' >/dev/null 2>&1 <<<"$input" ||
    deny "protect-lockfiles.sh could not parse its input"
case "$(jq -r '.tool_name | strings' <<<"$input")" in
Write | Edit) ;;
*) exit 0 ;;
esac

path=$(jq -r '.tool_input.file_path | strings' <<<"$input")
case "${path##*/}" in
uv.lock) regen="uv lock (or uv add / uv remove / uv sync)" ;;
pnpm-lock.yaml) regen="pnpm install (or pnpm add / pnpm remove)" ;;
deno.lock) regen="deno install" ;;
Cargo.lock) regen="cargo update (or cargo add / cargo remove)" ;;
go.sum) regen="go mod tidy" ;;
mise.lock) regen="mise lock" ;;
*) exit 0 ;;
esac

deny "$path is a lockfile, owned by the tool that generates it; do not write it by hand. Change the manifest and regenerate with: $regen."
