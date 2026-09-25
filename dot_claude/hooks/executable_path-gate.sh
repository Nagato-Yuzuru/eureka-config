#!/usr/bin/env bash
# path-gate v2 — whitelist file-access gate for Claude Code.
#
# Modes:
#   (no args)  PreToolUse gate. Read/Grep/Glob need r; Edit/Write/NotebookEdit
#              need w. Decision chain (first match): unresolvable target
#              (w->deny, r->ask) -> stores not ensured (w->deny) -> store
#              carve-out (w->deny, r->grants only) -> /dev/null -> workspace
#              (project + permissions.additionalDirectories) -> /private/tmp ->
#              current project auto-memory -> structural whitelist (r-only) ->
#              session grants -> durable grants -> ask (+ pending marker).
#              Every step judges the RESOLVED path: the path Claude Code's
#              tools open (trimmed, '..' collapsed lexically — cc_path), then
#              followed through symlinks to where the kernel lands the write.
#              The carve-out compares by file identity, not spelling.
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

# Fail-ask backstop: any crash in gate mode would otherwise exit non-zero with
# no decision, which Claude Code treats as non-blocking — i.e. fail-OPEN. It is
# armed before anything else can die (an unset HOME, a missing jq). Static
# printf, no jq: it must work when jq itself is the failure.
HOOK_DECIDED=0
hook_exit_backstop() {
    if [[ "${HOOK_DECIDED:-0}" != 1 ]]; then
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"path-gate crashed before reaching a decision; failing closed to ask. Debug: run ~/.claude/hooks/path-gate.sh manually and check the stores for malformed entries."}}'
    fi
    exit 0
}
case "${1-}" in
    "") trap hook_exit_backstop EXIT ;;
    record) trap 'exit 0' EXIT ;; # record must never block a tool
esac

die() {
    echo "path-gate: $*" >&2
    exit 3
}

# Every store and whitelist root hangs off $HOME: without an absolute one
# nothing can be located, so no decision is possible.
[[ "${HOME:-}" == /* ]] || die "HOME must be set to an absolute path"

CWD="$PWD"

# --- path helpers -----------------------------------------------------------
#
# A path never passes through $(...): command substitution strips trailing
# newlines, and a name may end in one. Helpers return through globals.

# realpath_of / readlink_of PATH -> _PG_OUT, byte for byte: the sentinel keeps
# a newline that ends the name. realpath always appends one newline; macOS
# readlink appends one only when the target does not already end in one, so
# it runs with -n (BSD and GNU alike) and nothing is stripped.
# macOS: realpath is /bin, readlink /usr/bin.
_PG_OUT=""
realpath_of() {
    _PG_OUT=$(/bin/realpath "$1" && printf x) || return 1
    _PG_OUT="${_PG_OUT%x}"
    _PG_OUT="${_PG_OUT%$'\n'}"
}
readlink_of() {
    _PG_OUT=$(/usr/bin/readlink -n "$1" && printf x) || return 1
    _PG_OUT="${_PG_OUT%x}"
}

# norm ABSPATH [keep] -> _PG_P: empty and '.' segments dropped; '..' removes
# the segment before it (lexically), or stays when "keep" is given.
_PG_P=""
norm() {
    local path="$1/" keep="${2:-}" out="" seg
    while [[ -n "$path" ]]; do
        seg="${path%%/*}"
        path="${path#*/}"
        case "$seg" in
            '' | .) ;;
            ..) if [[ -n "$keep" ]]; then out+="/.."; else out="${out%/*}"; fi ;;
            *) out+="/$seg" ;;
        esac
    done
    _PG_P="${out:-/}"
}

