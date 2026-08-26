"""Golden decision table for the night shim's argv checks (v5).

The shim is the credential injection point: the ONE place the night
system inspects a command, on real argv (zero string parsing anywhere
else; see the night-gate header). NIGHT_SHIM_CHECK_ONLY=1 is the
testability seam: run the checks, skip the fnox exec.
"""

from __future__ import annotations

import json
import os
import subprocess
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SHIM = REPO_ROOT / "dot_claude" / "night" / "bin" / "executable_night"
FNOX_CONFIG_TMPL = REPO_ROOT / "dot_config" / "fnox" / "config.toml.tmpl"


def render_fnox_config(os_: str, extra_data: dict | None = None) -> dict:
    """The fnox config exactly as chezmoi renders it for the given OS."""
    data: dict = {"chezmoi": {"os": os_, "arch": "arm64" if os_ == "darwin" else "amd64"}}
    if extra_data:
        data.update(extra_data)
    proc = subprocess.run(
        [
            "chezmoi", "execute-template", "--source", str(REPO_ROOT),
            "--override-data", json.dumps(data),
            "--file", str(FNOX_CONFIG_TMPL),
        ],
        text=True,
        capture_output=True,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    return tomllib.loads(proc.stdout)


def shim_verdict(argv: list[str]) -> str:
    proc = subprocess.run(
        ["bash", str(SHIM), *argv],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"], "NIGHT_SHIM_CHECK_ONLY": "1"},
        timeout=15,
    )
    if proc.returncode == 0:
        return "allow"
    assert proc.returncode == 3, proc.stderr
    return "deny"


DENY = [
    # history rewrite / ref removal: the Contents:write hole no server-side
    # ruleset closes on private-free repos
    ["git", "push", "--force", "origin", "HEAD:refs/heads/claude/x"],
    ["git", "push", "-f"],
    ["git", "push", "--force-with-lease", "origin", "claude/x"],
    ["git", "push", "--force-with-lease=refs/heads/claude/x", "origin", "claude/x"],
    ["git", "push", "--delete", "origin", "refs/tags/v1.0.0"],
    ["git", "push", "-d", "origin", "old-branch"],
    ["git", "push", "--mirror", "origin"],
    # explicit default-branch destination: never on a night task list
    ["git", "push", "origin", "main"],
    ["git", "push", "origin", "master"],
    ["git", "push", "origin", "HEAD:main"],
    ["git", "push", "origin", "HEAD:refs/heads/main"],
    ["git", "push", "origin", "claude/x:master"],
    ["git", "push", "origin", "refs/heads/main"],
    # git global flags and path-spelled programs still resolve
    ["git", "-C", "/tmp/x", "push", "--force"],
    ["/usr/bin/git", "push", "-f"],
    # gh: releases ride Contents:write, the one hole the App cannot close;
    # mutating api calls are T3
    ["gh", "release", "create", "v1.0.0"],
    ["gh", "release", "delete", "v1.0.0"],
    ["gh", "release", "edit", "v1.0.0", "--draft=false"],
    ["gh", "release", "upload", "v1.0.0", "dist.tgz"],
    ["gh", "-R", "o/r", "release", "create", "v2"],
    ["gh", "api", "-X", "POST", "repos/o/r/releases", "-f", "tag_name=v1"],
    ["gh", "api", "repos/o/r/contents/f", "-X", "PUT", "--field", "message=x"],
    ["gh", "api", "--method=DELETE", "repos/o/r/releases/1"],
    ["gh", "api", "repos/o/r/releases", "-f", "tag_name=v1"],  # fields => POST
]

ALLOW = [
    ["git", "push"],
    ["git", "push", "origin", "claude/probe"],
    ["git", "push", "origin", "main:claude/backup"],  # DESTINATION decides
    ["git", "push", "origin", "claude/fix-main"],
    ["git", "push", "--set-upstream", "origin", "claude/x"],
    ["git", "fetch", "origin"],
    ["git", "commit", "-m", "wip --force"],  # data, not push args
    # gh reads stay free, release READS included: checking latest versions
    # at night is legitimate T0
    ["gh", "release", "list"],
    ["gh", "release", "view", "v1.0.0"],
    ["gh", "release", "download", "v1.0.0"],
    ["gh", "api", "user", "-q", ".login"],
    ["gh", "api", "-X", "GET", "search/issues", "-f", "q=x"],
    ["gh", "pr", "list"],  # App has no PR perms; the server 403 is the tier
    ["op", "read", "op://Night/item/field"],
    ["echo", "--force"],  # non-git programs' flags are data
]


@pytest.mark.parametrize("argv", DENY, ids=lambda a: " ".join(a))
def test_shim_denies(argv: list[str]) -> None:
    assert shim_verdict(argv) == "deny"


@pytest.mark.parametrize("argv", ALLOW, ids=lambda a: " ".join(a))
def test_shim_allows(argv: list[str]) -> None:
    assert shim_verdict(argv) == "allow"


def test_shim_no_args_usage_error() -> None:
    proc = subprocess.run(
        ["bash", str(SHIM)],
        capture_output=True,
        text=True,
        env={"PATH": os.environ["PATH"], "HOME": os.environ["HOME"]},
        timeout=15,
    )
    assert proc.returncode == 3
    assert "usage" in proc.stderr


# --- the fnox config the shim pins ----------------------------------------


def test_fnox_night_profile_shape() -> None:
    # the load-bearing knobs of the night credential profile
    cfg = render_fnox_config("darwin")
    lease = cfg["profiles"]["night"]["leases"]["github"]
    assert lease["type"] == "github-oauth"
    assert lease["env_var"] == "GH_TOKEN"
    # discards the 184-day refresh token: "authorize while awake" rests here
    assert lease["keyring_cache"] is False
    secrets = cfg["profiles"]["night"]["secrets"]
    # credentials only, and the complete night set: nothing undeclared
    assert set(secrets) == {"OP_SERVICE_ACCOUNT_TOKEN"}
    assert secrets["OP_SERVICE_ACCOUNT_TOKEN"]["provider"] == "keychain"
    # the top-level [secrets] table merges into EVERY named profile, so it
    # must stay empty: a base secret silently joins the night surface
    assert "secrets" not in cfg
    # minting credentials ride the 1Password provider: locked app + op
    # sentinel make them mechanically unreachable at night, while keychain
    # items would stay readable after dark
    day = cfg["profiles"]["day"]["secrets"]
    assert day["LINEAR_BOT_CLIENT_SECRET"]["provider"] == "onepass"


def test_fnox_linux_custody_rides_age() -> None:
    # provisioned linux: the SA rides the committed age ciphertext and the
    # owner-placed key; no keychain provider exists to reach for
    cfg = render_fnox_config(
        "linux",
        {"night": {"age": {"recipient": "age1testrecipient", "sa_token_ciphertext": "QUJD"}}},
    )
    assert "keychain" not in cfg.get("providers", {})
    assert cfg["providers"]["age"]["recipients"] == ["age1testrecipient"]
    assert cfg["providers"]["age"]["key_file"] == "~/.config/fnox/age.key"
    sa = cfg["profiles"]["night"]["secrets"]["OP_SERVICE_ACCOUNT_TOKEN"]
    assert sa == {"provider": "age", "value": "QUJD"}
    assert "secrets" not in cfg


def test_fnox_linux_unprovisioned_declares_nothing() -> None:
    # before the wizard's custody stage: an absent credential fails fast at
    # preflight, never resolves to a silently wrong one
    cfg = render_fnox_config("linux")
    assert cfg["profiles"]["night"].get("secrets", {}) == {}
