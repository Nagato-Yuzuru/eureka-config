#!/usr/bin/env bash
# night-gate v4 — authority gate + night-mode operations for Claude Code.
#
# Night mode's entire state is one byte: ~/.claude/night/active.json exists
# or it doesn't. Three channels enforce it, all driven by that flag:
#
#   hook (this file; permanently registered, inert without the flag) — the
#     AUTHORITY layer: outbound speech, irreversibles, connectors, browser
#     writes, and the authorization surface itself;
#   ~/.zshenv sourcing night/env.sh — the HYGIENE layer (signing off, gh
#     isolation, empty ssh agent, headless fnox), hot for every Claude shell
#     and transitive to every descendant process;
#   fnox night profile via bin/night — the CREDENTIAL layer: what is declared
#     there is the complete night capability surface.
#
# Threat model: DRIFT (a confused-but-compliant agent), not an adversary —
# command strings can always smuggle past a parser, so this gate never
# pretends to be that boundary. The real teeth are capability starvation
# (the layers above) and server-side rulesets. The gate's duties, in order:
#   1. never false-deny legitimate T0 work;
#   2. reroute shapes that would hang on the locked 1Password agent;
#   3. give a predictable, honest nudge on obvious T3 / authorization moves.
# A miss is acceptable; lying or being unpredictable is not.
#
# The Bash rule model: the command string is split into segments (; && || |
# newlines — quote- and heredoc-aware, so arguments and heredoc bodies are
# never mistaken for programs), each segment is stripped of prefixes (VAR=x
# assignments, env/command/sudo, shell keywords, backslash escapes), and the
# BASENAME OF THE PROGRAM POSITION is looked up in one rule table. Wrapped
# strings (sh -c, eval, substitutions, variables) are deliberately not
# chased — known leaks under the drift model. Decisions are pure bash + jq:
# no external tool's absence can fail open.
#
# Modes:
#   (no args)      PreToolUse gate: inert unless the flag exists
#   on [--mode full|local-only] [--scope csv]
#                  flip the flag (owner's call; /night on orchestrates
#                  preflight + lease mint + scope confirm first)
#   off            end the night
#   status         flag / mode / scope / queue at a glance
#   preflight      three-verdict: exit 0 PASS / 2 DEGRADE (local-only) / 1 FAIL
#   session-start  SessionStart context while the flag is on

set -euo pipefail

NIGHT_DIR="${NIGHT_DIR:-$HOME/.claude/night}"
ACTIVE="$NIGHT_DIR/active.json"
QUEUE_DIR="$NIGHT_DIR/queue"
NIGHT_SHIM="$NIGHT_DIR/bin/night"

# presence-only flag: manual on/off, no time semantics. A corrupt flag file
# is still a file: fail-closed.
is_active() { [[ -e "$ACTIVE" ]]; }

# --- hook mode ---------------------------------------------------------------

# Fail-closed backstop: a crash mid-decision must deny, not ask — at night
# nobody answers an ask, and a deny lets the agent park and move on.
HOOK_DECIDED=0
hook_exit_backstop() {
    if [[ "${HOOK_DECIDED:-0}" != 1 ]]; then
        printf '%s\n' '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":"night-gate crashed before reaching a decision; failing closed. Park this action, note it in the shift report, continue with the next task."}}'
    fi
    exit 0
}

emit_deny() { # REASON
    jq -n --arg r "$1" '{
        hookSpecificOutput: {
            hookEventName: "PreToolUse",
            permissionDecision: "deny",
            permissionDecisionReason: $r
        }
    }'
    HOOK_DECIDED=1
    exit 0
}

allow() {
    HOOK_DECIDED=1
    exit 0
}

# ---- Bash command model -----------------------------------------------------

declare -a NF=() # collect_nonflags output
collect_nonflags() {
    NF=()
    local a
    for a in "$@"; do
        if [[ "$a" != -* ]]; then NF+=("$a"); fi
    done
}

# scan_paren/scan_backtick: find the end of a $()/`` substitution so segment
# splitting stays correct (a ';' inside it is not a boundary). The body is
# kept verbatim in the word, never classified — see the drift note above.
SUB_END=0
scan_paren() { # STRING INDEX-AFTER-OPENER
    local s="$1" i="$2" pdepth=1 c
    local n=${#s}
    while ((i < n)); do
        c="${s:i:1}"
        case "$c" in
            \\) i=$((i + 2)); continue ;;
            \')
                i=$((i + 1))
                while ((i < n)) && [[ "${s:i:1}" != \' ]]; do i=$((i + 1)); done
                ;;
            \")
                i=$((i + 1))
                while ((i < n)); do
                    c="${s:i:1}"
                    if [[ "$c" == "\\" ]]; then
                        i=$((i + 2))
                    elif [[ "$c" == '"' ]]; then
                        break
                    else
                        i=$((i + 1))
                    fi
                done
                ;;
            '(') pdepth=$((pdepth + 1)) ;;
            ')')
                pdepth=$((pdepth - 1))
                if ((pdepth == 0)); then break; fi
                ;;
        esac
        i=$((i + 1))
    done
    SUB_END=$((i + 1))
}