# cli_path RAW -> _PG_IN: a path typed at the CLI (check, grant, revoke), as
# Claude Code's path helper hands it to the tool before normalizing: JS-trimmed
# (String.prototype.trim: ECMA-262 WhiteSpace + LineTerminator, the code points
# below), then ~ expanded.
# One jq pass counts the whitespace BYTES at each end (linear; bash string
# scans are quadratic here), and bash cuts them off: the path's own bytes never
# pass through jq, which would replace invalid UTF-8.
# Hook mode never uses this: the hook receives the tool's path after that
# helper ran (trim, THEN normalize), and normalizing can leave whitespace at
# the end ("/ws/b /." -> "/ws/b "), which the tool then opens as is.
_PG_IN=""
cli_path() {
    local LC_ALL=C s="$1" lead=0 trail=0
    read -r lead trail < <(jq -nr --arg p "$s" '
        [9, 10, 11, 12, 13, 32, 160, 5760, range(8192; 8203), 8232, 8233, 8239,
         8287, 12288, 65279] as $ws
        | ($p | explode) as $a | ($a | length) as $n
        | def kept: $a[.] as $c | $ws | index([$c]) | not;
        (first(range(0; $n) | select(kept)) // $n) as $i
        | (first(range($n - 1; $i - 1; -1) | select(kept)) // ($i - 1)) as $j
        | "\($a[:$i] | implode | utf8bytelength) \($a[$j + 1:] | implode | utf8bytelength)"') \
        || die "cannot trim $s (jq failed)"
    s="${s:$lead:$((${#s} - lead - trail))}"
    # shellcheck disable=SC2088
    [[ "$s" == "~" || "$s" == "~/"* ]] && s="$HOME${s#\~}"
    _PG_IN="$s"
}

# tool_path PATH -> _PG_P: the path the tool opens, from the one it was given.
# Claude Code normalizes with Node's path.normalize / path.resolve(cwd, ...):
# '..' is collapsed LEXICALLY before the kernel ever sees it. norm() is that
# normalization (idempotent), taken against CWD for a relative path.
tool_path() {
    local path="$1"
    [[ "$path" == /* ]] || path="${CWD:-/}/$path"
    norm "$path"
}

# under PATH ROOT — true if PATH is ROOT or inside it
under() {
    [[ -z "$2" ]] && return 1
    case "$1" in
        "$2" | "$2"/*) return 0 ;;
        *) return 1 ;;
    esac
}

# resolve (input: _PG_P, from tool_path) -> REAL = where the kernel lands a write
# to it; or status 1 with the reason in REAL_WHY. Create-through included:
# - Existing path: realpath.
# - Otherwise split at the deepest ancestor that exists or is a symlink (cur)
#   and the absent rest (suffix):
#   - cur a dangling symlink: follow it by hand and start over with its target
#     + suffix. The kernel follows it too, so a create through it — O_CREAT on
#     a final-component link, or a parent made along the way — lands there.
#     The target's own '..' is kept for the next round: the kernel applies it
#     physically, and realpath does the same for the part that exists.
#   - Otherwise realpath(cur) + suffix. A '..' left in suffix came from a link
#     target and follows an absent name, where the kernel fails the write;
#     collapsing it lexically only names a nearby path to judge.
# Following more than PG_MAX_HOPS links (a loop, or a chain longer than any
# kernel follows: Linux MAXSYMLINKS 40, macOS 32) is unresolvable: deciding on
# the last hop reached would judge a path the write never lands on.
PG_MAX_HOPS=40
REAL=""
REAL_WHY=""
resolve() {
    local p="$_PG_P" hops=0 cur suffix t
    REAL="" REAL_WHY=""
    while :; do
        if [[ -e "$p" ]]; then
            realpath_of "$p" || {
                REAL_WHY="realpath failed"
                return 1
            }
            REAL="$_PG_OUT"
            return 0
        fi
        cur="$p" suffix=""
        while [[ "$cur" != / && ! -e "$cur" && ! -L "$cur" ]]; do
            suffix="/${cur##*/}$suffix"
            cur="${cur%/*}"
            [[ -n "$cur" ]] || cur=/
        done
        if [[ -L "$cur" && ! -e "$cur" ]]; then
            if ((hops >= PG_MAX_HOPS)); then
                REAL_WHY="a symlink loop, or a chain longer than $PG_MAX_HOPS links"
                return 1
            fi
            hops=$((hops + 1))
            readlink_of "$cur" || {
                REAL_WHY="readlink failed"
                return 1
            }
            t="$_PG_OUT"
            [[ "$t" == /* ]] || t="${cur%/*}/$t"
            norm "$t$suffix" keep
            p="$_PG_P"
            continue
        fi
        t=""
        if [[ "$cur" != / ]]; then
            realpath_of "$cur" || {
                REAL_WHY="realpath failed"
                return 1
            }
            t="$_PG_OUT"
        fi
        norm "$t$suffix"
        REAL="$_PG_P"
        return 0
    done
}

# real PATH -> REAL (status 0) | status 1, REAL_WHY. The space every
# allow/deny comparison happens in. _PG_P keeps the tool's spelling.
real() {
    tool_path "$1"
    resolve
}

# Roots, resolved. ~/.claude and /tmp can sit behind symlinks (on macOS /tmp
# and /var are symlinks into /private; dotfiles setups link ~/.claude), so ask
# the filesystem instead of hardcoding which platform redirects what. An
# unresolvable root (a symlink loop) falls back to its unresolved spelling:
# nothing beneath it resolves either, so no allow step can match through it.
if real "$HOME/.claude"; then CLAUDE_R="$REAL"; else CLAUDE_R="$_PG_P"; fi
if real /tmp; then TMP_R="$REAL"; else TMP_R="$_PG_P"; fi

# claude_root REL -> ROOT: ~/.claude/REL resolved like any target. Without a
# symlink below CLAUDE_R that is CLAUDE_R/REL, spared a realpath.
ROOT=""
claude_root() {
    local rest="$1/" p="$CLAUDE_R"
    while [[ -n "$rest" ]]; do
        p+="/${rest%%/*}"
        rest="${rest#*/}"
        if [[ -L "$p" ]]; then
            if real "$HOME/.claude/$1"; then ROOT="$REAL"; else ROOT=""; fi
            return 0
        fi
    done
    ROOT="$CLAUDE_R/$1"
}

# The stores, spelled through $HOME. is_store() compares by identity, so the
# spelling only has to reach them.
GRANTS="$HOME/.claude/hooks/path-gate-grants.json"
SESSIONS_DIR="$HOME/.claude/hooks/path-gate-sessions"

# ensure_stores -> STORES_OK=1 once both stores exist. The hooks dir always
# exists in production (the gate lives there) and file tools cannot delete, so
# after this every spelling that reaches a store names an existing file.
# The grants file is written whole under a temp name, then hard-linked into
# place: a reader sees no file or a complete one, never an empty one, and ln
# fails rather than replace a file a concurrent gate linked first.
STORES_OK=0
ensure_stores() {
    local tmp
    if [[ ! -f "$GRANTS" || ! -d "$SESSIONS_DIR" ]]; then
        mkdir -p "$SESSIONS_DIR" 2> /dev/null || true
        if [[ ! -e "$GRANTS" && ! -L "$GRANTS" ]] \
            && tmp=$(mktemp "${GRANTS%/*}/.pg.XXXXXX" 2> /dev/null); then
            (printf '{"version":1,"grants":[]}\n' > "$tmp" && ln "$tmp" "$GRANTS") 2> /dev/null || true
            command rm -f "$tmp"
        fi
    fi
    if [[ -f "$GRANTS" && -d "$SESSIONS_DIR" ]]; then STORES_OK=1; else STORES_OK=0; fi
}

# is_store R -> true if a write to resolved path R touches a store: R is the
# grants file, or the sessions dir or inside it. -ef asks the filesystem (same
# device and inode), so every alias matches — case and Unicode-fold variants,
# firmlinks, a symlinked ~/.claude, hardlinks — and nothing else does. Sound
# while both stores exist: then no absent tail of R can name one.
is_store() {
    local p="$1"
    [[ "$p" -ef "$GRANTS" ]] && return 0
    while [[ -n "$p" ]]; do
        [[ "$p" -ef "$SESSIONS_DIR" ]] && return 0
        p="${p%/*}"
    done
    return 1
}

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

# allowed R NEEDED PROJECT SID -> 0 allow | 1 ask | 2 deny
# R="" means the target is unresolvable (see resolve); PROJECT="" matches
# nothing. The caller has run ensure_stores.
allowed() {
    local R="$1" needed="$2" project="$3" sid="$4" d
    # Unresolvable: where a write lands is unknown, so it may be a store ->
    # deny. A read cannot forge anything -> ask, never allow.
    if [[ -z "$R" ]]; then
        [[ "$needed" == w ]] && return 2
        return 1
    fi
    # Without both stores in place is_store() cannot see every alias of them,
    # so no write can be told apart from a store write.
    [[ "$needed" == w && "$STORES_OK" != 1 ]] && return 2
    # step 0: tool-owned stores
    if is_store "$R"; then
        [[ "$needed" == w ]] && return 2
        grant_covers "$R" "$needed" "$project" && return 0
        return 1
    fi
    [[ "$R" == /dev/null ]] && return 0
    under "$R" "$project" && return 0
    while IFS= read -r d; do
        [[ -n "$d" ]] || continue
        real "$d" || continue
        under "$R" "$REAL" && return 0
    done < <(addl_dirs "$project")
    under "$R" "$TMP_R" && return 0
    local slug
    slug=$(slug_of "$project")
    claude_root "projects/$slug/memory"
    under "$R" "$ROOT" && return 0
    if [[ "$needed" == r ]]; then
        for d in "projects/$slug" plugins skills agents commands rules output-styles; do
            claude_root "$d"
            under "$R" "$ROOT" && return 0
        done
        for d in CLAUDE.md keybindings.json; do
            claude_root "$d"
            [[ "$R" == "$ROOT" ]] && return 0
        done
    fi
    session_covers "$R" "$needed" "$sid" && return 0
    grant_covers "$R" "$needed" "$project" && return 0
    return 1
}

# decide PATH NEEDED PROJECT SID -> RC (0/1/2 as allowed), R (resolved, or ""
# when unresolvable), RAW (the unresolved spelling), WHY (unresolvable only).
RC=0 R="" RAW="" WHY="" PROJECT=""
decide() {
    local project=""
    real "$3" && project="$REAL"
    RAW="" WHY="" R=""
    if real "$1"; then R="$REAL"; else WHY="$REAL_WHY"; fi
    RAW="$_PG_P"
    RC=0
    allowed "$R" "$2" "$project" "$4" || RC=$?
    PROJECT="$project"
}

# deny_reason -> why the last decide() returned 2.
deny_reason() {
    if [[ -z "$R" ]]; then
        printf '%s\n' "$RAW cannot be resolved ($WHY), so the write could land on a tool-owned path-gate store."
    elif [[ "$STORES_OK" != 1 ]]; then
        printf '%s\n' "path-gate stores cannot be ensured ($GRANTS must be a file and $SESSIONS_DIR a directory), so no write can be told apart from a store write; every file-tool write is denied until they are fixed from a shell."
    else
        printf '%s\n' "$R is a tool-owned path-gate store. File tools may never write it; grants are issued only via ~/.claude/hooks/path-gate.sh grant."
    fi
}

# --- hook mode --------------------------------------------------------------

emit() { # ask|deny REASON
    jq -n --arg d "$1" --arg why "$2" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: $d,
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

# read_fields JSON -> TOOL FPATH HCWD SID PMODE, or status 1. NUL-separated so
# every byte of a path arrives (@tsv would escape \, tab and newline). A NUL
# inside a field (never part of a real path) shows up as a sixth field.
TOOL="" FPATH="" HCWD="" SID="" PMODE=""
read_fields() {
    local -a fields=()
    local x
    while IFS= read -r -d '' x; do fields+=("$x"); done < <(jq -j '
        .tool_name // "",
        (.tool_input.file_path // .tool_input.notebook_path // .tool_input.path // ""),
        .cwd // "", .session_id // "", .permission_mode // ""
        | (., "\u0000")' <<< "$1" 2> /dev/null)
    ((${#fields[@]} == 5)) || return 1
    TOOL="${fields[0]}" FPATH="${fields[1]}" HCWD="${fields[2]}" SID="${fields[3]}" PMODE="${fields[4]}"
}

hook_mode() { # the backstop trap is armed at the top of the script
    local input needed
    input=$(cat)
    read_fields "$input" || exit 1
    needed=$(needed_for "$TOOL")
    if [[ -z "$needed" || -z "$FPATH" ]]; then
        HOOK_DECIDED=1
        exit 0
    fi
    CWD="$HCWD"
    ensure_stores
    decide "$FPATH" "$needed" "${CLAUDE_PROJECT_DIR:-${HCWD:-/nonexistent}}" "$SID"
    case "$RC" in
        0) ;;
        2) emit deny "$(deny_reason)" ;;
        *)
            local access=read
            [[ "$needed" == w ]] && access="write"
            if [[ -z "$R" ]]; then
                # no pending for an unresolvable target: nothing to promote
                emit ask "$TOOL needs $access access to $RAW, which cannot be resolved ($WHY). Approve = this call only; nothing is recorded."
            else
                [[ "$PMODE" != "bypassPermissions" ]] && pending_add "$SID" "$R" "$needed"
                emit ask "$TOOL needs $access access to $R — outside the workspace and whitelist. Approve = allowed for the rest of this session (auto-recorded). Durable: ask Claude to run ~/.claude/hooks/path-gate.sh grant \"$R\" $needed [--ttl 7d] — it always confirms with you first."
            fi
            ;;
    esac
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
cmd_record() { # exit 0 on any failure: trapped at the top of the script
    local input sid
    input=$(cat)
    # pure-bash early exit: no jq unless a session file with content exists
    [[ "$input" =~ \"session_id\"[[:space:]]*:[[:space:]]*\"([A-Za-z0-9._-]+)\" ]] || exit 0
    sid="${BASH_REMATCH[1]}"
    local f="$SESSIONS_DIR/$sid.json"
    [[ -s "$f" ]] || exit 0

    read_fields "$input" || exit 0
    [[ "$PMODE" == "bypassPermissions" ]] && exit 0
    local needed
    needed=$(needed_for "$TOOL")
    [[ -n "$needed" && -n "$FPATH" ]] || exit 0

    CWD="$HCWD"
    ensure_stores
    decide "$FPATH" "$needed" "${CLAUDE_PROJECT_DIR:-${HCWD:-/nonexistent}}" "$sid"
    [[ -n "$R" && "$RC" == 1 ]] || exit 0 # unresolvable: never pending

    jq -e --arg p "$R" --arg m "$needed" \
        '.pending[]? | select(.path == $p and .mode == $m)' "$f" > /dev/null 2>&1 || exit 0

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

# valid_store FILE — a grants store jq can read: an object with a grants array.
# An empty file is not one: jq reads it as no input and would write nothing.
valid_store() {
    jq -e '(.grants | type) == "array"' "$1" > /dev/null 2>&1
}

# replace_store TMP — move TMP over the grants file only if it is a valid store.
replace_store() {
    if valid_store "$1"; then
        mv "$1" "$GRANTS"
    else
        command rm -f "$1"
        die "the edit produced no valid store; $GRANTS left untouched"
    fi
}

STORE_BROKEN="is empty or not a valid store; nothing was changed. Repair it from a shell: it must hold {\"version\":1,\"grants\":[...]}"

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

    cli_path "$path"
    real "$_PG_IN" || die "cannot resolve $path ($REAL_WHY)"
    path="$REAL"
    if [[ "$scope" != "*" ]]; then
        cli_path "$scope"
        real "$_PG_IN" || die "cannot resolve scope $scope ($REAL_WHY)"
        scope="$REAL"
    fi

    local expires=null
    if [[ -n "$ttl" ]]; then
        local secs
        secs=$(ttl_to_seconds "$ttl") || exit $? # die in a subshell exits only the subshell
        expires=$(jq -n --argjson ttl "$secs" '(now + $ttl)')
    fi

    ensure_stores
    [[ "$STORES_OK" == 1 ]] || die "cannot create the stores ($GRANTS must be a file, $SESSIONS_DIR a directory)"
    valid_store "$GRANTS" || die "$GRANTS $STORE_BROKEN"
    local tmp
    # tmp in the store's own dir: mv is then a same-filesystem rename (atomic);
    # $TMPDIR may be another volume, where mv degrades to copy+unlink.
    tmp=$(mktemp "${GRANTS%/*}/.pg.XXXXXX")
    jq --arg scope "$scope" --arg path "$path" --arg mode "$mode" \
        --argjson expires "$expires" --arg note "$note" '
        .grants |= (
            map(select((.expires == null or .expires >= now)
                       and (.scope != $scope or .path != $path)))
            + [{scope: $scope, path: $path, mode: $mode, expires: $expires}
               + (if $note != "" then {note: $note} else {} end)]
        )' "$GRANTS" > "$tmp" 2> /dev/null || true
    replace_store "$tmp"
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
    cli_path "$path"
    real "$_PG_IN" || die "cannot resolve $path ($REAL_WHY)"
    path="$REAL"
    if [[ "$scope" != "*" ]]; then
        cli_path "$scope"
        real "$_PG_IN" || die "cannot resolve scope $scope ($REAL_WHY)"
        scope="$REAL"
    fi

    [[ -f "$GRANTS" ]] || die "no grants file"
    valid_store "$GRANTS" || die "$GRANTS $STORE_BROKEN"
    local tmp before after
    before=$(jq '.grants | length' "$GRANTS")
    tmp=$(mktemp "${GRANTS%/*}/.pg.XXXXXX") # same-dir: atomic rename
    jq --arg scope "$scope" --arg path "$path" \
        '.grants |= map(select(.scope != $scope or .path != $path))' \
        "$GRANTS" > "$tmp" 2> /dev/null || true
    replace_store "$tmp"
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
    ensure_stores
    cli_path "$project"
    project="$_PG_IN"
    cli_path "$path"
    decide "$_PG_IN" "$needed" "$project" "$sid"
    case "$RC" in
        0) echo "allow: $needed on $R (project $PROJECT)" ;;
        2)
            echo "deny: $needed — $(deny_reason)"
            exit 2
            ;;
        *)
            echo "ask: $needed on ${R:-$RAW} (project $PROJECT) — no covering grant"
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
