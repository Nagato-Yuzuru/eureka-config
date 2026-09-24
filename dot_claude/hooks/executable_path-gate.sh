#!/usr/bin/env bash
# path-gate v2 — whitelist file-access gate for Claude Code.
# Spec: docs/superpowers/specs/2026-07-15-path-gate-v2-design.md (repo-only).
#
# Modes:
#   (no args)  PreToolUse gate. Read/Grep/Glob need r; Edit/Write/NotebookEdit
#              need w. Decision chain (first match): unresolvable target
#              (w->deny, r->ask) -> store carve-out (w->deny, r->grants only;
#              any case spelling) -> /dev/null -> workspace (project +
#              permissions.additionalDirectories) -> /private/tmp -> current
#              project auto-memory -> structural whitelist (r-only) -> session
#              grants -> durable grants -> ask (+ pending marker). Allow steps
#              judge the RESOLVED path (realpath); the carve-out judges both
#              the lexical and resolved spellings.
#   record     PostToolUse/PostToolUseFailure: promote pending->granted.
#   grant <path> <r|w|rw> [--global|--project DIR] [--ttl 30m|2h|7d] [--note T]
#   revoke <path> [--global|--project DIR]
#   list
#   check <path> <r|w> [--project DIR] [--session SID]   # exit 0/1/2 = allow/ask/deny
#
# grant/revoke prompt via native permissions.ask rules (not a TTY). Stores are
# tool-owned: path-gate-grants.json (durable) and path-gate-sessions/ (per
# session). Never hand-edit either.

set -euo pipefail

CWD="$PWD"

# --- path helpers -----------------------------------------------------------

