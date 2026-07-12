#!/usr/bin/env bash
# path-gate: protocol-ized file-access gate for Claude Code.
#
# Access model: a GRANT is (scope, path, mode[, expires]).
#   scope   — project root the grant applies to, or "*" for all projects
#   path    — root being granted; covers everything under it
#   mode    — r, w, or rw
#   expires — optional epoch seconds (from --ttl); expired grants are inert
#
# Hook mode (PreToolUse, JSON on stdin): Read needs r; Edit/Write/NotebookEdit
# need w. Targets inside the project workspace, built-in exceptions (session
# scratchpad, auto-memory, /dev/null), or a matching grant pass; everything
# else => permissionDecision "ask" (explicit per-call user authorization).
#
# CLI:
#   path-gate.sh grant  <path> <r|w|rw> [--global | --project DIR] [--ttl 30m|2h|7d] [--note TEXT]
#   path-gate.sh revoke <path> [--global | --project DIR]
#   path-gate.sh list
#   path-gate.sh check  <path> <r|w> [--project DIR]     # dry-run, no side effects
#
# grant/revoke require a TTY: Claude's Bash tool has none, so only the human
# operator can mutate grants. grants.json is tool-owned — never hand-edit it.

set -euo pipefail

GRANTS="$HOME/.claude/hooks/path-gate-grants.json"
CWD="$PWD"

# --- path helpers -----------------------------------------------------------

