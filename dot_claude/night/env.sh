# shellcheck shell=sh
# Night hygiene environment: the single home for these facts.
# Sourced by ~/.zshenv for every Claude-spawned shell (CLAUDECODE=1) while
# ~/.claude/night/active.json exists. Hot for all sessions: every Bash tool
# command is a fresh zsh that re-reads zshenv. Transitive: every descendant
# process inherits.
#
# Everything here is an ENV VAR on purpose: the Claude shell snapshot is
# sourced AFTER zshenv and re-exports PATH, so PATH edits die in tool
# shells; env vars survive and reach descendants, including day-born
# sessions whose snapshot still carries the op-plugin gh/hcloud aliases.
# Credentials are NOT here (fnox night profile, per invocation via `night`).

export SSH_AUTH_SOCK=""
export GIT_TERMINAL_PROMPT=0
# Inherited overrides reopen whole classes the vars below close: an env
# GIT_SSH_COMMAND/GIT_SSH outranks core.sshCommand=/usr/bin/false (git
# would exec the day ssh), and gh prefers an env GH_TOKEN/GITHUB_TOKEN
# over the isolated config dir. Clear them; `night gh` still works (fnox
# exec injects its token into the child, after this ran). (YUZ-89)
unset GIT_SSH_COMMAND GIT_SSH GH_TOKEN GITHUB_TOKEN
# Night is headless: a dead lease must fail fast and classifiable, never poll
# a device-flow code nobody will enter. FNOX_NON_INTERACTIVE covers device
# and browser auth (FNOX_PROMPT_AUTH does not), and also blocks fnox's own
# <5min-to-expiry auto-remint (a device flow) from firing mid-shift.
# /night on mints interactively BEFORE the flag falls.
export FNOX_NON_INTERACTIVE=1
# gh physically isolated from any day login state: with no config dir there
# is nothing to fall back to when the night lease is absent, so gh fails
# fast.
export GH_CONFIG_DIR="$HOME/.claude/night/gh-null"
# Sentinel: a deliberately malformed 1Password service-account token. ANY
# op invocation takes the SA code path and dies in ~50ms on token parse
# ("format is invalid") instead of hanging on the locked desktop app. That
# covers every route to op: bare calls, the day op-plugin gh/hcloud aliases
# baked into a day-born session's snapshot, and calls nested inside
# scripts. `night op ...` still works: fnox exec overrides this with the
# real SA token.
export OP_SERVICE_ACCOUNT_TOKEN="ops_NIGHT_SENTINEL_invalid_on_purpose_use_night_op"
# Git config as GIT_CONFIG_KEY_i/VALUE_i pairs with a DERIVED count: a
# hand-maintained GIT_CONFIG_COUNT drifts against the pairs — too low
# silently drops trailing entries (system values win), too high fatals
# every night git command. The builder makes misalignment unrepresentable.
# (YUZ-92)
_night_git_n=0
_night_git_cfg() {
    # shellcheck disable=SC2163  # dynamic names are the point of the builder
    export "GIT_CONFIG_KEY_${_night_git_n}=$1" "GIT_CONFIG_VALUE_${_night_git_n}=$2"
    _night_git_n=$((_night_git_n + 1))
}
_night_git_cfg commit.gpgsign false
_night_git_cfg url.https://github.com/.insteadOf git@github.com:
_night_git_cfg url.https://github.com/.insteadOf ssh://git@github.com/
_night_git_cfg credential.helper ""
_night_git_cfg credential.helper '!gh auth git-credential'
# The insteadOf pair above only covers github.com; user gitconfig may rewrite
# OTHER hosts https->ssh (url.<ssh>.insteadOf), and insteadOf longest-match
# cannot be neutralized per-host from here. Kill the whole class instead: any
# git that reaches ssh fails in milliseconds, not hanging on the locked
# 1Password agent until morning. (Bare ssh itself is handled in ssh_config:
# ~/.claude/night/ssh-night.conf, Include'd by the owner.)
_night_git_cfg core.sshCommand /usr/bin/false
_night_git_cfg tag.gpgsign false
export GIT_CONFIG_COUNT="$_night_git_n"
unset -f _night_git_cfg
unset _night_git_n