scan_backtick() { # STRING INDEX-AFTER-OPENER
    local s="$1" i="$2" c
    local n=${#s}
    while ((i < n)); do
        c="${s:i:1}"
        if [[ "$c" == "\\" ]]; then
            i=$((i + 2))
        elif [[ "$c" == '`' ]]; then
            break
        else
            i=$((i + 1))
        fi
    done
    SUB_END=$((i + 1))
}

# lex_command helpers — mutate lex_command's locals via dynamic scope.
finish_word() {
    if ((has_cur)); then
        if ((skip_target)); then
            skip_target=0 # redirection target: a filename, not an argument
        else
            words+=("$cur")
        fi
    fi
    cur=''
    has_cur=0
}

end_segment() {
    finish_word
    skip_target=0
    if ((${#words[@]})); then
        gate_segment "${words[@]}"
        words=()
    fi
}

lex_command() { # STRING — split into segments, gate each
    local s="$1"
    local n=${#s} i=0 c
    local cur='' has_cur=0 skip_target=0
    local -a words=() heredocs=()

    while ((i < n)); do
        c="${s:i:1}"
        case "$c" in
            \')
                i=$((i + 1))
                while ((i < n)) && [[ "${s:i:1}" != \' ]]; do
                    cur+="${s:i:1}"
                    i=$((i + 1))
                done
                i=$((i + 1))
                has_cur=1
                ;;
            \")
                i=$((i + 1))
                while ((i < n)); do
                    c="${s:i:1}"
                    if [[ "$c" == "\\" ]]; then
                        cur+="${s:i + 1:1}"
                        i=$((i + 2))
                    elif [[ "$c" == '"' ]]; then
                        i=$((i + 1))
                        break
                    elif [[ "$c" == '$' && "${s:i + 1:1}" == '(' ]]; then
                        scan_paren "$s" $((i + 2))
                        cur+="${s:i:SUB_END - i}"
                        i=$SUB_END
                    elif [[ "$c" == '`' ]]; then
                        scan_backtick "$s" $((i + 1))
                        cur+="${s:i:SUB_END - i}"
                        i=$SUB_END
                    else
                        cur+="$c"
                        i=$((i + 1))
                    fi
                done
                has_cur=1
                ;;
            "\\")
                # escape: next char literal (this is how \gh sheds its alias)
                if ((i + 1 < n)); then
                    if [[ "${s:i + 1:1}" != $'\n' ]]; then
                        cur+="${s:i + 1:1}"
                        has_cur=1
                    fi
                    i=$((i + 2))
                else
                    cur+="\\"
                    has_cur=1
                    i=$((i + 1))
                fi
                ;;
            '$')
                if [[ "${s:i + 1:1}" == '(' ]]; then
                    scan_paren "$s" $((i + 2))
                    cur+="${s:i:SUB_END - i}"
                    has_cur=1
                    i=$SUB_END
                else
                    cur+='$'
                    has_cur=1
                    i=$((i + 1))
                fi
                ;;
            '`')
                scan_backtick "$s" $((i + 1))
                cur+="${s:i:SUB_END - i}"
                has_cur=1
                i=$SUB_END
                ;;
            ';')
                i=$((i + 1))
                while ((i < n)) && [[ "${s:i:1}" == ';' || "${s:i:1}" == '&' ]]; do
                    i=$((i + 1)) # ;; ;& ;;& — same boundary either way
                done
                end_segment
                ;;
            '&')
                if [[ "${s:i + 1:1}" == '>' ]]; then
                    finish_word # &> / &>> redirection
                    i=$((i + 2))
                    if [[ "${s:i:1}" == '>' ]]; then i=$((i + 1)); fi
                    skip_target=1
                else
                    i=$((i + 1))
                    if [[ "${s:i:1}" == '&' ]]; then i=$((i + 1)); fi
                    end_segment
                fi
                ;;
            '|')
                i=$((i + 1))
                if [[ "${s:i:1}" == '|' || "${s:i:1}" == '&' ]]; then i=$((i + 1)); fi
                end_segment
                ;;
            '(' | ')')
                end_segment # subshell / function-def grouping
                i=$((i + 1))
                ;;
            '<')
                if [[ "${s:i + 1:1}" == '(' ]]; then
                    scan_paren "$s" $((i + 2))
                    cur+="${s:i:SUB_END - i}"
                    has_cur=1
                    i=$SUB_END
                elif [[ "${s:i + 1:2}" == '<<' ]]; then
                    finish_word # <<< herestring: operator + one data word
                    i=$((i + 3))
                    skip_target=1
                elif [[ "${s:i + 1:1}" == '<' ]]; then
                    # heredoc: parse the delimiter; the body is DATA — skipped
                    # wholesale at the newline, never matched against rules
                    finish_word
                    i=$((i + 2))
                    if [[ "${s:i:1}" == '-' ]]; then i=$((i + 1)); fi
                    while ((i < n)) && [[ "${s:i:1}" == ' ' || "${s:i:1}" == $'\t' ]]; do
                        i=$((i + 1))
                    done
                    local delim=''
                    while ((i < n)); do
                        c="${s:i:1}"
                        case "$c" in
                            \')
                                i=$((i + 1))
                                while ((i < n)) && [[ "${s:i:1}" != \' ]]; do
                                    delim+="${s:i:1}"
                                    i=$((i + 1))
                                done
                                i=$((i + 1))
                                ;;
                            \")
                                i=$((i + 1))
                                while ((i < n)) && [[ "${s:i:1}" != \" ]]; do
                                    delim+="${s:i:1}"
                                    i=$((i + 1))
                                done
                                i=$((i + 1))
                                ;;
                            "\\")
                                delim+="${s:i + 1:1}"
                                i=$((i + 2))
                                ;;
                            ' ' | $'\t' | $'\n' | ';' | '&' | '|' | '<' | '>' | '(' | ')') break ;;
                            *)
                                delim+="$c"
                                i=$((i + 1))
                                ;;
                        esac
                    done
                    if [[ -n "$delim" ]]; then heredocs+=("$delim"); fi
                else
                    if ((has_cur)) && [[ "$cur" =~ ^[0-9]+$ ]]; then
                        cur=''
                        has_cur=0 # fd number glued to the operator
                    fi
                    finish_word
                    i=$((i + 1))
                    if [[ "${s:i:1}" == '>' || "${s:i:1}" == '&' ]]; then i=$((i + 1)); fi
                    skip_target=1
                fi
                ;;
            '>')
                if [[ "${s:i + 1:1}" == '(' ]]; then
                    scan_paren "$s" $((i + 2))
                    cur+="${s:i:SUB_END - i}"
                    has_cur=1
                    i=$SUB_END
                else
                    if ((has_cur)) && [[ "$cur" =~ ^[0-9]+$ ]]; then
                        cur=''
                        has_cur=0 # fd number glued to the operator
                    fi
                    finish_word
                    i=$((i + 1))
                    if [[ "${s:i:1}" == '>' || "${s:i:1}" == '|' ]]; then i=$((i + 1)); fi
                    if [[ "${s:i:1}" == '&' ]]; then
                        i=$((i + 1))
                        if [[ "${s:i:1}" == [0-9-] ]]; then
                            while ((i < n)) && [[ "${s:i:1}" == [0-9-] ]]; do
                                i=$((i + 1)) # >&2 fd dup: no filename follows
                            done
                        else
                            skip_target=1
                        fi
                    else
                        skip_target=1
                    fi
                fi
                ;;
            '#')
                if ((has_cur)); then
                    cur+='#'
                    i=$((i + 1))
                else
                    while ((i < n)) && [[ "${s:i:1}" != $'\n' ]]; do
                        i=$((i + 1)) # comment
                    done
                fi
                ;;
            $'\n')
                i=$((i + 1))
                finish_word
                while ((${#heredocs[@]})); do
                    local d="${heredocs[0]}" line rest
                    heredocs=("${heredocs[@]:1}")
                    while ((i < n)); do
                        rest="${s:i}"
                        line="${rest%%$'\n'*}"
                        i=$((i + ${#line} + 1))
                        if ((i > n)); then i=$n; fi
                        if [[ "${line#"${line%%[!$'\t']*}"}" == "$d" ]]; then break; fi
                    done
                done
                end_segment
                ;;
            ' ' | $'\t')
                finish_word
                i=$((i + 1))
                ;;
            *)
                cur+="$c"
                has_cur=1
                i=$((i + 1))
                ;;
        esac
    done
    end_segment
}

# ---- tier rules: one segment = (program, argv) ------------------------------

gate_gh() { # ARGV after 'gh' — shim context (bare gh never gets here)
    local -a a=("$@")
    local x subcmd='' method='' fields=0
    while ((${#a[@]})); do
        x="${a[0]}"
        a=("${a[@]:1}")
        case "$x" in
            -X | --method)
                method="${a[0]:-}"
                a=("${a[@]:1}")
                ;;
            -X*) method="${x#-X}" ;;
            --method=*) method="${x#--method=}" ;;
            -f | -F | --field | --raw-field | --input)
                fields=1
                a=("${a[@]:1}")
                ;;
            -f* | -F* | --field=* | --raw-field=* | --input=*) fields=1 ;;
            -R | --repo | --hostname) a=("${a[@]:1}") ;;
            -*) : ;;
            *) if [[ -z "$subcmd" ]]; then subcmd="$x"; fi ;;
        esac
    done
    if [[ "$subcmd" == release ]]; then
        emit_deny "night T3: mutating GitHub state beyond pushes (releases live under the Contents permission). T2-shaped speech goes in the queue; T3 is a shift-report proposal."
    fi
    if [[ "$subcmd" == api ]]; then
        local m=""
        m=$(printf '%s' "$method" | tr '[:lower:]' '[:upper:]')
        case "$m" in
            POST | PUT | PATCH | DELETE)
                emit_deny "night T3: mutating GitHub API call ($m). T2-shaped speech goes in the queue; T3 is a shift-report proposal." ;;
        esac
        if ((fields)) && [[ "$m" != GET ]]; then
            emit_deny "night T3: gh api with body fields defaults to POST. For a read, make the method explicit: gh api -X GET ... -f key=value."
        fi
    fi
    return 0
}