# Lexically normalize: collapse //, ., .. and expand ~ so prefix checks can't
# be dodged with traversal. Symlinks are handled by resolve(), not here.
normalize() {
    local path="$1"
    # shellcheck disable=SC2088
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

# Like normalize() but keeps '..' segments — for feeding realpath, which must
# see the pre-collapse path so a symlink component is followed before '..'.
normalize_raw() {
    local path="$1"
    # shellcheck disable=SC2088
    [[ "$path" == "~" || "$path" == "~/"* ]] && path="$HOME${path#\~}"
    [[ "$path" != /* ]] && path="${CWD:-/}/$path"
    local -a out=() segs
    IFS='/' read -ra segs <<< "$path"
    local seg
    for seg in "${segs[@]}"; do
        case "$seg" in
            '' | '.') ;;
            *) out+=("$seg") ;;
        esac
    done
    if ((${#out[@]} == 0)); then echo /; else printf '/%s' "${out[@]}"; echo; fi
}

# Lexical spelling, no symlink following. Only the tool-owned-store check uses
# it, to catch a path by name as well as by target. Everything a decision
# compares against a resolved path goes through real(), so the two spellings
# never have to be reconciled by hand.
lex() { normalize "$1"; }

# under PATH ROOT — true if PATH is ROOT or inside it
under() {
    [[ -z "$2" ]] && return 1
    case "$1" in
        "$2" | "$2"/*) return 0 ;;
        *) return 1 ;;
    esac
}

# may_alias PATH STORE — true if PATH could name STORE or something inside it
# on a case-insensitive volume. The store check needs this and under() is not
# enough: realpath canonicalizes only names that exist, and a store is absent
# until the first grant, so a case variant keeps the caller's spelling and then
# lands on the real store. Pre-creating the stores would close that only while
# they exist; comparing spellings holds in every state.
# Such volumes fold with full Unicode rules (APFS: ß=ss, ſ=s, ﬅ=st, K=k) plus
# normalization, which no ASCII table reproduces, so the match over-approximates
# — deny/ask is the safe side — within tight bounds:
# - Component by component (no fold yields '/'): PATH needs at least STORE's
#   component count, and each of the first N must be able to alias STORE's.
#   Cost is linear in STORE's depth, not PATH's length.
# - Both ASCII-only: case-insensitive equality.
# - Only PATH's has non-ASCII: a non-ASCII character folds (full case folding
#   then NFD) to at most 3 ASCII characters, all letters or ';' '`', or to
#   nothing (HFS+ ignores some code points). So each run of k non-ASCII bytes
#   stands for 0..3k of those and nothing else: a non-ASCII name can never
#   spell a store name's '-' or '.'.
# - STORE's has non-ASCII (only $HOME's own components can): each non-ASCII
#   run is an unbounded wildcard, tried in both directions.
# Applied on every volume: detecting the volume's rules per path costs more.
may_alias() {
    [[ -n "$2" ]] || return 1
    local rc=0
    shopt -s nocasematch # ASCII-only under the LC_ALL=C set below
    _may_alias "$1" "$2" || rc=$?
    shopt -u nocasematch
    return "$rc"
}

_may_alias() {
    local LC_ALL=C # byte-wise ${#}, ${:i:1}, [[ ]] ranges; ASCII-only case folding
    local p="${1#/}" s="${2#/}" pc sc
    while [[ -n "$s" ]]; do
        [[ -n "$p" ]] || return 1 # PATH is shallower than STORE
        sc="${s%%/*}"
        if [[ "$s" == */* ]]; then s="${s#*/}"; else s=""; fi
        pc="${p%%/*}"
        if [[ "$p" == */* ]]; then p="${p#*/}"; else p=""; fi
        component_may_alias "$pc" "$sc" || return 1
    done
}

_PG_HIGH=$'\x80'-$'\xff' # non-ASCII byte range, for [[ ]] under LC_ALL=C

component_may_alias() { # P S — caller holds LC_ALL=C and nocasematch
    local rc=0 g1
    if [[ "$1$2" != *[$_PG_HIGH]* ]]; then
        [[ "$1" == "$2" ]]
        return
    fi
    # Over any NAME_MAX (255 chars x 4 UTF-8 bytes) no volume creates the name,
    # so "may alias" costs nothing and spares the per-byte fold below.
    ((${#1} <= 1024 && ${#2} <= 1024)) || return 0
    if [[ "$2" != *[$_PG_HIGH]* ]]; then
        fold_regex "$1"
        # shellcheck disable=SC2319 # the [[ ]] status is the point: 1 vs 2
        [[ "$2" =~ $_PG_FOLD ]] || rc=$?
        ((rc != 1)) # 2 = regex error: fail closed, count it as a match
        return
    fi
    fold_glob "$1"
    g1="$_PG_FOLD"
    fold_glob "$2"
    # shellcheck disable=SC2053 # the right side is a glob on purpose
    [[ "$2" == $g1 || "$1" == $_PG_FOLD ]]
}

# fold_regex NAME -> _PG_FOLD: an anchored ERE for the ASCII names NAME may
# alias — ASCII bytes literal, each run of k non-ASCII bytes [a-z;`]{0,3k}
# (a byte count bounds the character count from above). Past RE_DUP_MAX (255)
# the bound is dropped. Results go through a variable, not stdout: $() would
# strip a trailing newline from the name.
_PG_FOLD=""
fold_regex() {
    local LC_ALL=C name="$1" re="^" c i run=0
    for ((i = 0; i <= ${#name}; i++)); do
        c="${name:i:1}" # "" past the end: flushes a trailing run
        if [[ -n "$c" && "$c" == [$_PG_HIGH] ]]; then
            run=$((run + 1))
            continue
        fi
        if ((run > 0)); then
            if ((run * 3 > 255)); then re+='[a-z;`]*'; else re+="[a-z;\`]{0,$((run * 3))}"; fi
            run=0
        fi
        case "$c" in
            '') ;;
            '^' | "\\") re+="\\$c" ;;
            *) re+="[$c]" ;;
        esac
    done
    _PG_FOLD="$re\$"
}

# fold_glob NAME -> _PG_FOLD: a glob matching NAME with every ASCII byte
# literal and every run of non-ASCII bytes standing for anything.
fold_glob() {
    local LC_ALL=C name="$1" pat="" c i wild=0
    for ((i = 0; i < ${#name}; i++)); do
        c="${name:i:1}"
        if [[ "$c" != [$_PG_HIGH] ]]; then
            pat+="\\$c"
            wild=0
        elif ((wild == 0)); then
            pat+='*'
            wild=1
        fi
    done
    _PG_FOLD="$pat"
}

# resolve RAWPATH -> real path (R), or status 1 when it cannot be resolved.
# Input comes from normalize_raw(): '..' segments are intact so realpath
# follows a symlink component BEFORE applying '..' — collapsing first would
# let 'link/../x' escape unresolved.
# - Existing path: realpath.
# - Otherwise find the nearest ancestor that exists or is a symlink. A dangling
#   symlink there is followed by hand (readlink, then start over with its
#   target + the rest): the kernel follows it too, so a create through it —
#   O_CREAT on a final-component link, or a parent made along the way — lands
#   at the target, which is what must be judged.
# - Otherwise realpath of that ancestor plus the absent suffix ('..' collapsed
#   lexically — nothing left there to follow), so a symlinked parent cannot
#   smuggle a create elsewhere.
# Following more than PG_MAX_HOPS links (a loop, or a chain longer than any
# kernel follows: Linux MAXSYMLINKS 40, macOS 32) is unresolvable: status 1,
# with a best-effort lexical spelling on stdout for messages only. Deciding
# on the last hop reached instead would judge a path the write never lands on.
PG_MAX_HOPS=40
resolve() {
    local p="$1" hops=0 t cur suffix
    while :; do
        if [[ -e "$p" ]]; then
            t=$(/bin/realpath "$p" 2> /dev/null) || break
            echo "$t"
            return 0
        fi
        cur="$p" suffix=""
        while [[ "$cur" != / && ! -e "$cur" && ! -L "$cur" ]]; do
            suffix="/${cur##*/}$suffix"
            cur="${cur%/*}"
            [[ -z "$cur" ]] && cur=/
        done
        if [[ -L "$cur" && ! -e "$cur" ]]; then
            ((hops < PG_MAX_HOPS)) || break
            t=$(/usr/bin/readlink "$cur") || break # macOS: readlink is /usr/bin, realpath /bin
            [[ "$t" != /* ]] && t="${cur%/*}/$t"
            p=$(normalize_raw "$t$suffix")
            hops=$((hops + 1))
            continue
        fi
        if [[ "$cur" == / ]]; then
            normalize "$p"
            return 0
        fi
        t=$(/bin/realpath "$cur" 2> /dev/null) || break
        [[ "$t" == / ]] && t=""
        normalize "$t$suffix"
        return 0
    done
    normalize "$p"
    return 1
}

# The space every allow/deny comparison happens in. Status 1 = unresolvable.
real() { resolve "$(normalize_raw "$1")"; }

# Roots, resolved. $HOME itself can sit behind a symlink (on macOS /tmp and /var
# are symlinks into /private), so ask the filesystem instead of hardcoding which
# platform redirects what. On a system that redirects nothing this is identity.
# An unresolvable root (a symlink loop) keeps its lexical spelling rather than
# aborting before any decision; the store check matches that spelling anyway.
HOME_R=$(real "$HOME") || true
TMP_R=$(real /tmp) || true
GRANTS="$HOME_R/.claude/hooks/path-gate-grants.json"
SESSIONS_DIR="$HOME_R/.claude/hooks/path-gate-sessions"
GRANTS_L=$(lex "$HOME/.claude/hooks/path-gate-grants.json")
SESSIONS_DIR_L=$(lex "$HOME/.claude/hooks/path-gate-sessions")

# Claude Code's project-dir slug: every non-[A-Za-z0-9] character -> '-'.
# ASCII-only regardless of locale (LC_ALL=C — [[:alnum:]] would keep non-ASCII
# letters and diverge). printf, not echo: the slug always starts with '-'.
# Undocumented upstream; pinned by tests against real ~/.claude/projects names.
slug_of() {
    local s
    s=$(LC_ALL=C; printf '%s' "${1//[^A-Za-z0-9]/-}")
    printf '%s\n' "$s"
}

# Timestamps: all expiry math uses jq's builtin `now` (fractional-second)
# inside each jq program — never `date +%s`, whose whole-second floor on both
# the write and read side lets a just-expired short TTL read as valid for up
# to ~2s. (BSD `date` on macOS has no `%N`; jq is already a hard dependency.)

# --- decision core ----------------------------------------------------------

# permissions.additionalDirectories from user/project/local settings scopes.
addl_dirs() {
    local project="$1" f
    local -a files=()
    for f in "$HOME/.claude/settings.json" "$project/.claude/settings.json" \
        "$project/.claude/settings.local.json"; do
        [[ -f "$f" ]] && files+=("$f")
    done
    ((${#files[@]})) || return 0
    jq -r '(.permissions.additionalDirectories // [])[]' "${files[@]}" 2> /dev/null || true
}

# One jq pass over the durable store. Entries are written normalized by the
# CLI; malformed entries fail the selects and are inert. A corrupt file makes
# jq fail -> return 1 -> the chain falls through to ask (fail-closed).
grant_covers() { # TARGET NEEDED PROJECT
    [[ -r "$GRANTS" ]] || return 1
    jq -e --arg t "$1" --arg m "$2" --arg proj "$3" '
        [.grants[]? | select(
            ((.expires // null) == null or ((.expires | type) == "number" and .expires >= now))
            and ((.mode // "") | IN("r", "w", "rw"))
            and (.mode | contains($m))
            and ((.scope // "") as $s
                 | ($s == "*") or (($s | startswith("/"))
                    and ($proj == $s or ($proj | startswith($s + "/")))))
            and ((.path // "") as $p
                 | ($p | startswith("/"))
                   and ($t == $p or ($t | startswith($p + "/"))))
        )] | length > 0' "$GRANTS" > /dev/null 2>&1
}

session_covers() { # TARGET NEEDED SID
    [[ -n "$3" ]] || return 1
    local f="$SESSIONS_DIR/$3.json"
    [[ -s "$f" ]] || return 1
    jq -e --arg t "$1" --arg m "$2" '
        [.granted[]? | select(
            ((.mode // "") | contains($m))
            and ((.path // "") as $p
                 | ($p | startswith("/"))
                   and ($t == $p or ($t | startswith($p + "/"))))
        )] | length > 0' "$f" > /dev/null 2>&1
}

# allowed L R NEEDED PROJECT SID -> 0 allow | 1 ask | 2 deny
# R="" means the target is unresolvable (see resolve); PROJECT="" matches
# nothing.
allowed() {
    local L="$1" R="$2" needed="$3" project="$4" sid="$5" store d dr
    # Unresolvable: where a write lands is unknown, so it may be a store ->
    # deny. A read cannot forge anything -> ask, never allow.
    if [[ -z "$R" ]]; then
        [[ "$needed" == w ]] && return 2
        return 1
    fi
    # step 0: tool-owned stores, checked against BOTH spellings, in any case
    for store in "$GRANTS" "$SESSIONS_DIR" "$GRANTS_L" "$SESSIONS_DIR_L"; do
        if may_alias "$L" "$store" || may_alias "$R" "$store"; then
            [[ "$needed" == w ]] && return 2
            grant_covers "$R" "$needed" "$project" && return 0
            return 1
        fi
    done
    [[ "$R" == /dev/null ]] && return 0
    under "$R" "$project" && return 0
    while IFS= read -r d; do
        [[ -n "$d" ]] || continue
        dr=$(real "$d") || continue
        under "$R" "$dr" && return 0
    done < <(addl_dirs "$project")
    under "$R" "$TMP_R" && return 0
    local slug
    slug=$(slug_of "$project")
    under "$R" "$HOME_R/.claude/projects/$slug/memory" && return 0
    if [[ "$needed" == r ]]; then
        under "$R" "$HOME_R/.claude/projects/$slug" && return 0
        for d in plugins skills agents commands rules output-styles; do
            under "$R" "$HOME_R/.claude/$d" && return 0
        done
        [[ "$R" == "$HOME_R/.claude/CLAUDE.md" || "$R" == "$HOME_R/.claude/keybindings.json" ]] && return 0
    fi
    session_covers "$R" "$needed" "$sid" && return 0
    grant_covers "$R" "$needed" "$project" && return 0
    return 1
}

# --- hook mode --------------------------------------------------------------

# Fail-ask backstop: any unexpected crash in gate mode would otherwise exit
# non-zero with no decision, which Claude Code treats as non-blocking — i.e.
# fail-OPEN. Static printf, no jq: must work when jq itself is the failure.
HOOK_DECIDED=0
hook_exit_backstop() {
    if [[ "${HOOK_DECIDED:-0}" != 1 ]]; then
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"path-gate crashed before reaching a decision; failing closed to ask. Debug: run ~/.claude/hooks/path-gate.sh manually and check the stores for malformed entries."}}'
    fi
    exit 0
}

emit_ask() { # TOOL NEEDED R
    jq -n --arg tool "$1" --arg needed "$2" --arg path "$3" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "ask",
            permissionDecisionReason: "\($tool) needs \(if $needed == "r" then "read" else "write" end) access to \($path) — outside the workspace and whitelist. Approve = allowed for the rest of this session (auto-recorded). Durable: ask Claude to run ~/.claude/hooks/path-gate.sh grant \"\($path)\" \($needed) [--ttl 7d] — it always confirms with you first."
        }
    }'
}

# deny_reason R L — why allowed() returned 2; R="" is the unresolvable case.
deny_reason() {
    if [[ -z "$1" ]]; then
        printf '%s\n' "$2 cannot be resolved (symlink loop, or a chain longer than $PG_MAX_HOPS links), so the write could land on a tool-owned path-gate store."
    else
        printf '%s\n' "$1 is a tool-owned path-gate store. File tools may never write it; grants are issued only via ~/.claude/hooks/path-gate.sh grant."
    fi
}

emit_deny() { # REASON
    jq -n --arg why "$1" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $why
        }
    }'
}

needed_for() { # TOOL -> r | w | "" (not gated)
    case "$1" in
        Read | Grep | Glob) echo r ;;
        Edit | Write | NotebookEdit) echo w ;;
        *) echo "" ;;
    esac
}

hook_mode() {
    trap hook_exit_backstop EXIT
    local input fields tool fpath cwd sid pmode
    input=$(cat)
    fields=$(jq -r '[.tool_name // "",
                     (.tool_input.file_path // .tool_input.notebook_path // .tool_input.path // ""),
                     .cwd // "", .session_id // "", .permission_mode // ""] | @tsv' <<< "$input")
    # NOT `IFS=$'\t' read -r ... <<< "$fields"`: bash classifies tab as
    # "IFS whitespace" regardless of what IFS is set to, so consecutive tabs
    # collapse instead of yielding an empty field — a real Grep/Glob call
    # with no path param (empty fpath) would silently shift every later
    # field left by one. Literal substring splitting can't collapse.
    local rest
    tool="${fields%%$'\t'*}"; rest="${fields#*$'\t'}"
    fpath="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
    cwd="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
    sid="${rest%%$'\t'*}"; pmode="${rest#*$'\t'}"
    local needed
    needed=$(needed_for "$tool")
    if [[ -z "$needed" || -z "$fpath" ]]; then
        HOOK_DECIDED=1
        exit 0
    fi
    CWD="$cwd"
    local project L R rc=0
    project=$(real "${CLAUDE_PROJECT_DIR:-${cwd:-/nonexistent}}") || project=""
    L=$(lex "$fpath")
    R=$(real "$fpath") || R=""
    allowed "$L" "$R" "$needed" "$project" "$sid" || rc=$?
    case "$rc" in
        0)
            HOOK_DECIDED=1
            exit 0
            ;;
        2)
            emit_deny "$(deny_reason "$R" "$L")"
            HOOK_DECIDED=1
            exit 0
            ;;
    esac
    # no pending for an unresolvable target: nothing it could be promoted to
    [[ "$pmode" != "bypassPermissions" && -n "$R" ]] && pending_add "$sid" "$R" "$needed"
    emit_ask "$tool" "$needed" "${R:-$L}"
    HOOK_DECIDED=1
    exit 0
}

# Record a pending marker before emitting ask. Best-effort: a failure here
# costs one extra prompt later, never an escalation — so errors are swallowed.
# Session ids become filenames: refuse anything but a safe charset.
pending_add() { # SID PATH MODE
    [[ "$1" =~ ^[A-Za-z0-9._-]+$ ]] || return 0
    mkdir -p "$SESSIONS_DIR" 2> /dev/null || return 0
    local f="$SESSIONS_DIR/$1.json" tmp
    [[ -s "$f" ]] || printf '{"pending":[],"granted":[]}\n' > "$f" 2> /dev/null || return 0
    tmp=$(mktemp "$SESSIONS_DIR/.pg.XXXXXX") || return 0
    if jq --arg p "$2" --arg m "$3" '
        .pending |= (map(select(.path != $p or .mode != $m)) + [{path: $p, mode: $m}])
    ' "$f" > "$tmp" 2> /dev/null; then
        mv "$tmp" "$f" 2> /dev/null || command rm -f "$tmp"
    else
        command rm -f "$tmp"
    fi
    return 0
}

# PostToolUse/PostToolUseFailure: the tool executed. For a gated call that
# means a human approved the prompt — promote pending -> granted, but only if
# (1) the exact (path, mode) is pending, (2) we are not in bypassPermissions
# (no prompt was shown there and no pending was written; a stale pending from
# a denied prompt must not be promotable), and (3) allowed() still says ask -
# execution under a durable grant proves nothing, and recording would let a
# later revoke be outlived. record must NEVER block a tool: exit 0 always.
cmd_record() {
    trap 'exit 0' EXIT
    local input sid
    input=$(cat)
    # pure-bash early exit: no jq unless a session file with content exists
    [[ "$input" =~ \"session_id\"[[:space:]]*:[[:space:]]*\"([A-Za-z0-9._-]+)\" ]] || exit 0
    sid="${BASH_REMATCH[1]}"
    local f="$SESSIONS_DIR/$sid.json"
    [[ -s "$f" ]] || exit 0

    local fields tool fpath cwd _sid pmode rest
    fields=$(jq -r '[.tool_name // "",
                     (.tool_input.file_path // .tool_input.notebook_path // .tool_input.path // ""),
                     .cwd // "", .session_id // "", .permission_mode // ""] | @tsv' <<< "$input") || exit 0
    # substring split (not `IFS=$'\t' read`: bash collapses consecutive tabs)
    tool="${fields%%$'\t'*}"; rest="${fields#*$'\t'}"
    fpath="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
    cwd="${rest%%$'\t'*}"; rest="${rest#*$'\t'}"
    _sid="${rest%%$'\t'*}"; pmode="${rest#*$'\t'}"
    [[ "$pmode" == "bypassPermissions" ]] && exit 0
    local needed
    needed=$(needed_for "$tool")
    [[ -n "$needed" && -n "$fpath" ]] || exit 0

    CWD="$cwd"
    local project L R rc=0
    project=$(real "${CLAUDE_PROJECT_DIR:-${cwd:-/nonexistent}}") || project=""
    L=$(lex "$fpath")
    R=$(real "$fpath") || exit 0 # unresolvable: never pending, nothing to promote

    jq -e --arg p "$R" --arg m "$needed" \
        '.pending[]? | select(.path == $p and .mode == $m)' "$f" > /dev/null 2>&1 || exit 0
    allowed "$L" "$R" "$needed" "$project" "$sid" || rc=$?
    [[ "$rc" == 1 ]] || exit 0

    local tmp
    tmp=$(mktemp "$SESSIONS_DIR/.pg.XXXXXX") || exit 0
    if jq --arg p "$R" --arg m "$needed" '
        .pending |= map(select(.path != $p or .mode != $m))
        | .granted |= (map(select(.path != $p or .mode != $m)) + [{path: $p, mode: $m}])
    ' "$f" > "$tmp" 2> /dev/null; then
        mv "$tmp" "$f" 2> /dev/null || command rm -f "$tmp"
    else
        command rm -f "$tmp"
    fi
    find "$SESSIONS_DIR" -type f -name '*.json' -mtime +7 -delete 2> /dev/null || true
    exit 0
}

# --- CLI --------------------------------------------------------------------

die() {
    echo "path-gate: $*" >&2
    exit 3
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

cmd_grant() {
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

    path=$(real "$path") || die "cannot resolve $path (symlink loop or chain over $PG_MAX_HOPS links)"
    if [[ "$scope" != "*" ]]; then scope=$(real "$scope") || die "cannot resolve scope $scope"; fi

    local expires=null
    [[ -n "$ttl" ]] && expires=$(jq -n --argjson ttl "$(ttl_to_seconds "$ttl")" '(now + $ttl)')

    ensure_store
    local tmp
    # tmp in the store's own dir: mv is then a same-filesystem rename (atomic);
    # $TMPDIR may be another volume, where mv degrades to copy+unlink.
    tmp=$(mktemp "$(dirname "$GRANTS")/.pg.XXXXXX")
    jq --arg scope "$scope" --arg path "$path" --arg mode "$mode" \
        --argjson expires "$expires" --arg note "$note" '
        .grants |= (
            map(select((.expires == null or .expires >= now)
                       and (.scope != $scope or .path != $path)))
            + [{scope: $scope, path: $path, mode: $mode, expires: $expires}
               + (if $note != "" then {note: $note} else {} end)]
        )' "$GRANTS" > "$tmp" && mv "$tmp" "$GRANTS"
    echo "granted: scope=$scope path=$path mode=$mode expires=${ttl:-never}"
}

cmd_revoke() {
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
    path=$(real "$path") || die "cannot resolve $path (symlink loop or chain over $PG_MAX_HOPS links)"
    if [[ "$scope" != "*" ]]; then scope=$(real "$scope") || die "cannot resolve scope $scope"; fi

    [[ -f "$GRANTS" ]] || die "no grants file"
    local tmp before after
    before=$(jq '.grants | length' "$GRANTS")
    tmp=$(mktemp "$(dirname "$GRANTS")/.pg.XXXXXX") # same-dir: atomic rename
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
    jq -r '
        .grants[]
        | [ .scope, .path, .mode,
            (if .expires == null then "never"
             elif .expires < now then "EXPIRED"
             else ((.expires - now) / 60 | floor | tostring) + "m left" end),
            (.note // "") ]
        | @tsv' "$GRANTS" \
        | column -t -s $'\t' || echo "no grants"
}

cmd_check() {
    local path="" needed="" project="$PWD" sid=""
    while (($# > 0)); do
        case "$1" in
            --project)
                project="$2"
                shift
                ;;
            --session)
                sid="$2"
                shift
                ;;
            -*) die "unknown flag $1" ;;
            *)
                if [[ -z "$path" ]]; then path="$1"; elif [[ -z "$needed" ]]; then needed="$1"; else die "unexpected arg $1"; fi
                ;;
        esac
        shift
    done
    [[ -n "$path" && "$needed" =~ ^(r|w)$ ]] || die "usage: check <path> <r|w> [--project DIR] [--session SID]"
    local L R rc=0
    project=$(real "$project") || project=""
    L=$(lex "$path")
    R=$(real "$path") || R=""
    allowed "$L" "$R" "$needed" "$project" "$sid" || rc=$?
    case "$rc" in
        0) echo "allow: $needed on $R (project $project)" ;;
        2)
            echo "deny: $needed — $(deny_reason "$R" "$L")"
            exit 2
            ;;
        *)
            echo "ask: $needed on ${R:-$L} (project $project) — no covering grant"
            exit 1
            ;;
    esac
}

# --- dispatch ---------------------------------------------------------------

if (($# == 0)); then
    hook_mode
fi

cmd="$1"
shift
case "$cmd" in
    record) cmd_record ;;
    grant) cmd_grant "$@" ;;
    revoke) cmd_revoke "$@" ;;
    list) cmd_list ;;
    check) cmd_check "$@" ;;
    *) die "unknown command '$cmd' (record | grant | revoke | list | check, or no args for hook mode)" ;;
esac