# Lexically normalize: collapse //, ., .. and expand ~ so prefix checks can't
# be dodged with traversal. Symlinks deliberately unresolved (targets may not
# exist yet for Write).
normalize() {
    local path="$1"
    # shellcheck disable=SC2088  # literal ~ match is intended: grant entries may contain unexpanded ~/
    [[ "$path" == "~" || "$path" == "~/"* ]] && path="$HOME${path#\~}"
    [[ "$path" != /* ]] && path="${CWD:-/}/$path"
    local -a out=() segs
    IFS='/' read -ra segs <<< "$path"
    local seg
    for seg in "${segs[@]}"; do
        case "$seg" in
            '' | '.') ;;
            '..') ((${#out[@]} > 0)) && out=("${out[@]:0:${#out[@]}-1}") ;;
            *) out+=("$seg") ;;
        esac
    done
    if ((${#out[@]} == 0)); then
        echo /
    else
        printf '/%s' "${out[@]}"
        echo
    fi
}

# under PATH ROOT — true if PATH is ROOT or inside it
under() {
    [[ -z "$2" ]] && return 1
    case "$1" in
        "$2" | "$2"/*) return 0 ;;
        *) return 1 ;;
    esac
}

# --- decision core ----------------------------------------------------------

# covered_by_grant TARGET NEEDED(r|w) PROJECT
# Malformed entries are inert (skipped), never fatal — a corrupt grants file
# must degrade to "ask", not crash the hook into fail-open.
covered_by_grant() {
    local target="$1" needed="$2" project="$3"
    [[ -r "$GRANTS" ]] || return 1
    local now entry scope gpath mode expires
    now=$(date +%s)
    while IFS= read -r entry; do
        expires=$(jq -r '.expires // empty' <<< "$entry")
        if [[ -n "$expires" ]]; then
            [[ "$expires" =~ ^[0-9]+$ ]] || continue
            ((expires < now)) && continue
        fi
        mode=$(jq -r '.mode // empty' <<< "$entry")
        # exact-set match, NOT substring: junk like "true" contains "r"
        [[ "$mode" =~ ^(r|w|rw)$ ]] || continue
        [[ "$mode" == *"$needed"* ]] || continue
        scope=$(jq -r '.scope // empty' <<< "$entry")
        [[ "$scope" == "*" || "$scope" == /* ]] || continue
        if [[ "$scope" != "*" ]]; then
            under "$project" "$(normalize "$scope")" || continue
        fi
        gpath=$(jq -r '.path // empty' <<< "$entry")
        [[ "$gpath" == /* ]] || continue
        under "$target" "$(normalize "$gpath")" && return 0
    done < <(jq -c '.grants[]?' "$GRANTS" 2> /dev/null)
    return 1
}

# allowed TARGET NEEDED(r|w) PROJECT
allowed() {
    local target="$1" needed="$2" project="$3"
    [[ "$target" == /dev/null ]] && return 0
    under "$target" "$(normalize "${project:-/nonexistent}")" && return 0
    # Session scratchpad / task-output dirs — this user's only ($UID pinned)
    under "$target" "/private/tmp/claude-${UID}" && return 0
    # Auto-memory: ~/.claude/projects/<one slug component>/memory/**
    # Decomposed by hand because [[ == pattern ]] lets * cross slashes.
    local rel="${target#"$HOME"/.claude/projects/}"
    if [[ "$rel" != "$target" && "$rel" == */* ]]; then
        local slug="${rel%%/*}" rest="${rel#*/}"
        [[ -n "$slug" && "$rest" == memory/* ]] && return 0
    fi
    covered_by_grant "$target" "$needed" "$project"
}

# --- hook mode --------------------------------------------------------------

# Fail-ask backstop: any unexpected crash in hook mode (unbound variable,
# missing jq, ...) would otherwise exit non-zero with no decision, which
# Claude Code treats as a non-blocking error — i.e. fail-OPEN. The EXIT trap
# converts every path that didn't reach an explicit decision into "ask".
# Static printf, no jq: it must work even when jq itself is the failure.
HOOK_DECIDED=0
hook_exit_backstop() {
    if [[ "${HOOK_DECIDED:-0}" != 1 ]]; then
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"path-gate hook crashed before reaching a decision; failing closed to ask. Debug: run ~/.claude/hooks/path-gate.sh manually and check path-gate-grants.json for malformed entries."}}'
    fi
    exit 0
}

hook_mode() {
    trap hook_exit_backstop EXIT
    local input tool file_path needed project target
    input=$(cat)
    tool=$(jq -r '.tool_name // empty' <<< "$input")

    case "$tool" in
        Read) needed=r ;;
        Edit | Write | NotebookEdit) needed=w ;;
        *)
            HOOK_DECIDED=1
            exit 0
            ;;
    esac

    file_path=$(jq -r '.tool_input.file_path // .tool_input.notebook_path // empty' <<< "$input")
    if [[ -z "$file_path" ]]; then
        HOOK_DECIDED=1
        exit 0
    fi

    CWD=$(jq -r '.cwd // empty' <<< "$input")
    project="${CLAUDE_PROJECT_DIR:-$CWD}"

    target=$(normalize "$file_path")
    # macOS: /tmp is a symlink to /private/tmp — compare in canonical form
    [[ "$target" == /tmp/* ]] && target="/private$target"

    if allowed "$target" "$needed" "$project"; then
        HOOK_DECIDED=1
        exit 0
    fi

    # HOOK_DECIDED is set only after jq succeeds: if this emission fails,
    # set -e aborts and the EXIT backstop prints the static ask JSON instead.
    jq -n --arg tool "$tool" --arg path "$target" --arg needed "$needed" \
        --arg proj "${project:-unknown}" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "ask",
            permissionDecisionReason: "\($tool) needs \(if $needed == "r" then "read" else "write" end) access to \($path) — outside the project workspace (\($proj)) and not covered by any grant. Approving allows this call only. For a durable grant, run in YOUR terminal: ~/.claude/hooks/path-gate.sh grant \($path) \($needed) [--global] [--ttl 2h]"
        }
    }'
    HOOK_DECIDED=1
    exit 0
}

# --- CLI --------------------------------------------------------------------

die() {
    echo "path-gate: $*" >&2
    exit 3
}

require_tty() {
    [[ -t 0 && -t 1 ]] || die "grant/revoke must be run by a human in a terminal (no TTY — Claude cannot self-authorize)"
}

ensure_store() {
    [[ -f "$GRANTS" ]] || printf '{"version":1,"grants":[]}\n' > "$GRANTS"
}

ttl_to_seconds() {
    [[ "$1" =~ ^([0-9]+)([smhd])$ ]] || die "bad --ttl '$1' (use e.g. 30m, 2h, 7d)"
    local n=${BASH_REMATCH[1]}
    case "${BASH_REMATCH[2]}" in
        s) echo "$n" ;;
        m) echo $((n * 60)) ;;
        h) echo $((n * 3600)) ;;
        d) echo $((n * 86400)) ;;
    esac
}

# parse_scope_args VAR_NAME remaining-args... — sets scope, echoes leftover count consumed handled inline instead
cmd_grant() {
    require_tty
    local path="" mode="" scope="$PWD" ttl="" note=""
    while (($# > 0)); do
        case "$1" in
            --global) scope='*' ;;
            --project)
                scope="$2"
                shift
                ;;
            --ttl)
                ttl="$2"
                shift
                ;;
            --note)
                note="$2"
                shift
                ;;
            -*) die "unknown flag $1" ;;
            *)
                if [[ -z "$path" ]]; then path="$1"; elif [[ -z "$mode" ]]; then mode="$1"; else die "unexpected arg $1"; fi
                ;;
        esac
        shift
    done
    [[ -n "$path" && -n "$mode" ]] || die "usage: grant <path> <r|w|rw> [--global | --project DIR] [--ttl 2h] [--note TEXT]"
    [[ "$mode" =~ ^(r|w|rw)$ ]] || die "mode must be r, w, or rw"

    path=$(normalize "$path")
    [[ "$scope" != "*" ]] && scope=$(normalize "$scope")

    local expires=null
    [[ -n "$ttl" ]] && expires=$(($(date +%s) + $(ttl_to_seconds "$ttl")))

    ensure_store
    local tmp
    tmp=$(mktemp)
    # prune expired entries, drop any older grant for the same (scope, path), append
    jq --arg scope "$scope" --arg path "$path" --arg mode "$mode" \
        --argjson expires "$expires" --arg note "$note" --argjson now "$(date +%s)" '
        .grants |= (
            map(select((.expires == null or .expires >= $now)
                       and (.scope != $scope or .path != $path)))
            + [{scope: $scope, path: $path, mode: $mode, expires: $expires}
               + (if $note != "" then {note: $note} else {} end)]
        )' "$GRANTS" > "$tmp" && mv "$tmp" "$GRANTS"
    echo "granted: scope=$scope path=$path mode=$mode expires=${ttl:-never}"
}

cmd_revoke() {
    require_tty
    local path="" scope="$PWD"
    while (($# > 0)); do
        case "$1" in
            --global) scope='*' ;;
            --project)
                scope="$2"
                shift
                ;;
            -*) die "unknown flag $1" ;;
            *)
                [[ -z "$path" ]] || die "unexpected arg $1"
                path="$1"
                ;;
        esac
        shift
    done
    [[ -n "$path" ]] || die "usage: revoke <path> [--global | --project DIR]"
    path=$(normalize "$path")
    [[ "$scope" != "*" ]] && scope=$(normalize "$scope")

    [[ -f "$GRANTS" ]] || die "no grants file"
    local tmp before after
    before=$(jq '.grants | length' "$GRANTS")
    tmp=$(mktemp)
    jq --arg scope "$scope" --arg path "$path" \
        '.grants |= map(select(.scope != $scope or .path != $path))' \
        "$GRANTS" > "$tmp" && mv "$tmp" "$GRANTS"
    after=$(jq '.grants | length' "$GRANTS")
    echo "revoked $((before - after)) grant(s) for scope=$scope path=$path"
}

cmd_list() {
    [[ -f "$GRANTS" ]] || {
        echo "no grants"
        return
    }
    jq -r --argjson now "$(date +%s)" '
        .grants[]
        | [ .scope, .path, .mode,
            (if .expires == null then "never"
             elif .expires < $now then "EXPIRED"
             else ((.expires - $now) / 60 | floor | tostring) + "m left" end),
            (.note // "") ]
        | @tsv' "$GRANTS" \
        | column -t -s $'\t' || echo "no grants"
}

cmd_check() {
    local path="" needed="" project="$PWD"
    while (($# > 0)); do
        case "$1" in
            --project)
                project="$2"
                shift
                ;;
            -*) die "unknown flag $1" ;;
            *)
                if [[ -z "$path" ]]; then path="$1"; elif [[ -z "$needed" ]]; then needed="$1"; else die "unexpected arg $1"; fi
                ;;
        esac
        shift
    done
    [[ -n "$path" && "$needed" =~ ^(r|w)$ ]] || die "usage: check <path> <r|w> [--project DIR]"
    local target
    target=$(normalize "$path")
    [[ "$target" == /tmp/* ]] && target="/private$target"
    if allowed "$target" "$needed" "$(normalize "$project")"; then
        echo "allow: $needed on $target (project $(normalize "$project"))"
    else
        echo "ask: $needed on $target (project $(normalize "$project")) — no covering grant"
        exit 1
    fi
}

# --- dispatch ---------------------------------------------------------------

if (($# == 0)); then
    hook_mode
fi

cmd="$1"
shift
case "$cmd" in
    grant) cmd_grant "$@" ;;
    revoke) cmd_revoke "$@" ;;
    list) cmd_list ;;
    check) cmd_check "$@" ;;
    *) die "unknown command '$cmd' (grant | revoke | list | check, or no args for hook mode)" ;;
esac