gate_git() { # ARGV after 'git'
    local -a a=("$@")
    while ((${#a[@]})); do
        case "${a[0]}" in
            -C | -c | --git-dir | --work-tree | --namespace | --config-env) a=("${a[@]:2}") ;;
            --git-dir=* | --work-tree=* | --namespace=* | --config-env=* | -c*) a=("${a[@]:1}") ;;
            -*) a=("${a[@]:1}") ;;
            *) break ;;
        esac
    done
    if [[ "${a[0]:-}" != push ]]; then
        return 0
    fi
    # Only the unmistakable history-rewrite flags. Destination discipline
    # (claude/* only, no default branches) is the server-side ruleset's job —
    # a bare `git push` on a claude/* branch is legitimate T0.
    local x
    for x in "${a[@]:1}"; do
        case "$x" in
            --force | --force-with-lease* | -f | --delete | -d | --mirror)
                emit_deny "night T3: git push with '$x' rewrites or removes published refs. Push plain fast-forwards to claude/*; propose anything stronger in the shift report." ;;
        esac
    done
    return 0
}

gate_segment() { # WORD... — deny exits; return 0 = this segment is fine
    local -a w=("$@")
    local shim=0 t base verb a

    while ((${#w[@]})); do
        t="${w[0]}"

        # environment-assignment prefixes (VAR=x cmd). Redirecting the fnox
        # profile is an authorization-surface change wherever it appears.
        if [[ "$t" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
            if [[ "$t" == FNOX_PROFILE=* ]]; then
                emit_deny "night: direct fnox use is frozen — credentialed commands go through ~/.claude/night/bin/night <cmd>; secrets and grants change only while the owner is awake."
            fi
            w=("${w[@]:1}")
            continue
        fi

        # shell keywords in command position (`do gh ...` still reroutes)
        case "$t" in
            if | then | elif | else | fi | for | while | until | do | done | time | '!' | '{' | '}')
                w=("${w[@]:1}")
                continue
                ;;
        esac

        base="${t##*/}"
        case "$base" in
            # ---- transparent wrappers: strip, then re-classify ----------
            env)
                w=("${w[@]:1}")
                while ((${#w[@]})); do
                    case "${w[0]}" in
                        -u | -C | -S) w=("${w[@]:2}") ;;
                        -*) w=("${w[@]:1}") ;;
                        *) break ;;
                    esac
                done
                continue
                ;;
            command)
                if [[ "${w[1]:-}" == -v || "${w[1]:-}" == -V ]]; then
                    return 0 # lookup, not execution
                fi
                w=("${w[@]:1}")
                if [[ "${w[0]:-}" == -p ]]; then w=("${w[@]:1}"); fi
                continue
                ;;
            sudo)
                w=("${w[@]:1}")
                while ((${#w[@]})); do
                    case "${w[0]}" in
                        -u | -g | -h | -p) w=("${w[@]:2}") ;;
                        -*) w=("${w[@]:1}") ;;
                        *) break ;;
                    esac
                done
                continue
                ;;

            # ---- the sanctioned credential path -------------------------
            night)
                # the shim execs real binaries with tonight's lease; classify
                # what it runs, in shim context — shim-shaped T3 still denies
                shim=1
                w=("${w[@]:1}")
                continue
                ;;

            # ---- authorization surface: frozen while the owner sleeps ---
            fnox)
                emit_deny "night: direct fnox use is frozen — credentialed commands go through ~/.claude/night/bin/night <cmd>; secrets and grants change only while the owner is awake." ;;
            security)
                emit_deny "night T3: the keychain is off the night surface entirely — dump/unlock/export read it as surely as add/delete/find. Propose changes in the shift report." ;;
            path-gate.sh)
                collect_nonflags "${w[@]:1}"
                verb="${NF[0]:-}"
                if [[ "$verb" == grant || "$verb" == revoke ]]; then
                    emit_deny "night T3: the authorization surface (path grants) is frozen while the owner sleeps. Propose changes in the shift report."
                fi
                return 0
                ;;

            # ---- ssh-shaped: the locked 1Password agent hangs -----------
            ssh | scp | sftp)
                emit_deny "night: ssh goes through the locked 1Password agent (ssh_config IdentityAgent — env cannot override it) and would hang until morning. git already talks https via the hygiene env; anything else ssh-shaped: park it and record it in the shift report." ;;
            rsync)
                for a in "${w[@]:1}"; do
                    if [[ "$a" == -e || "$a" == --rsh || "$a" == --rsh=* || "$a" == rsync://* ]] \
                        || { [[ "$a" != -* && "$a" == *:* ]] && [[ "${a%%:*}" != */* ]]; }; then
                        emit_deny "night: remote rsync rides ssh through the locked 1Password agent and would hang until morning. Local rsync is fine; park the remote copy in the shift report."
                    fi
                done
                return 0
                ;;

            # ---- 1Password shell-plugin binaries ------------------------
            gh)
                if ((shim == 0)); then
                    emit_deny "night: bare gh runs through the locked 1Password plugin and hangs. Use ~/.claude/night/bin/night <same command> — real binary, tonight's lease. A 403 there is a capability the owner deliberately did not grant tonight: park it and record it."
                fi
                gate_gh "${w[@]:1}"
                return 0
                ;;
            hcloud)
                if ((shim == 0)); then
                    emit_deny "night: bare hcloud runs through the locked 1Password plugin and hangs. Use ~/.claude/night/bin/night <same command> — real binary, tonight's lease."
                fi
                return 0
                ;;
            op)
                if ((shim == 0)); then
                    emit_deny "night: interactive op hits the locked desktop app and hangs. Use ~/.claude/night/bin/night op <args> — answers as the night-vault service account; an item outside that vault is unavailable tonight: park it."
                fi
                return 0
                ;;

            # ---- T3 irreversibles / high-risk, by program+verb ----------
            git)
                gate_git "${w[@]:1}"
                return 0
                ;;
            npm | pnpm | yarn)
                collect_nonflags "${w[@]:1}"
                if [[ "${NF[0]:-}" == publish ]]; then
                    emit_deny "night T3: publishing a package is irreversible. Write it up as a proposal in the shift report; the owner runs it awake."
                fi
                return 0
                ;;
            cargo)
                collect_nonflags "${w[@]:1}"
                if [[ "${NF[0]:-}" == publish ]]; then
                    emit_deny "night T3: publishing a crate is irreversible. Write it up as a proposal in the shift report; the owner runs it awake."
                fi
                return 0
                ;;
            gem)
                collect_nonflags "${w[@]:1}"
                if [[ "${NF[0]:-}" == push ]]; then
                    emit_deny "night T3: publishing a gem is irreversible. Write it up as a proposal in the shift report; the owner runs it awake."
                fi
                return 0
                ;;
            docker)
                collect_nonflags "${w[@]:1}"
                verb="${NF[0]:-}"
                if [[ "$verb" == push ]]; then
                    emit_deny "night T3: pushing an image publishes it. Write it up as a proposal in the shift report; the owner runs it awake."
                fi
                if [[ "$verb" == buildx ]]; then
                    for a in "${w[@]:1}"; do
                        if [[ "$a" == --push ]]; then
                            emit_deny "night T3: buildx --push publishes the image. Build without --push; propose the push in the shift report."
                        fi
                    done
                fi
                return 0
                ;;
            terraform)
                collect_nonflags "${w[@]:1}"
                case "${NF[0]:-}" in
                    apply | destroy)
                        emit_deny "night T3: terraform ${NF[0]} mutates real infrastructure. Plan is fine; propose the apply in the shift report." ;;
                esac
                return 0
                ;;
            kubectl)
                collect_nonflags "${w[@]:1}"
                case "${NF[0]:-}" in
                    apply | delete | create | replace | patch | scale | edit)
                        emit_deny "night T3: kubectl ${NF[0]} mutates the cluster. Reads are fine; propose mutations in the shift report." ;;
                esac
                return 0
                ;;
            wrangler)
                collect_nonflags "${w[@]:1}"
                case "${NF[0]:-}" in
                    deploy | publish)
                        emit_deny "night T3: wrangler ${NF[0]} ships to production. Write it up as a proposal in the shift report." ;;
                    versions)
                        if [[ "${NF[1]:-}" == upload ]]; then
                            emit_deny "night T3: wrangler versions upload ships code to Cloudflare. Write it up as a proposal in the shift report."
                        fi
                        ;;
                esac
                return 0
                ;;
            flyctl | fly)
                collect_nonflags "${w[@]:1}"
                if [[ "${NF[0]:-}" == deploy ]]; then
                    emit_deny "night T3: flyctl deploy ships to production. Write it up as a proposal in the shift report."
                fi
                for a in "${w[@]:1}"; do
                    if [[ "$a" == destroy ]]; then
                        emit_deny "night T3: flyctl destroy is irreversible. Write it up as a proposal in the shift report."
                    fi
                done
                return 0
                ;;
            vercel)
                collect_nonflags "${w[@]:1}"
                if [[ "${NF[0]:-}" == deploy ]]; then
                    emit_deny "night T3: vercel deploy ships to production. Write it up as a proposal in the shift report."
                fi
                for a in "${w[@]:1}"; do
                    if [[ "$a" == --prod ]]; then
                        emit_deny "night T3: vercel --prod ships to production. Write it up as a proposal in the shift report."
                    fi
                done
                return 0
                ;;

            # ---- no nested CLI sessions ---------------------------------
            claude)
                emit_deny "night: no nested CLI sessions. Use the Agent tool (subagents inherit this gate) or park the task." ;;

            *) return 0 ;; # T0 — everything else stays free at night
        esac
    done
    return 0 # bare assignments / keywords only
}

gate_bash() { # CMD
    local LC_ALL=C # byte-indexed scanning, locale-independent
    lex_command "$1"
    allow
}

gate_mcp() { # TOOL_NAME
    local t="$1"
    case "$t" in
        mcp__linear-server__*)
            # Linear is the night task tracker — but allow-listed, not open:
            # reads plus private task state. save_issue/save_comment are T0
            # (Linear-internal notes, the skill's own queue/report mirror),
            # NOT the T2 "comment" of the tier table — that means public
            # GitHub PR/issue speech. Merging diffs, submitting reviews and
            # publishing issues act on the world: deny. Unlike Bash strings,
            # tool names cannot be respelled — this allow-list is a real
            # boundary, so it stays strict.
            case "${t#mcp__linear-server__}" in
                list_* | get_* | search_documentation | extract_images | save_issue | save_comment) allow ;;
                *)
                    emit_deny "night: Linear is allow-listed to reads + task state (list_*/get_*/save_issue/save_comment). '$t' merges, reviews or publishes — T2/T3 outbound: queue it or propose it in the shift report." ;;
            esac
            ;;
        mcp__claude_ai_*)
            emit_deny "night: claude.ai connectors are off the night surface entirely — reads included (unattended reading of mail/files is a prompt-injection channel). Night task tracking is Linear." ;;
        mcp__claude-in-chrome__computer | mcp__claude-in-chrome__javascript_tool | mcp__claude-in-chrome__form_input | mcp__claude-in-chrome__file_upload | mcp__claude-in-chrome__upload_image | mcp__claude-in-chrome__browser_batch | mcp__claude-in-chrome__shortcuts_execute)
            emit_deny "night T2: browser write/execute under logged-in sessions can post anywhere. Browsing is read-only at night (navigate, read_page, get_page_text, find, screenshots)." ;;
        mcp__plugin_playwright_playwright__browser_click | mcp__plugin_playwright_playwright__browser_type | mcp__plugin_playwright_playwright__browser_fill_form | mcp__plugin_playwright_playwright__browser_file_upload | mcp__plugin_playwright_playwright__browser_run_code_unsafe | mcp__plugin_playwright_playwright__browser_evaluate | mcp__plugin_playwright_playwright__browser_press_key | mcp__plugin_playwright_playwright__browser_drag | mcp__plugin_playwright_playwright__browser_drop | mcp__plugin_playwright_playwright__browser_select_option | mcp__plugin_playwright_playwright__browser_handle_dialog | mcp__plugin_playwright_playwright__browser_network_request)
            emit_deny "night T2: browser write/execute. Browsing is read-only at night." ;;
    esac
    allow
}

