"""Decision tables for the night shim (v5).

The shim is the credential injection point: the ONE place the night
system inspects a direct command (zero string parsing anywhere else; see
the night-gate header). Two seams drive the tables:

* NIGHT_SHIM_CHECK_ONLY=1 — run the argv checks (gh), execute nothing.
* NIGHT_SHIM_EXEC=direct — run the FULL pipeline without credential
  injection. git push verdicts ride this: the shim dry-runs against a
  real (local, fixture) remote and judges the ref-update objects the
  remote reports — `+` rewrite, `-` delete, resolved destination ref —
  never argv spellings (YUZ-87). Allow-verdicts really push; the
  assertions read the remote's refs afterwards.
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


# --- gh: argv checks at the injection point --------------------------------

DENY = [
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
    ["git", "fetch", "origin"],
    ["git", "commit", "-m", "wip --force"],  # data — no argv rescan exists to trip on it
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


# --- git push: the ref-update judgment table (YUZ-87) ----------------------


class PushRig:
    """A work repo with a bare 'origin', pushed at through the shim.

    Git runs against a scratch HOME (no user/system config: no signing,
    no insteadOf, no aliases), so the shim's verdict is judged on exactly
    the fixture's state. Standard state: remote <default> and claude/x
    exist; local <default> and claude/x are one commit ahead (fast-
    forwardable); claude/new is local-only.
    """

    def __init__(self, tmp_path: Path, default_branch: str = "main") -> None:
        self.default_branch = default_branch
        home = tmp_path / "home"
        home.mkdir()
        self.env = {
            "PATH": os.environ["PATH"],
            "HOME": str(home),
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_AUTHOR_NAME": "night-test",
            "GIT_AUTHOR_EMAIL": "night@test",
            "GIT_COMMITTER_NAME": "night-test",
            "GIT_COMMITTER_EMAIL": "night@test",
        }
        self.remote = tmp_path / "remote.git"
        self.work = tmp_path / "work"
        self.git("init", "--bare", "-b", default_branch, str(self.remote))
        self.git("init", "-b", default_branch, str(self.work))
        self.commit("c1")
        self.git("-C", str(self.work), "remote", "add", "origin", str(self.remote))
        self.git("-C", str(self.work), "push", "-q", "origin", default_branch)
        self.git("-C", str(self.work), "branch", "claude/x")
        self.git("-C", str(self.work), "switch", "-q", "claude/x")
        self.commit("c2x")
        self.git("-C", str(self.work), "push", "-q", "origin", "claude/x")
        self.commit("c3x")  # local claude/x one ahead: fast-forwardable
        self.git("-C", str(self.work), "switch", "-q", default_branch)
        self.commit("c2m")  # local default one ahead: fast-forwardable
        self.git("-C", str(self.work), "branch", "claude/new")

    def git(self, *args: str) -> str:
        proc = subprocess.run(
            ["git", *args], env=self.env, capture_output=True, text=True, timeout=15
        )
        assert proc.returncode == 0, proc.stderr
        return proc.stdout.strip()

    def commit(self, msg: str) -> None:
        self.git("-C", str(self.work), "commit", "-q", "--allow-empty", "-m", msg)

    def diverge_claude_x(self) -> None:
        """Rewrite local claude/x so pushing it is a non-fast-forward."""
        self.git("-C", str(self.work), "switch", "-q", "claude/x")
        # local sits at c3x = remote c2x + 1; drop BELOW the remote head
        # and commit anew so the histories genuinely fork
        self.git("-C", str(self.work), "reset", "-q", "--hard", "HEAD~2")
        self.commit("c2x-rewritten")

    def remote_sha(self, ref: str) -> str | None:
        out = self.git("-C", str(self.remote), "for-each-ref", "--format=%(objectname)", ref)
        return out or None

    def local_sha(self, ref: str) -> str:
        return self.git("-C", str(self.work), "rev-parse", ref)

    def push(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["bash", str(SHIM), "git", "-C", str(self.work), "push", *args],
            env={**self.env, "NIGHT_SHIM_EXEC": "direct"},
            capture_output=True,
            text=True,
            timeout=30,
        )


@pytest.fixture
def rig(tmp_path: Path) -> PushRig:
    return PushRig(tmp_path)


@pytest.fixture
def trunk_rig(tmp_path: Path) -> PushRig:
    return PushRig(tmp_path, default_branch="trunk")


def assert_denied(proc: subprocess.CompletedProcess, tier: str) -> None:
    assert proc.returncode == 3, (proc.stdout, proc.stderr)
    assert f"night: {tier}:" in proc.stderr


# -- denials: rewrites -------------------------------------------------------


def test_force_that_would_rewrite_is_denied(rig: PushRig) -> None:
    rig.diverge_claude_x()
    before = rig.remote_sha("refs/heads/claude/x")
    proc = rig.push("--force", "origin", "claude/x")
    assert_denied(proc, "T3")
    assert "rewrite" in proc.stderr
    assert rig.remote_sha("refs/heads/claude/x") == before


def test_clustered_short_force_is_denied(rig: PushRig) -> None:
    rig.diverge_claude_x()
    assert_denied(rig.push("-fu", "origin", "claude/x"), "T3")


def test_plus_refspec_force_is_denied(rig: PushRig) -> None:
    rig.diverge_claude_x()
    assert_denied(rig.push("origin", "+refs/heads/claude/x:refs/heads/claude/x"), "T3")


def test_plus_refspec_on_default_branch_is_denied(rig: PushRig) -> None:
    # the ticket's argv verbatim: fast-forwardable here, so the T1
    # default-branch verdict lands; a diverged main would die earlier as T3
    assert_denied(rig.push("origin", "+refs/heads/main"), "T1")


# -- denials: deletions ------------------------------------------------------


def test_colon_refspec_delete_is_denied(rig: PushRig) -> None:
    proc = rig.push("origin", ":refs/heads/claude/x")
    assert_denied(proc, "T3")
    assert "delete" in proc.stderr
    assert rig.remote_sha("refs/heads/claude/x") is not None


def test_dash_d_delete_is_denied(rig: PushRig) -> None:
    assert_denied(rig.push("-d", "origin", "claude/x"), "T3")


def test_prune_delete_is_denied(rig: PushRig) -> None:
    # remote-only branch: --prune would remove it — a class no spelling
    # table ever listed
    rig.git("-C", str(rig.work), "push", "-q", "origin", "claude/x:refs/heads/claude/old")
    proc = rig.push("--prune", "origin", "refs/heads/claude/*:refs/heads/claude/*")
    assert_denied(proc, "T3")
    assert rig.remote_sha("refs/heads/claude/old") is not None


# -- denials: default branch, resolved not spelled ---------------------------


def test_default_branch_fast_forward_is_denied(rig: PushRig) -> None:
    proc = rig.push("origin", "main")
    assert_denied(proc, "T1")
    assert "default branch" in proc.stderr


def test_default_branch_via_refspec_is_denied(rig: PushRig) -> None:
    assert_denied(rig.push("origin", "claude/x:main"), "T1")


def test_push_all_containing_default_is_denied(rig: PushRig) -> None:
    assert_denied(rig.push("--all", "origin"), "T1")


def test_trunk_default_is_denied_without_hardcode(trunk_rig: PushRig) -> None:
    # the remote's HEAD names trunk: the verdict comes from ls-remote
    # --symref, not from any main|master spelling
    proc = trunk_rig.push("origin", "trunk")
    assert_denied(proc, "T1")
    assert trunk_rig.remote_sha("refs/heads/trunk") != trunk_rig.local_sha("trunk")


def test_main_name_belt_holds_on_foreign_default(trunk_rig: PushRig) -> None:
    # default is trunk, but creating refs/heads/main is still refused: the
    # belt judges the resolved destination name
    assert_denied(trunk_rig.push("origin", "claude/x:main"), "T1")


# -- allows: the verdict is on effects, and allowed pushes really land -------


def test_fast_forward_claude_branch_pushes(rig: PushRig) -> None:
    proc = rig.push("origin", "claude/x")
    assert proc.returncode == 0, proc.stderr
    assert rig.remote_sha("refs/heads/claude/x") == rig.local_sha("claude/x")


def test_destination_decides_source_name_is_data(rig: PushRig) -> None:
    proc = rig.push("origin", "main:claude/backup")
    assert proc.returncode == 0, proc.stderr
    assert rig.remote_sha("refs/heads/claude/backup") == rig.local_sha("main")


def test_new_branch_pushes(rig: PushRig) -> None:
    proc = rig.push("origin", "claude/new")
    assert proc.returncode == 0, proc.stderr
    assert rig.remote_sha("refs/heads/claude/new") == rig.local_sha("claude/new")


def test_force_spelling_without_rewrite_is_allowed(rig: PushRig) -> None:
    # --force on a fast-forward rewrites nothing: the model judges the
    # ref-update object, not the vocabulary
    proc = rig.push("--force", "origin", "claude/x")
    assert proc.returncode == 0, proc.stderr
    assert rig.remote_sha("refs/heads/claude/x") == rig.local_sha("claude/x")


def test_upstream_default_push_is_judged(rig: PushRig) -> None:
    # bare `git push` carries no refspec in argv at all — only ref-update
    # modeling sees where it goes
    rig.git("-C", str(rig.work), "switch", "-q", "claude/x")
    rig.git("-C", str(rig.work), "branch", "-q", "--set-upstream-to=origin/claude/x")
    proc = rig.push()
    assert proc.returncode == 0, proc.stderr
    assert rig.remote_sha("refs/heads/claude/x") == rig.local_sha("claude/x")


def test_up_to_date_push_is_allowed(rig: PushRig) -> None:
    assert rig.push("origin", "claude/x").returncode == 0
    assert rig.push("origin", "claude/x").returncode == 0


def test_push_to_empty_remote_is_allowed(rig: PushRig, tmp_path: Path) -> None:
    # no symref on an empty remote: nothing exists there to protect, and
    # the name belt still guards main/master
    empty = tmp_path / "empty.git"
    rig.git("init", "--bare", "-b", "main", str(empty))
    proc = rig.push(str(empty), "claude/x")
    assert proc.returncode == 0, proc.stderr


# -- the remote's own refusal is relayed, not translated ---------------------


def test_remote_rejection_relayed_not_denied(rig: PushRig) -> None:
    rig.diverge_claude_x()
    proc = rig.push("origin", "claude/x")
    assert proc.returncode == 1  # git's own exit, not the shim's 3
    assert "night:" not in proc.stderr
    assert "[rejected]" in proc.stdout or "non-fast-forward" in proc.stderr


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
