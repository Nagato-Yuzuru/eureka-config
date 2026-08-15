#!/bin/bash
# The git-repo externals clone over SSH; a fresh machine has no github.com
# host key yet and a non-interactive clone would fail. Keys come from
# api.github.com/meta (HTTPS-authenticated), not from ssh-keyscan (TOFU).
# Best effort: if this cannot reach GitHub, the clone will fail with a clear
# message of its own — do not take the whole apply down here.
set -uo pipefail

if ssh-keygen -F github.com >/dev/null 2>&1; then
    exit 0
fi

keys="$(curl -fsSL https://api.github.com/meta |
    grep -oE '"(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa) [A-Za-z0-9+/=]+"' |
    tr -d '"' |
    sed 's/^/github.com /')" || keys=""

if [ -z "$keys" ]; then
    echo "warning: could not fetch github.com host keys; SSH clones may fail" >&2
    exit 0
fi

echo "Adding github.com host keys to ~/.ssh/known_hosts..."
mkdir -p ~/.ssh && chmod 700 ~/.ssh
printf '%s\n' "$keys" >>~/.ssh/known_hosts