hook_mode() {
    trap hook_exit_backstop EXIT
    # NIGHT_GATE_FORCE can only ACTIVATE the gate (preflight live-fire),
    # never bypass it.
    if ! is_active && [[ "${NIGHT_GATE_FORCE:-}" != 1 ]]; then
        allow
    fi
    local input='' tool
    IFS= read -r -d '' input || : # read to EOF; no external cat
    tool=$(jq -r '.tool_name // ""' <<< "$input")
    case "$tool" in
        Bash)
            gate_bash "$(jq -r '.tool_input.command // ""' <<< "$input")"
            ;;
        mcp__*)
            gate_mcp "$tool"
            ;;
    esac
    allow
}

# --- session-start -----------------------------------------------------------

cmd_session_start() {
    is_active || exit 0
    local mode extra=""
    mode=$(jq -r '.mode // "full"' "$ACTIVE" 2> /dev/null || echo full)
    [[ "$mode" == "local-only" ]] && extra=" TONIGHT IS LOCAL-ONLY: the GitHub lease is absent, and fnox deliberately aborts on a dead lease — night gh, night git pushes AND night op are all unavailable. Commits stay local; park anything needing credentials."
    jq -n --arg extra "$extra" '{
        hookSpecificOutput: {
            hookEventName: "SessionStart",
            additionalContext: ("NIGHT MODE is on (machine-wide flag; ends only when the owner runs /night off). The owner is asleep: load the night-shift skill before any work. Hygiene is automatic in every shell (signing off, ssh dead, gh isolated); credentialed commands go through ~/.claude/night/bin/night <cmd>. A 403 or deny is a capability the owner deliberately did not grant tonight: park it and record it, never work around it." + $extra)
        }
    }'
    exit 0
}

