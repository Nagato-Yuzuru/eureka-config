# shellcheck shell=sh
# Night hygiene environment — the single home for these facts.
# Sourced by ~/.zshenv for every Claude-spawned shell (CLAUDECODE=1) while
# ~/.claude/night/active.json exists: hot for all sessions, and transitive —
# every descendant process of every Bash command inherits it, which is what
# catches the ssh/signing paths buried inside builds and package managers.
# Credentials are NOT here (fnox night profile, per invocation via `night`).

export SSH_AUTH_SOCK=""
export GIT_TERMINAL_PROMPT=0
# Night is headless: never prompt for auth mid-shift — a dead lease must fail
# fast and classifiable, not poll a device-flow code nobody will enter.
# FNOX_NON_INTERACTIVE covers browser/device auth flows (FNOX_PROMPT_AUTH does
# not — verified against fnox source). The flag boundary separates this from
# arm time: /night on mints interactively BEFORE the flag takes effect.
export FNOX_NON_INTERACTIVE=1
# gh physically isolated from any day login state: with no config dir there
# is nothing to fall back to when the night lease is absent — fail fast.
export GH_CONFIG_DIR="$HOME/.claude/night/gh-null"
export GIT_CONFIG_COUNT=7
export GIT_CONFIG_KEY_0=commit.gpgsign GIT_CONFIG_VALUE_0=false
export GIT_CONFIG_KEY_1=url.https://github.com/.insteadOf GIT_CONFIG_VALUE_1=git@github.com:
export GIT_CONFIG_KEY_2=url.https://github.com/.insteadOf GIT_CONFIG_VALUE_2=ssh://git@github.com/
export GIT_CONFIG_KEY_3=credential.helper GIT_CONFIG_VALUE_3=
export GIT_CONFIG_KEY_4=credential.helper GIT_CONFIG_VALUE_4='!gh auth git-credential'
# The insteadOf pair above only covers github.com; user gitconfig may rewrite
# OTHER hosts https->ssh (url.<ssh>.insteadOf), and insteadOf longest-match
# cannot be neutralized per-host from here. Kill the whole class instead: any
# git that reaches ssh fails in milliseconds, not hanging on the locked
# 1Password agent until morning.
export GIT_CONFIG_KEY_5=core.sshCommand GIT_CONFIG_VALUE_5=/usr/bin/false
export GIT_CONFIG_KEY_6=tag.gpgsign GIT_CONFIG_VALUE_6=false