# --- CLI ---------------------------------------------------------------------

die() {
    echo "night-gate: $*" >&2
    exit 3
}

cmd_on() {
    local mode="full" scope="[]"
    while (($#)); do
        case "$1" in
            --mode)
                [[ -n "${2-}" ]] || die "--mode needs a value (full|local-only)"
                case "$2" in
                    full | local-only) mode="$2" ;;
                    *) die "unknown --mode '$2' (full|local-only)" ;;
                esac
                shift 2
                ;;
            --scope)
                [[ -n "${2-}" ]] || die "--scope needs a comma-separated repo list"
                scope=$(printf '%s' "$2" | jq -R 'split(",") | map(select(length > 0))')
                shift 2
                ;;
            *) die "usage: on [--mode full|local-only] [--scope repo1,repo2]" ;;
        esac
    done
    local today ts
    today=$(date -u +%F)
    ts=$(date -u +%FT%TZ) # ISO in the flag: portable to read back anywhere
    mkdir -p "$NIGHT_DIR" "$QUEUE_DIR/$today" "$NIGHT_DIR/reports"
    jq -n --arg m "$mode" --argjson s "$scope" --arg ts "$ts" \
        '{since: $ts, mode: $m, scope: $s, confirmed_at: $ts}' > "$ACTIVE"
    echo "night mode ON ($mode) — every Claude session on this machine is now governed; ends only with 'off'"
    echo "queue: $QUEUE_DIR/$today/"
}

cmd_off() {
    command rm -f "$ACTIVE"
    echo "night mode OFF"
    local n
    n=$(find "$QUEUE_DIR" -name '*.md' -not -path '*/done/*' -not -path '*/rejected/*' 2> /dev/null | wc -l | tr -d ' ')
    ((n > 0)) && echo "queued entries awaiting review: $n — run /night review"
    exit 0
}

cmd_status() {
    if is_active; then
        local since mode
        since=$(jq -r '.since // empty' "$ACTIVE" 2> /dev/null || true)
        mode=$(jq -r '.mode // "full"' "$ACTIVE" 2> /dev/null || echo full)
        if [[ -n "$since" ]]; then
            echo "night mode: ON ($mode) since $since"
        else
            echo "night mode: ON ($mode)"
        fi
        echo "scope (arm-time snapshot, record not truth): $(jq -c '.scope // []' "$ACTIVE" 2> /dev/null || echo '[]')"
    else
        echo "night mode: off"
    fi
    echo "queue entries (all dates): $(find "$QUEUE_DIR" -name '*.md' 2> /dev/null | wc -l | tr -d ' ')"
}

# Three-verdict preflight. FAIL = the fabric is broken (gate, hygiene,
# keychain, shim) — do not start a shift. DEGRADE = only the GitHub lease is
# unavailable — a local-only night is possible. Verifies real channels and
# the enforcement machinery itself, never proxies.
cmd_preflight() {
    local fails=0 degraded=0
    ok() { echo "  ✓ $*"; }
    bad() {
        echo "  ✗ $*"
        fails=$((fails + 1))
    }
    soft() {
        echo "  ~ $*"
        degraded=1
    }

    echo "night preflight:"

    # the gate itself: registered with a matcher that covers both tool
    # classes, executable, and both decision paths fire on the REGISTERED
    # hook command (not this file — a stale deployment must show up here)
    local entry hookcmd='' matcher=''
    entry=$(jq -c '[.hooks.PreToolUse[]? | select([.hooks[]?.command // empty] | any(test("night-gate")))] | .[0] // empty' "$HOME/.claude/settings.json" 2> /dev/null || true)
    if [[ -n "$entry" ]]; then
        hookcmd=$(jq -r '[.hooks[].command] | .[0]' <<< "$entry")
        matcher=$(jq -r '.matcher // ""' <<< "$entry")
        if [[ -x "${hookcmd%% *}" ]]; then
            ok "gate registered and executable"
        else
            bad "night-gate registered but not executable ($hookcmd)"
        fi
        if [[ "Bash" =~ $matcher ]] && [[ "mcp__linear-server__get_issue" =~ $matcher ]]; then
            ok "matcher covers Bash and mcp__ tools"
        else
            bad "matcher '$matcher' does not cover both Bash and mcp__ tools"
        fi
    else
        bad "night-gate not registered in ~/.claude/settings.json"
    fi
    if [[ -n "$hookcmd" ]]; then
        local fire
        fire=$(printf '%s' '{"tool_name":"Bash","tool_input":{"command":"fnox get X"}}' \
            | NIGHT_DIR="$NIGHT_DIR" NIGHT_GATE_FORCE=1 bash -c "$hookcmd" 2> /dev/null || true)
        if jq -e '(.hookSpecificOutput.permissionDecision == "deny") and (.hookSpecificOutput.permissionDecisionReason | test("fnox"))' <<< "$fire" > /dev/null 2>&1; then
            ok "gate live-fire denies with the fnox rule (registered hook, forced on)"
        else
            bad "gate live-fire deny failed — the registered hook's deny path is broken"
        fi
        fire=$(printf '%s' '{"tool_name":"Bash","tool_input":{"command":"echo hi"}}' \
            | NIGHT_DIR="$NIGHT_DIR" NIGHT_GATE_FORCE=1 bash -c "$hookcmd" 2> /dev/null || true)
        if [[ -z "$fire" ]]; then
            ok "gate live-fire allows T0 (silent on 'echo hi')"
        else
            bad "gate live-fire allow path broken — T0 command produced output"
        fi
    fi

    # hygiene channel: the deployed env.sh, really sourced in a clean shell,
    # must set the real values — not just exist under the right filename
    if [[ -f "$HOME/.claude/night/env.sh" ]]; then ok "env.sh present"; else bad "env.sh missing"; fi
    if grep -q 'night/env.sh' "$HOME/.zshenv" 2> /dev/null; then ok "zshenv guard present"; else bad "zshenv guard missing from ~/.zshenv"; fi
    local hy
    # shellcheck disable=SC2016  # the zsh script must expand in zsh, not here
    hy=$(env -i HOME="$HOME" PATH="$PATH" CLAUDECODE=1 zsh -c \
        'source "$HOME/.claude/night/env.sh"; print -r -- "$GH_CONFIG_DIR|$FNOX_NON_INTERACTIVE|$SSH_AUTH_SOCK|$(git config --get commit.gpgsign)|$(git config --get tag.gpgsign)|$(git config --get core.sshCommand)"' 2> /dev/null || true)
    if [[ "$hy" == "$HOME/.claude/night/gh-null|1||false|false|/usr/bin/false" ]]; then
        ok "hygiene env sources clean: gh isolated, fnox headless, ssh agent dead, signing off, git-over-ssh killed"
    else
        bad "hygiene env content wrong when sourced (got '$hy')"
    fi
    # regression: on-disk private keys would let night ssh succeed as the owner
    if grep -lE -- '-----BEGIN .*PRIVATE KEY' "$HOME"/.ssh/* 2> /dev/null | grep -q .; then
        bad "private key files on disk in ~/.ssh — night ssh could succeed with the owner's identity"
    else
        ok "no on-disk ssh private keys"
    fi
    # file-tool writes to the queue/report dirs must not prompt at night
    if "$HOME/.claude/hooks/path-gate.sh" check "$NIGHT_DIR" w > /dev/null 2>&1; then
        ok "path-gate grants w on $NIGHT_DIR (queue + report writes)"
    else
        bad "path-gate denies w on $NIGHT_DIR — queue/report writes would prompt with nobody awake"
    fi

    # credential channel: real resolutions, not existence checks
    if [[ -x "$NIGHT_SHIM" ]]; then ok "night shim executable"; else bad "night shim missing"; fi
    if command -v fnox > /dev/null 2>&1; then
        ok "fnox present ($(fnox --version 2> /dev/null || echo '?'))"
    else
        bad "fnox not installed"
    fi
    # lease-independent on purpose: `fnox get` resolves the single secret, so
    # a dead GitHub lease cannot mask a healthy (or broken) 1Password channel
    local sa
    if sa=$(FNOX_NON_INTERACTIVE=1 fnox -c "$HOME/.config/fnox/config.toml" -P night get OP_SERVICE_ACCOUNT_TOKEN 2> /dev/null) \
        && OP_SERVICE_ACCOUNT_TOKEN="$sa" timeout 20 op vault list --format=json 2> /dev/null \
        | jq -e 'map(.name) == ["night"]' > /dev/null 2>&1; then
        ok "SA reaches exactly the night vault (keychain ACL + 1Password, end to end)"
    else
        bad "SA cannot list exactly the night vault (keychain ACL? dead token? op missing?)"
    fi

    # the shim itself must exec before the lease question is even asked —
    # a broken shim is FAIL (fabric), never mistaken for a missing lease
    local shim_ok=0
    if FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" true > /dev/null 2>&1; then
        ok "night shim execs end to end (fnox exec path)"
        shim_ok=1
    else
        bad "night shim cannot exec a trivial command — shim/fnox wiring broken (not a lease question)"
    fi

    # lease: absent => DEGRADE (local-only), not FAIL
    local login
    if ((shim_ok)) && login=$(FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api user -q .login 2> /dev/null); then
        ok "github lease alive (as $login)"
        echo "  scope — installation repositories (the enforced allowlist):"
        FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api user/installations --jq '.installations[].id' 2> /dev/null \
            | while read -r iid; do
                FNOX_NON_INTERACTIVE=1 "$NIGHT_SHIM" gh api "user/installations/$iid/repositories" --jq '.repositories[].full_name' 2> /dev/null | sed 's/^/    - /'
            done || true
    elif ((shim_ok)); then
        soft "github lease absent/expired — tonight would be LOCAL-ONLY (mint interactively: $NIGHT_SHIM gh api user)"
    fi

    echo
    if ((fails > 0)); then
        echo "preflight FAILED ($fails) — do not start the shift."
        exit 1
    fi
    if ((degraded)); then
        echo "preflight DEGRADED — local-only night available (pushes and gh unavailable)."
        exit 2
    fi
    echo "preflight PASS."
}

# --- dispatch ----------------------------------------------------------------

if (($# == 0)); then
    hook_mode
fi

cmd="$1"
shift
case "$cmd" in
    on) cmd_on "$@" ;;
    off) cmd_off ;;
    status) cmd_status ;;
    preflight) cmd_preflight ;;
    session-start) cmd_session_start ;;
    *) die "unknown command '$cmd' (on | off | status | preflight | session-start, or no args for hook mode)" ;;
esac
