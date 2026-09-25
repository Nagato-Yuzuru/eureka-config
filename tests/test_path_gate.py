"""path-gate v2. Drives the hook scripts as subprocesses with a fake $HOME.

The gate's decision chain is tested through the `check` verb (exit codes:
0=allow, 1=ask, 2=deny). Hook mode (stdin JSON) parses its input differently,
so the resolve and store-identity cases run in both modes (BOTH_MODES); hook
mode and the record verb also have their own sections.

Two roots carry the whole suite, and neither hardcodes a platform:

- TMP_ROOT is the OS shared scratch the gate allows, spelled the way the
  filesystem spells it. pytest's own tmp_path is NOT usable as a foreign path,
  because it lands inside TMP_ROOT on Linux.
- SANDBOX is a repo-relative root the gate must call foreign. test_sandbox_is_foreign
  asserts that, so a platform where it stops holding fails once and legibly.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
GATE = REPO_ROOT / "dot_claude" / "hooks" / "executable_path-gate.sh"

# The gate must run on macOS's stock /bin/bash (3.2), not just whatever `env bash`
# finds first — on a Homebrew machine that is 5.x and would hide 3.2-only
# breakage. Where /bin/bash exists it is the interpreter; elsewhere, the shebang.
GATE_CMD = ["/bin/bash", str(GATE)] if Path("/bin/bash").exists() else [str(GATE)]

SID = "11111111-2222-3333-4444-555555555555"

# The gate's only fixed allowlist root. realpath, never a literal: the kernel
# spells it /private/tmp on macOS and /tmp on Linux.
TMP_ROOT = Path(os.path.realpath("/tmp"))

# Where under TMP_ROOT the suite creates things. Every assertion holds at any
# depth under the root, so prefer $TMPDIR when it resolves inside: Claude Code's
# Bash sandbox grants $TMPDIR but not bare TMP_ROOT.
_ENV_TMP = Path(os.path.realpath(os.environ.get("TMPDIR", "/tmp")))
TMP_BASE = _ENV_TMP if TMP_ROOT in _ENV_TMP.parents else TMP_ROOT

# Everything else the gate allows is derived from $HOME and --project, which the
# fixtures own, so any root outside TMP_ROOT is foreign. The repo qualifies.
SANDBOX = REPO_ROOT / ".pytest-sandbox"


def slug_of(path: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(path))


@pytest.fixture()
def sandbox():
    SANDBOX.mkdir(exist_ok=True)
    d = Path(tempfile.mkdtemp(dir=SANDBOX))
    yield d
    shutil.rmtree(d, ignore_errors=True)


@pytest.fixture()
def home(sandbox):
    h = sandbox / "home"
    (h / ".claude" / "hooks").mkdir(parents=True)
    return h


@pytest.fixture()
def project(sandbox):
    p = sandbox / "proj"
    p.mkdir()
    return p


def env_for(home: Path) -> dict[str, str]:
    e = dict(os.environ)
    e["HOME"] = str(home)
    e.pop("CLAUDE_PROJECT_DIR", None)
    return e


def check(home, path, mode, project, session=None) -> int:
    args = [*GATE_CMD, "check", str(path), mode, "--project", str(project)]
    if session:
        args += ["--session", session]
    return subprocess.run(args, env=env_for(home), capture_output=True).returncode


def cli(home, *args) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*GATE_CMD, *[str(a) for a in args]],
        env=env_for(home),
        capture_output=True,
        text=True,
    )


ALLOW, ASK, DENY = 0, 1, 2


def test_sandbox_is_foreign(home, project, sandbox):
    """Every foreign-path assertion below rests on this. If a platform ever puts
    the repo inside an allowlist root, fail here once instead of everywhere."""
    assert check(home, sandbox / "probe.txt", "r", project) == ASK


# --- decision chain via check ------------------------------------------------


class TestWorkspace:
    def test_project_file_allows_rw(self, home, project):
        assert check(home, project / "src" / "a.py", "r", project) == ALLOW
        assert check(home, project / "src" / "a.py", "w", project) == ALLOW

    def test_outside_project_asks(self, home, project):
        assert check(home, home / "elsewhere.txt", "r", project) == ASK
        assert check(home, home / "elsewhere.txt", "w", project) == ASK

    def test_dev_null_allows(self, home, project):
        assert check(home, "/dev/null", "w", project) == ALLOW

    def test_traversal_does_not_escape(self, home, project):
        assert check(home, project / ".." / "escape.txt", "r", project) == ASK

    def test_additional_directories_from_user_settings(self, home, project, sandbox):
        extra = sandbox / "extra"
        extra.mkdir()
        (home / ".claude" / "settings.json").write_text(
            json.dumps({"permissions": {"additionalDirectories": [str(extra)]}})
        )
        assert check(home, extra / "f.txt", "r", project) == ALLOW
        assert check(home, extra / "f.txt", "w", project) == ALLOW

    def test_additional_directories_from_project_and_local(self, home, project, sandbox):
        e1 = sandbox / "e1"
        e2 = sandbox / "e2"
        e1.mkdir()
        e2.mkdir()
        cfg = project / ".claude"
        cfg.mkdir()
        (cfg / "settings.json").write_text(
            json.dumps({"permissions": {"additionalDirectories": [str(e1)]}})
        )
        (cfg / "settings.local.json").write_text(
            json.dumps({"permissions": {"additionalDirectories": [str(e2)]}})
        )
        assert check(home, e1 / "f", "r", project) == ALLOW
        assert check(home, e2 / "f", "w", project) == ALLOW


@pytest.mark.parametrize("hook", [False, True], ids=["check", "hook"])
class TestOrdinaryWrites:
    def test_new_nested_parents_in_workspace_allow(self, home, project, hook):
        target = project / "a" / "b" / "c" / "new.txt"
        assert decide(home, target, "w", project, hook=hook) == ALLOW

    def test_symlinked_workspace_allows_new_nested_file(self, home, project, sandbox, hook):
        link = sandbox / "ws-link"
        link.symlink_to(project)
        target = link / "a" / "b" / "new.txt"
        assert decide(home, target, "w", link, hook=hook) == ALLOW
        assert decide(home, sandbox / "sibling.txt", "w", link, hook=hook) == ASK

    def test_tmp_spelling_allows_new_nested_file(self, home, project, hook):
        d = Path(tempfile.mkdtemp(dir=TMP_BASE))
        rel = d.relative_to(TMP_ROOT).as_posix()
        target = Path("/tmp") / rel / "x" / "y" / "new.txt"
        assert decide(home, target, "w", project, hook=hook) == ALLOW


class TestTmp:
    def test_private_tmp_allows_rw_even_foreign_and_old(self, home, project):
        d = tempfile.mkdtemp(dir=TMP_BASE)
        f = Path(d) / "old.log"
        f.write_text("x")
        os.utime(f, (0, 0))  # ancient mtime — must not matter, no birthtime logic
        assert check(home, f, "r", project) == ALLOW
        assert check(home, f, "w", project) == ALLOW

    def test_tmp_symlink_spelling_still_allows(self, home, project):
        d = tempfile.mkdtemp(dir=TMP_BASE)
        f = Path(d) / "f.txt"
        f.write_text("x")
        assert check(home, "/tmp/" + f.relative_to(TMP_ROOT).as_posix(), "r", project) == ALLOW

    def test_foreign_temp_dir_asks(self, home, project, sandbox):
        foreign = sandbox / "foreign.txt"
        foreign.write_text("x")
        assert check(home, foreign, "r", project) == ASK


class TestSymlinks:
    """Blocker gate (§3): the chain judges resolved paths."""

    def test_link_in_workspace_to_outside_secret_asks(self, home, project):
        secret = home / ".ssh-secret"
        secret.write_text("key")
        link = project / "innocent.txt"
        link.symlink_to(secret)
        assert check(home, link, "r", project) == ASK
        assert check(home, link, "w", project) == ASK

    def test_link_in_tmp_to_outside_secret_asks(self, home, project):
        secret = home / "secret.txt"
        secret.write_text("key")
        d = tempfile.mkdtemp(dir=TMP_BASE)
        link = Path(d) / "grab"
        link.symlink_to(secret)
        assert check(home, link, "r", project) == ASK

    def test_link_resolving_into_workspace_allows(self, home, project):
        inside = project / "real.txt"
        inside.write_text("x")
        d = tempfile.mkdtemp(dir=TMP_BASE)
        link = Path(d) / "into-proj"
        link.symlink_to(inside)
        assert check(home, link, "r", project) == ALLOW

    def test_symlinked_parent_on_creation_path_judged_at_target(self, home, project, sandbox):
        outside = sandbox / "outside" / "nest"
        outside.mkdir(parents=True)
        linkdir = project / "linkdir"
        linkdir.symlink_to(outside)
        # creating linkdir/new.txt really creates outside the workspace -> ask
        assert check(home, linkdir / "new.txt", "w", project) == ASK

    def test_dangling_link_write_judged_at_target_location(self, home, project):
        # Write through a dangling link creates the target file
        link = project / "dangle"
        link.symlink_to(home / "planted.txt")
        assert check(home, link, "w", project) == ASK

    def test_link_to_store_is_store(self, home, project):
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        grants.write_text('{"version":1,"grants":[]}')
        link = project / "not-a-store.json"
        link.symlink_to(grants)
        assert check(home, link, "w", project) == DENY


class TestStores:
    def test_store_write_denied_even_inside_workspace(self, home):
        # session launched from $HOME: workspace contains the stores
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        assert check(home, grants, "w", home) == DENY

    def test_sessions_dir_write_denied(self, home, project):
        target = home / ".claude" / "hooks" / "path-gate-sessions" / f"{SID}.json"
        assert check(home, target, "w", project) == DENY
        assert check(home, home / ".claude" / "hooks" / "path-gate-sessions", "w", project) == DENY

    def test_store_deny_survives_home_inside_an_allowlist_root(self, project):
        """The deny compares $HOME-derived store paths against a resolved target.
        With HOME under TMP_ROOT the two spellings used to diverge, and the deny
        silently stopped matching while the whole tree read as allowed."""
        h = Path(tempfile.mkdtemp(dir=TMP_BASE)) / "home"
        (h / ".claude" / "hooks").mkdir(parents=True)
        try:
            grants = h / ".claude" / "hooks" / "path-gate-grants.json"
            assert check(h, grants, "w", project) == DENY
        finally:
            shutil.rmtree(h.parent, ignore_errors=True)

    def test_store_read_asks_not_allows(self, home):
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        grants.write_text('{"version":1,"grants":[]}')
        assert check(home, grants, "r", home) == ASK


# The gate pre-creates both stores, so any spelling that can reach one (case or
# Unicode-fold variant on a folding volume, firmlink, symlinked parent) names an
# existing file, and the gate asks the filesystem whether it is the store. These
# tests use the same oracle: after the decision, does the spelling land on the
# store? That keeps them valid on case-sensitive and case-insensitive volumes
# alike. project == home puts the stores inside the workspace: a variant that
# slipped the carve-out would read as ALLOW, not merely ASK.
STORE_NAMES = {"grants": "path-gate-grants.json", "sessions": "path-gate-sessions"}
CASE_VARIANTS = {
    "canonical": lambda n: n,
    "upper": str.upper,
    "mixed": lambda n: "".join(c.upper() if i % 2 else c for i, c in enumerate(n)),
}


def store_target(home: Path, store: str, spelling: str) -> Path:
    name = CASE_VARIANTS[spelling](STORE_NAMES[store])
    target = home / ".claude" / "hooks" / name
    return target / f"{SID}.json" if store == "sessions" else target


def materialize(home: Path, store: str) -> None:
    hooks = home / ".claude" / "hooks"
    if store == "sessions":
        (hooks / STORE_NAMES[store]).mkdir()
    else:
        (hooks / STORE_NAMES[store]).write_text('{"version":1,"grants":[]}')


def lands_on_store(home: Path, target: Path) -> bool:
    """The kernel's answer: does writing TARGET touch a store? Asked after the
    gate ran, so the stores exist."""
    hooks = home / ".claude" / "hooks"
    for probe, store in (
        (target, hooks / STORE_NAMES["grants"]),
        (target.parent, hooks / STORE_NAMES["sessions"]),
        (target, hooks / STORE_NAMES["sessions"]),
    ):
        try:
            if probe.samefile(store):
                return True
        except OSError:
            continue
    return False


class TestStoreCreation:
    def test_decision_creates_both_stores(self, sandbox, project):
        h = sandbox / "fresh"
        h.mkdir()
        assert check(h, project / "a.txt", "w", project) == ALLOW
        hooks = h / ".claude" / "hooks"
        assert json.loads((hooks / "path-gate-grants.json").read_text()) == {
            "version": 1,
            "grants": [],
        }
        assert (hooks / "path-gate-sessions").is_dir()

    def test_hook_mode_creates_both_stores(self, sandbox, project):
        h = sandbox / "fresh"
        h.mkdir()
        assert run_hook(h, tool="Read", path=project / "a", cwd=project)["decision"] == "allow"
        assert (h / ".claude" / "hooks" / "path-gate-grants.json").is_file()
        assert (h / ".claude" / "hooks" / "path-gate-sessions").is_dir()

    def test_missing_sessions_dir_is_created_next_to_existing_grants(self, home, project):
        """A machine with grants from before the gate created both stores."""
        hooks = home / ".claude" / "hooks"
        cli(home, "grant", home / "g", "r", "--global")
        shutil.rmtree(hooks / "path-gate-sessions")
        before = (hooks / "path-gate-grants.json").read_text()
        assert check(home, home / "g" / "x", "r", project) == ALLOW
        assert (hooks / "path-gate-grants.json").read_text() == before
        assert (hooks / "path-gate-sessions").is_dir()
        assert check(home, project / "a.txt", "w", project) == ALLOW

    @pytest.mark.parametrize("blocker", ["grants-is-dir", "sessions-is-file", "hooks-is-file"])
    def test_unensurable_stores_deny_every_write(self, home, project, blocker):
        hooks = home / ".claude" / "hooks"
        if blocker == "grants-is-dir":
            (hooks / "path-gate-grants.json").mkdir()
        elif blocker == "sessions-is-file":
            (hooks / "path-gate-sessions").write_text("")
        else:
            shutil.rmtree(hooks)
            hooks.write_text("")
        r = cli(home, "check", project / "a.txt", "w", "--project", project)
        assert r.returncode == DENY
        assert "stores cannot be ensured" in r.stdout
        out = run_hook(home, tool="Write", path=project / "a.txt", cwd=project)
        assert out["decision"] == "deny"
        assert "stores cannot be ensured" in out["reason"]
        # reads cannot forge a grant: the ordinary chain still applies
        assert check(home, project / "a.txt", "r", project) == ALLOW
        assert check(home, home / "x", "r", project) == ASK

    def test_interrupted_creation_leaves_no_empty_store(self, sandbox, project):
        """A creator killed mid-write (here: SIGXFSZ under a zero file-size
        limit) must not leave an empty grants file that passes as a store."""
        h = sandbox / "fresh"
        h.mkdir()
        args = [*GATE_CMD, "check", str(project / "a.txt"), "w", "--project", str(project)]
        r = subprocess.run(
            ["sh", "-c", 'ulimit -f 0; exec "$@"', "sh", *args],
            env=env_for(h),
            capture_output=True,
            text=True,
        )
        grants = h / ".claude" / "hooks" / "path-gate-grants.json"
        assert not grants.exists()
        assert r.returncode == DENY
        assert "stores cannot be ensured" in r.stdout
        # the next decision without the limit creates it whole
        assert check(h, project / "a.txt", "w", project) == ALLOW
        assert json.loads(grants.read_text()) == {"version": 1, "grants": []}


class TestStoreSpellings:
    @pytest.mark.parametrize("spelling", CASE_VARIANTS)
    @pytest.mark.parametrize("present", [False, True], ids=["absent", "present"])
    @pytest.mark.parametrize("store", STORE_NAMES)
    def test_case_variant_write_decided_by_landing(self, home, store, present, spelling):
        if present:
            materialize(home, store)
        target = store_target(home, store, spelling)
        rc = check(home, target, "w", home)
        assert rc == (DENY if lands_on_store(home, target) else ALLOW)
        if spelling == "canonical":
            assert rc == DENY

    @pytest.mark.parametrize("spelling", ["upper", "mixed"])
    @pytest.mark.parametrize("store", STORE_NAMES)
    def test_case_variant_read_decided_by_landing(self, home, store, spelling):
        target = store_target(home, store, spelling)
        rc = check(home, target, "r", home)
        assert rc == (ASK if lands_on_store(home, target) else ALLOW)

    @pytest.mark.parametrize("hook", [False, True], ids=["check", "hook"])
    def test_case_variant_of_parent_dirs_decided_by_landing(self, home, hook):
        """Nothing under ~/.claude exists before the call: the gate creates it,
        then the variant spelling reaches it or not."""
        shutil.rmtree(home / ".claude")
        target = home / ".CLAUDE" / "Hooks" / "PATH-GATE-GRANTS.JSON"
        rc = decide(home, target, "w", home, hook=hook)
        assert rc == (DENY if lands_on_store(home, target) else ALLOW)

    @pytest.mark.parametrize(
        "name",
        [
            "path-gate-seßions",  # ß folds to ss under full case folding
            "path-gate-ſessions",  # ſ (long s) folds to s
            "path-gate-grantſ.json",
        ],
    )
    def test_unicode_fold_variant_decided_by_landing(self, home, name):
        target = home / ".claude" / "hooks" / name
        rc = check(home, target, "w", home)
        assert rc == (DENY if lands_on_store(home, target) else ALLOW)

    @pytest.mark.parametrize(
        "name",
        [
            "path-gate-grants.json.bak",  # sibling, not inside the store
            "path-gate-grants",
            "path-gate-sessions.json",
            "café.txt",
            "工作",
            # longer than any NAME_MAX: the write fails, nothing to deny
            pytest.param("é" * 2000, id="over-name-max"),
        ],
    )
    def test_non_store_neighbors_still_allowed(self, home, name):
        assert check(home, home / ".claude" / "hooks" / name, "w", home) == ALLOW

    def test_hardlinked_grants_denied(self, home, project):
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        grants.write_text('{"version":1,"grants":[]}')
        os.link(grants, project / "innocent.json")
        assert check(home, project / "innocent.json", "w", project) == DENY

    def test_non_ascii_workspace_file_allowed(self, home, project):
        assert check(home, project / "résumé.md", "w", project) == ALLOW

    @pytest.mark.parametrize("name", ["工作", "日記.json", "é"])
    @pytest.mark.parametrize("parent", ["/", "~", "~/.claude", "~/.claude/hooks"])
    def test_non_ascii_dir_is_an_ordinary_workspace(self, home, parent, name):
        base = Path("/") if parent == "/" else home / parent.removeprefix("~").lstrip("/")
        ws = base / name
        assert check(home, ws / "README.md", "w", ws) == ALLOW
        assert check(home, ws / "README.md", "r", ws) == ALLOW

    @pytest.mark.parametrize("name", ["工作", "日記.json", "é"])
    def test_non_ascii_dir_outside_workspace_asks(self, home, project, name):
        assert check(home, home / name / "x.txt", "w", project) == ASK

    def test_non_ascii_workspace_allows_in_hook_mode(self, home):
        ws = home / "工作" / "proj"
        ws.mkdir(parents=True)
        out = run_hook(home, tool="Write", path=ws / "README.md", cwd=ws)
        assert out["decision"] == "allow"

    # HOME may carry glob metacharacters; every comparison must stay literal.
    @pytest.mark.parametrize(
        ("home_dir", "sibling"),
        [("a[1]é", "a1é"), ("*é", "zé"), ("?é", "zé")],
    )
    def test_glob_metacharacters_in_home_stay_literal(self, sandbox, home_dir, sibling):
        h = sandbox / home_dir / "home"
        (h / ".claude" / "hooks").mkdir(parents=True)
        rel = Path(".claude") / "hooks" / "path-gate-grants.json"
        assert check(h, h / rel, "w", sandbox) == DENY
        assert check(h, sandbox / sibling / "home" / rel, "w", sandbox) == ALLOW

    def test_non_ascii_home_stores_denied(self, sandbox):
        h = sandbox / "ホーム" / "home"
        (h / ".claude" / "hooks").mkdir(parents=True)
        for spelling in CASE_VARIANTS:
            target = store_target(h, "grants", spelling)
            rc = check(h, target, "w", h)
            assert rc == (DENY if lands_on_store(h, target) else ALLOW)
        assert check(h, store_target(h, "grants", "canonical"), "w", h) == DENY
        assert check(h, h / "ノート" / "a.txt", "w", h) == ALLOW


class TestDecisionCost:
    """The hook runs under a timeout; a kill skips the ask backstop, which is
    fail-open. Cost must stay linear in the path, however long or deep."""

    BOUND_S = 3.0  # generous: a warm decision takes ~0.1 s

    def timed_check(self, home, path, project) -> tuple[int, float]:
        t0 = time.monotonic()
        rc = check(home, path, "w", project)
        return rc, time.monotonic() - t0

    def test_long_deep_path_decides_fast(self, home, project):
        path = project.joinpath(*["d" * 200] * 20, "f.txt")
        assert len(str(path)) > 4000
        rc, dt = self.timed_check(home, path, project)
        assert rc == ALLOW
        assert dt < self.BOUND_S, f"{dt:.2f}s"


def link_chain(where: Path, length: int, target: Path) -> Path:
    """where/l0 -> l1 -> ... -> l{length-1} -> target; returns the head."""
    for i in range(length - 1):
        (where / f"l{i}").symlink_to(f"l{i + 1}")
    (where / f"l{length - 1}").symlink_to(target)
    return where / "l0"


class TestSymlinkChains:
    # Past this many links every kernel gives up (Linux 40, macOS 32), and so
    # must the gate: a decision on some intermediate hop judges a path the
    # write never lands on.
    BEYOND_ANY_KERNEL = 64

    def test_long_chain_to_absent_store_is_store(self, home, project):
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        head = link_chain(project, 12, grants)
        assert check(home, head, "w", project) == DENY

    def test_long_chain_to_workspace_file_still_resolves(self, home, project):
        head = link_chain(project, 12, project / "real.txt")
        assert check(home, head, "w", project) == ALLOW

    def test_chain_beyond_kernel_limit_fails_closed(self, home, project):
        head = link_chain(project, self.BEYOND_ANY_KERNEL, project / "real.txt")
        assert check(home, head, "w", project) == DENY
        assert check(home, head, "r", project) == ASK

    def test_symlink_loop_fails_closed(self, home, project):
        (project / "a").symlink_to("b")
        (project / "b").symlink_to("a")
        assert check(home, project / "a", "w", project) == DENY
        assert check(home, project / "a", "r", project) == ASK
        assert check(home, project / "a" / "x.txt", "w", project) == DENY

    def test_dangling_parent_link_is_followed(self, home, project):
        sessions = home / ".claude" / "hooks" / "path-gate-sessions"
        (project / "d").symlink_to(sessions)
        assert check(home, project / "d" / f"{SID}.json", "w", project) == DENY

    def test_unresolvable_deny_says_why(self, home, project):
        (project / "a").symlink_to("a")
        r = cli(home, "check", project / "a", "w", "--project", project)
        assert r.returncode == DENY
        assert "cannot be resolved (a symlink loop" in r.stdout

    def test_unresolvable_write_denied_in_hook_mode(self, home, project):
        (project / "a").symlink_to("a")
        out = run_hook(home, tool="Write", path=project / "a", cwd=project)
        assert out["decision"] == "deny"
        assert "cannot be resolved" in out["reason"]

    def test_unresolvable_read_asks_without_pending(self, home, project):
        (project / "a").symlink_to("a")
        out = run_hook(home, tool="Read", path=project / "a", cwd=project)
        assert out["decision"] == "ask"
        assert "nothing is recorded" in out["reason"]
        assert "auto-recorded" not in out["reason"]
        assert not session_file(home).exists()

    def test_grant_refuses_unresolvable_path(self, home, project):
        (project / "a").symlink_to("a")
        r = cli(home, "grant", project / "a", "w", "--global")
        assert r.returncode == 3
        assert "cannot resolve" in r.stderr
        assert not (home / ".claude" / "hooks" / "path-gate-grants.json").exists()


def node_write(path: Path, data: str) -> None:
    """Write a normalized path (no '..') the way Claude Code's file tools do:
    Node's recursive mkdir of the parent, then an open the kernel resolves, so
    where the file lands is the kernel's answer to what the gate judged."""

    def mkdirp(p: Path) -> None:
        try:
            p.mkdir()
        except FileExistsError:
            return
        except FileNotFoundError:
            mkdirp(p.parent)
            with contextlib.suppress(FileExistsError):
                p.mkdir()

    mkdirp(path.parent)
    path.write_text(data)


def decide(home, path, mode, project, *, hook: bool) -> int:
    """One decision, in check mode or in hook mode (JSON on stdin)."""
    if not hook:
        return check(home, path, mode, project)
    tool = "Write" if mode == "w" else "Read"
    out = run_hook(home, tool=tool, path=path, cwd=project)
    return {"allow": ALLOW, "ask": ASK, "deny": DENY}[out["decision"]]


BOTH_MODES = pytest.mark.parametrize("hook", [False, True], ids=["check", "hook"])


@BOTH_MODES
class TestClaudeCodeNormalization:
    """Claude Code's file tools trim the path (JS trim), then apply Node's
    path.normalize / path.resolve(cwd, ...) before the kernel sees it, and
    hooks receive that normalized path. So '..' in the tool input is LEXICAL:
    `x/..` is gone before any symlink `x` could be followed. The gate judges
    the same path, then follows symlinks physically from there."""

    def test_missing_dir_dotdot_then_link_to_store_denied(self, home, sandbox, hook):
        ws = sandbox / "ws"
        ws.mkdir()
        (ws / "l1").symlink_to(home / ".claude")
        target = ws / "nonexist" / ".." / "l1" / "hooks" / "path-gate-grants.json"
        assert decide(home, target, "w", ws, hook=hook) == DENY

    def test_dotdot_through_link_is_lexical_so_store_denied(self, home, sandbox, hook):
        """Physically `evil/../..` would climb from evil's target into ws; the
        tool collapses it to the sandbox, where home's store lives."""
        ws = sandbox / "ws"
        (ws / "q" / "r").mkdir(parents=True)
        (ws / "evil").symlink_to(ws / "q" / "r")
        target = ws / "evil" / ".." / ".." / "home" / ".claude" / "hooks" / "path-gate-grants.json"
        assert decide(home, target, "w", ws, hook=hook) == DENY

    def test_dotdot_then_link_out_of_workspace_asks(self, home, sandbox, hook):
        ws = sandbox / "ws"
        ws.mkdir()
        elsewhere = sandbox / "elsewhere"
        elsewhere.mkdir()
        (ws / "out").symlink_to(elsewhere)
        assert decide(home, ws / "zz" / ".." / "out" / "evil.txt", "w", ws, hook=hook) == ASK

    def test_dotdot_after_link_stays_where_the_text_says(self, home, sandbox, hook):
        ws = sandbox / "ws"
        ws.mkdir()
        (sandbox / "outside" / "nest").mkdir(parents=True)
        (ws / "mylink").symlink_to(sandbox / "outside" / "nest")
        assert decide(home, ws / "mylink" / ".." / "f.txt", "w", ws, hook=hook) == ALLOW

    def test_climb_out_of_workspace_asks(self, home, sandbox, hook):
        ws = sandbox / "ws"
        ws.mkdir()
        assert decide(home, ws / "m" / ".." / ".." / "x.txt", "w", ws, hook=hook) == ASK

    def test_many_dotdot_pairs_are_not_hops(self, home, project, hook):
        target = project.joinpath(*["n", ".."] * 41, "x.txt")
        assert decide(home, target, "w", project, hook=hook) == ALLOW


# JS trim's set, one of each kind: ASCII, a line terminator, multibyte.
PADS = {
    "space": " ",
    "tab": "\t",
    "newline": "\n",
    "nbsp": "\u00a0",
    "ideographic": "\u3000",
    "bom": "\ufeff",
    "line-sep": "\u2028",
}


class TestCliTrim:
    """check / grant / revoke take raw paths, which Claude Code's helper would
    trim before normalizing: the CLI judges the trimmed path."""

    @pytest.mark.parametrize("pad", PADS.values(), ids=list(PADS))
    def test_padded_store_path_denied(self, home, sandbox, pad):
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        assert check(home, f"{pad}{grants}{pad}", "w", sandbox) == DENY

    def test_padded_grant_path_is_trimmed(self, home, project):
        r = cli(home, "grant", f" \u3000{home / 'g'}\n", "r", "--global")
        assert r.returncode == 0, r.stderr
        assert f"path={(home / 'g').resolve()} mode=r" in r.stdout

    def test_trim_keeps_non_utf8_bytes(self, home, project):
        """Only whitespace is cut; a name's own bytes arrive untouched even when
        they are not UTF-8 (Linux allows any bytes but '/' and NUL)."""
        w = os.fsencode(project.resolve())
        name = b"a\xffb\xc2.txt"  # invalid UTF-8, and a lone lead byte at the end
        raw = b" \xc2\xa0" + w + b"/" + name + b"\xe3\x80\x80\n"
        r = subprocess.run(
            [*GATE_CMD, "check", raw, "w", "--project", w],
            env=env_for(home),
            capture_output=True,
        )
        assert r.returncode == ALLOW, r.stdout
        assert r.stdout.startswith(b"allow: w on " + w + b"/" + name + b" (project")

    def test_long_padding_trims_in_linear_time(self, home, sandbox):
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        pad = " \u00a0" * 8000  # 16k characters on each side
        t0 = time.monotonic()
        rc = check(home, f"{pad}{grants}{pad}", "w", sandbox)
        dt = time.monotonic() - t0
        assert rc == DENY
        assert dt < 3.0, f"{dt:.2f}s"


class TestHookTrailingWhitespace:
    """Claude Code trims, THEN normalizes, so the path a hook receives can end
    in whitespace ("/ws/b /." normalizes to "/ws/b "), and the tool opens it as
    is. Hook mode must judge exactly that name, never a trimmed one."""

    @pytest.mark.parametrize(
        "pad", ["\n", " ", "\u00a0", "\u3000"], ids=["nl", "sp", "nbsp", "u3000"]
    )
    def test_padded_name_linked_to_grants_denied(self, home, sandbox, pad):
        ws = sandbox / "ws"
        (ws / "b").mkdir(parents=True)  # the trimmed name: an ordinary dir
        (ws / f"b{pad}").symlink_to(home / ".claude" / "hooks" / "path-gate-grants.json")
        out = run_hook(home, tool="Write", path=f"{ws}/b{pad}", cwd=ws)
        assert out["decision"] == "deny"
        assert "tool-owned path-gate store" in out["reason"]

    @pytest.mark.parametrize(
        "pad", ["\n", " ", "\u00a0", "\u3000"], ids=["nl", "sp", "nbsp", "u3000"]
    )
    def test_padded_name_linked_into_sessions_denied(self, home, sandbox, pad):
        ws = sandbox / "ws"
        ws.mkdir()
        sessions = home / ".claude" / "hooks" / "path-gate-sessions"
        (ws / f"d{pad}").symlink_to(sessions / f"{SID}.json")
        out = run_hook(home, tool="Write", path=f"{ws}/d{pad}", cwd=ws)
        assert out["decision"] == "deny"

    @pytest.mark.parametrize(
        "pad", ["\n", " ", "\u00a0", "\u3000"], ids=["nl", "sp", "nbsp", "u3000"]
    )
    def test_padded_sibling_of_project_asks_with_exact_path(self, home, project, pad):
        target = f"{project}{pad}"  # a sibling of the project dir, not the project
        out = run_hook(home, tool="Write", path=target, cwd=project)
        assert out["decision"] == "ask"
        exact = f"{project.resolve()}{pad}"
        assert f"write access to {exact} — outside the workspace" in out["reason"]
        data = json.loads(session_file(home).read_text())
        assert data["pending"] == [{"path": exact, "mode": "w"}]

    def test_padded_name_inside_workspace_allows(self, home, project):
        out = run_hook(home, tool="Write", path=f"{project}/notes ", cwd=project)
        assert out["decision"] == "allow"


class TestHomeUnset:
    """Every store and root hangs off $HOME; without it no decision is possible,
    and each mode must still fail the way it promises."""

    def env(self) -> dict[str, str]:
        e = dict(os.environ)
        e.pop("HOME", None)
        e.pop("CLAUDE_PROJECT_DIR", None)
        return e

    def test_hook_mode_fails_closed_to_ask(self, project):
        payload = {
            "tool_name": "Write",
            "tool_input": {"file_path": str(project / "a.txt")},
            "cwd": str(project),
            "session_id": SID,
            "permission_mode": "default",
        }
        r = subprocess.run(
            GATE_CMD, input=json.dumps(payload), text=True, capture_output=True, env=self.env()
        )
        assert r.returncode == 0
        out = json.loads(r.stdout)["hookSpecificOutput"]
        assert out["permissionDecision"] == "ask"
        assert "crashed before reaching a decision" in out["permissionDecisionReason"]

    def test_record_never_fails(self, project):
        payload = {"tool_name": "Read", "tool_input": {"file_path": "/x"}, "session_id": SID}
        r = subprocess.run(
            [*GATE_CMD, "record"],
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=self.env(),
        )
        assert r.returncode == 0
        assert r.stdout == ""

    @pytest.mark.parametrize(
        "home_value", [None, "", "relative/home"], ids=["unset", "empty", "relative"]
    )
    def test_cli_refuses_with_reason(self, project, home_value):
        e = self.env()
        if home_value is not None:
            e["HOME"] = home_value
        r = subprocess.run(
            [*GATE_CMD, "check", str(project / "a"), "w", "--project", str(project)],
            text=True,
            capture_output=True,
            env=e,
        )
        assert r.returncode == 3
        assert "HOME must be set to an absolute path" in r.stderr


class TestClaudeCodeNormalizationLinks:
    """'..' inside a symlink target is the kernel's, applied physically."""

    def test_dotdot_in_existing_link_target_is_physical(self, home, sandbox):
        ws = sandbox / "ws"
        ws.mkdir()
        (sandbox / "outside" / "nest").mkdir(parents=True)
        (ws / "inner").symlink_to(sandbox / "outside" / "nest")
        (ws / "up").symlink_to("inner/..")  # physically: sandbox/outside
        assert check(home, ws / "up" / "x.txt", "w", ws) == ASK

    def test_dotdot_in_dangling_link_target_is_physical(self, home, sandbox):
        ws = sandbox / "ws"
        ws.mkdir()
        (sandbox / "outside" / "nest").mkdir(parents=True)
        (ws / "inner").symlink_to(sandbox / "outside" / "nest")
        (ws / "up").symlink_to("inner/../new.txt")  # physically: outside/new.txt
        assert check(home, ws / "up", "w", ws) == ASK
        node_write(ws / "up", "x")
        assert (sandbox / "outside" / "new.txt").read_text() == "x"

    def test_dotdot_after_absent_name_in_link_target_is_collapsed(self, home, sandbox):
        """The kernel fails this write (`missing` is absent); the gate still
        judges a plain path, never a spelling that keeps '..'."""
        ws = sandbox / "ws"
        ws.mkdir()
        (ws / "l").symlink_to("missing/../../escape.txt")
        r = cli(home, "check", ws / "l", "w", "--project", ws)
        assert r.returncode == ASK
        assert f"on {sandbox.resolve()}/escape.txt " in r.stdout


# (raw path relative to the workspace W, the path the tool opens). Written out
# literally; test_table_matches_node checks it against Node itself.
CC_PATHS = [
    ("W/a/../b.txt", "W/b.txt"),
    ("W/nonexist/../x.txt", "W/x.txt"),
    ("W//a/./b.txt", "W/a/b.txt"),
    ("W/a/b/../../c.txt", "W/c.txt"),
    ("  W/a.txt\n", "W/a.txt"),
    (" ﻿W/a.txt　 ", "W/a.txt"),
    ("W/a​.txt", "W/a​.txt"),  # zero-width space is not whitespace
    ("W/tab\tin/name", "W/tab\tin/name"),  # only the ends are trimmed
]

NODE_NORMALIZE = """
const p = require("path");
const [raw, cwd] = JSON.parse(process.argv[1]);
const t = raw.trim();
process.stdout.write(JSON.stringify(p.isAbsolute(t) ? p.normalize(t) : p.resolve(cwd, t)));
"""


def node_normalize(raw: str, cwd: str) -> str:
    r = subprocess.run(
        ["node", "-e", NODE_NORMALIZE, json.dumps([raw, cwd])],
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(r.stdout)


class TestClaudeCodePathTable:
    @pytest.mark.parametrize(("raw", "expected"), CC_PATHS)
    def test_gate_judges_the_normalized_path(self, home, project, raw, expected):
        w = str(project.resolve())
        r = cli(home, "check", raw.replace("W", w, 1), "w", "--project", project)
        assert r.returncode == ALLOW, r.stdout
        assert r.stdout.startswith(f"allow: w on {expected.replace('W', w, 1)} (project")

    def test_relative_path_resolves_against_cwd(self, home, project):
        w = str(project.resolve())
        r = subprocess.run(
            [*GATE_CMD, "check", "a/../b.txt", "w", "--project", w],
            env=env_for(home),
            capture_output=True,
            text=True,
            cwd=w,
        )
        assert r.stdout.startswith(f"allow: w on {w}/b.txt (project")

    @pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
    @pytest.mark.parametrize(("raw", "expected"), CC_PATHS)
    def test_table_matches_node(self, raw, expected):
        assert node_normalize(raw.replace("W", "/w", 1), "/") == expected.replace("W", "/w", 1)


# Characters jq's @tsv escapes. A hook-mode field must reach the decision byte
# for byte (inside a name: at either end the tool's trim removes them).
ODD_CHARS = {"backslash": "\\", "tab": "\t", "newline": "\n"}


class TestHookFieldFidelity:
    @pytest.mark.parametrize("ch", ODD_CHARS.values(), ids=list(ODD_CHARS))
    def test_store_behind_odd_name_denied(self, home, sandbox, ch):
        ws = sandbox / "ws"
        ws.mkdir()
        (ws / f"a{ch}b").symlink_to(home / ".claude" / "hooks")
        target = ws / f"a{ch}b" / "path-gate-grants.json"
        assert check(home, target, "w", ws) == DENY
        out = run_hook(home, tool="Write", path=target, cwd=ws)
        assert out["decision"] == "deny"
        assert "tool-owned path-gate store" in out["reason"]

    @pytest.mark.parametrize("ch", ODD_CHARS.values(), ids=list(ODD_CHARS))
    def test_pending_records_the_exact_path(self, home, project, ch):
        target = home / f"f{ch}g" / f"x{ch}y"
        out = run_hook(home, tool="Read", path=target, cwd=project)
        assert out["decision"] == "ask"
        data = json.loads(session_file(home).read_text())
        assert data["pending"] == [{"path": str(target), "mode": "r"}]

    def test_nul_in_path_fails_closed_to_ask(self, home, project):
        out = run_hook(home, tool="Write", path=f"{project}/a\0b", cwd=project)
        assert out["decision"] == "ask"
        assert "crashed" in out["reason"]


@BOTH_MODES
class TestSymlinkedClaudeDir:
    """Dotfiles setups link ~/.claude or ~/.claude/hooks elsewhere; the store is
    wherever the filesystem puts it, whatever $HOME spells."""

    @pytest.mark.parametrize("linked", [".claude", ".claude/hooks"])
    def test_store_at_link_target_denied(self, sandbox, linked, hook):
        h = sandbox / "home"
        dots = sandbox / "dots" / "claude"
        (dots / "hooks").mkdir(parents=True)
        link = h / linked
        link.parent.mkdir(parents=True, exist_ok=True)
        link.symlink_to(dots if linked == ".claude" else dots / "hooks")
        target = dots / "hooks" / "path-gate-grants.json"
        assert decide(h, target, "w", sandbox, hook=hook) == DENY
        sessions = dots / "hooks" / "path-gate-sessions" / f"{SID}.json"
        assert decide(h, sessions, "w", sandbox, hook=hook) == DENY
        # a sibling under the same link target is an ordinary workspace file
        assert decide(h, dots / "hooks" / "other.json", "w", sandbox, hook=hook) == ALLOW


# /System/Volumes/Data is where macOS's firmlinks point; realpath keeps that
# spelling, so only the filesystem's own identity can see it is the same file.
DATA_VOLUME = Path("/System/Volumes/Data")


def data_volume_spelling(p: Path) -> Path | None:
    alias = Path(str(DATA_VOLUME) + str(p))
    try:
        return alias if alias.samefile(p) else None
    except OSError:
        return None


@BOTH_MODES
class TestFirmlinkSpelling:
    def test_store_under_data_volume_spelling_denied(self, home, hook):
        hooks = home / ".claude" / "hooks"
        alias = data_volume_spelling(hooks)
        if alias is None:
            pytest.skip("no firmlinked data volume on this host")
        assert decide(home, alias / "path-gate-grants.json", "w", home, hook=hook) == DENY
        assert decide(home, alias / "path-gate-sessions" / "s.json", "w", home, hook=hook) == DENY


@BOTH_MODES
class TestNewlineInLinkTargets:
    """$(readlink) and $(realpath) strip trailing newlines; a name ending in one
    must keep it, or the gate judges a sibling of where the write lands."""

    def test_dangling_link_to_name_ending_in_newline(self, home, sandbox, hook):
        ws = sandbox / "ws"
        (ws / "d").mkdir(parents=True)  # the name the stripped target would hit
        outside = sandbox / "outside"
        outside.mkdir()
        (ws / "d\n").symlink_to(outside / "new.txt")  # dangling, lands outside
        (ws / "l").symlink_to("d\n")
        assert decide(home, ws / "l", "w", ws, hook=hook) == ASK
        node_write(ws / "l", "x")
        assert (outside / "new.txt").read_text() == "x"

    def test_link_to_dir_ending_in_newline(self, home, sandbox, hook):
        ws = sandbox / "ws"
        ws.mkdir()
        (sandbox / "ws\n").mkdir()  # outside the workspace, one byte away
        (ws / "link").symlink_to("../ws\n")
        assert decide(home, ws / "link" / "new.txt", "w", ws, hook=hook) == ASK

    def test_chain_to_absent_store_through_newline_name(self, home, sandbox, hook):
        ws = sandbox / "ws"
        ws.mkdir()
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        (ws / "d\n").symlink_to(grants)
        (ws / "l").symlink_to("d\n")
        assert decide(home, ws / "l", "w", ws, hook=hook) == DENY


class TestMemoryAndWhitelist:
    def test_current_project_memory_rw(self, home, project):
        mem = home / ".claude" / "projects" / slug_of(project) / "memory" / "m.md"
        assert check(home, mem, "r", project) == ALLOW
        assert check(home, mem, "w", project) == ALLOW

    def test_cross_slug_memory_asks(self, home, project):
        other = home / ".claude" / "projects" / "-some-other-proj" / "memory" / "m.md"
        assert check(home, other, "w", project) == ASK
        assert check(home, other, "r", project) == ASK

    def test_current_slug_transcripts_read_only(self, home, project):
        f = home / ".claude" / "projects" / slug_of(project) / "tool-results" / "t.txt"
        assert check(home, f, "r", project) == ALLOW
        assert check(home, f, "w", project) == ASK

    def test_cross_slug_transcripts_ask(self, home, project):
        f = home / ".claude" / "projects" / "-other" / "session.jsonl"
        assert check(home, f, "r", project) == ASK

    @pytest.mark.parametrize(
        "sub", ["plugins", "skills", "agents", "commands", "rules", "output-styles"]
    )
    def test_structural_dirs_read_only(self, home, project, sub):
        f = home / ".claude" / sub / "thing.md"
        assert check(home, f, "r", project) == ALLOW
        assert check(home, f, "w", project) == ASK

    @pytest.mark.parametrize("name", ["CLAUDE.md", "keybindings.json"])
    def test_structural_files_read_only(self, home, project, name):
        f = home / ".claude" / name
        assert check(home, f, "r", project) == ALLOW
        assert check(home, f, "w", project) == ASK

    @pytest.mark.parametrize("name", ["settings.json", ".credentials.json", "history.jsonl"])
    def test_excluded_claude_files_ask(self, home, project, name):
        assert check(home, home / ".claude" / name, "r", project) == ASK

    def test_whitelist_through_symlinked_claude_dir(self, sandbox, project):
        """Dotfiles setups link ~/.claude elsewhere: its whitelisted roots are
        wherever the link resolves, like every target."""
        h = sandbox / "home"
        dots = sandbox / "dots" / "claude"
        (dots / "hooks").mkdir(parents=True)
        h.mkdir()
        (h / ".claude").symlink_to(dots)
        c = h / ".claude"
        mem = c / "projects" / slug_of(project) / "memory" / "m.md"
        assert check(h, mem, "w", project) == ALLOW
        assert check(h, c / "projects" / slug_of(project) / "t.jsonl", "r", project) == ALLOW
        assert check(h, c / "skills" / "x.md", "r", project) == ALLOW
        assert check(h, c / "CLAUDE.md", "r", project) == ALLOW
        assert check(h, c / "skills" / "x.md", "w", project) == ASK
        assert check(h, c / "settings.json", "r", project) == ASK
        assert check(h, c / "projects" / "-other" / "memory" / "m.md", "w", project) == ASK

    def test_whitelist_through_symlinked_subdir(self, home, project, sandbox):
        shared = sandbox / "shared-skills"
        shared.mkdir()
        (home / ".claude" / "skills").symlink_to(shared)
        assert check(home, home / ".claude" / "skills" / "x.md", "r", project) == ALLOW
        assert check(home, shared / "x.md", "r", project) == ALLOW  # same file
        assert check(home, sandbox / "other.md", "r", project) == ASK

    def test_slug_matches_real_layout(self):
        """Every non-alnum -> '-'. The rule is undocumented upstream, so this is a
        transcription of observed behaviour: re-check by eye against
        `ls ~/.claude/projects` when a Claude Code release moves it."""
        assert slug_of(Path("/home/u/.local/share/chezmoi")) == "-home-u--local-share-chezmoi"


class TestGrantsCli:
    def test_grant_without_tty_and_match(self, home, project):
        r = cli(home, "grant", str(home / "shared"), "r", "--project", str(project))
        assert r.returncode == 0, r.stderr
        assert check(home, home / "shared" / "x.txt", "r", project) == ALLOW
        assert check(home, home / "shared" / "x.txt", "w", project) == ASK

    def test_grant_scope_other_project_does_not_match(self, home, project, sandbox):
        other = sandbox / "other"
        other.mkdir()
        cli(home, "grant", str(home / "shared"), "r", "--project", str(other))
        assert check(home, home / "shared" / "x", "r", project) == ASK

    def test_global_grant_matches_everywhere(self, home, project):
        cli(home, "grant", str(home / "g"), "rw", "--global")
        assert check(home, home / "g" / "x", "w", project) == ALLOW

    def test_expired_ttl_is_inert(self, home, project):
        cli(home, "grant", str(home / "t"), "r", "--global", "--ttl", "1s")
        time.sleep(1.1)
        assert check(home, home / "t" / "x", "r", project) == ASK

    def test_revoke(self, home, project):
        cli(home, "grant", str(home / "g2"), "r", "--global")
        cli(home, "revoke", str(home / "g2"), "--global")
        assert check(home, home / "g2" / "x", "r", project) == ASK

    def test_corrupt_grants_fail_closed(self, home, project):
        (home / ".claude" / "hooks" / "path-gate-grants.json").write_text("{corrupt")
        assert check(home, home / "anything", "r", project) == ASK

    @pytest.mark.parametrize(
        "content",
        ["", "{corrupt", '{"version":1}', "[]"],
        ids=["empty", "bad-json", "no-grants", "array"],
    )
    @pytest.mark.parametrize("verb", ["grant", "revoke"])
    def test_broken_store_is_refused_and_left_untouched(self, home, verb, content):
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        grants.write_text(content)
        extra = ["r"] if verb == "grant" else []
        r = cli(home, verb, home / "g", *extra, "--global")
        assert r.returncode == 3
        assert "is empty or not a valid store; nothing was changed" in r.stderr
        assert "granted" not in r.stdout
        assert grants.read_text() == content
        assert not list(grants.parent.glob(".pg.*"))  # no temp left behind

    def test_failed_store_write_leaves_store_untouched(self, home):
        """jq killed mid-write (SIGXFSZ) leaves a partial temp file: it must
        never replace the store."""
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        assert cli(home, "grant", home / "keep", "r", "--global").returncode == 0
        before = grants.read_text()
        args = [*GATE_CMD, "grant", str(home / "g"), "r", "--global"]
        r = subprocess.run(
            ["sh", "-c", 'ulimit -f 0; exec "$@"', "sh", *args],
            env=env_for(home),
            capture_output=True,
            text=True,
        )
        assert r.returncode == 3
        assert "the edit produced no valid store" in r.stderr
        assert grants.read_text() == before

    def test_grant_then_revoke_round_trip(self, home, project):
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        assert cli(home, "grant", home / "g", "r", "--global").returncode == 0
        paths = [g["path"] for g in json.loads(grants.read_text())["grants"]]
        assert paths == [str((home / "g").resolve())]
        r = cli(home, "revoke", home / "g", "--global")
        assert r.stdout.startswith("revoked 1 grant(s)")
        assert json.loads(grants.read_text())["grants"] == []

    def test_session_flag_consults_session_store(self, home, project):
        sdir = home / ".claude" / "hooks" / "path-gate-sessions"
        sdir.mkdir()
        (sdir / f"{SID}.json").write_text(
            json.dumps({"pending": [], "granted": [{"path": str(home / "sess"), "mode": "r"}]})
        )
        assert check(home, home / "sess" / "f", "r", project, session=SID) == ALLOW
        assert check(home, home / "sess" / "f", "r", project) == ASK  # no --session
        assert check(home, home / "sess" / "f", "w", project, session=SID) == ASK


# --- hook mode (stdin JSON) ---------------------------------------------------


def run_hook(home, *, tool, path, cwd, session=SID, mode="default", extra_env=None):
    key = {
        "Read": "file_path",
        "Edit": "file_path",
        "Write": "file_path",
        "NotebookEdit": "notebook_path",
        "Grep": "path",
        "Glob": "path",
    }[tool]
    payload = {
        "tool_name": tool,
        "tool_input": {key: str(path)},
        "cwd": str(cwd),
        "session_id": session,
        "permission_mode": mode,
    }
    e = env_for(home)
    if extra_env:
        e.update(extra_env)
    r = subprocess.run(GATE_CMD, input=json.dumps(payload), text=True, capture_output=True, env=e)
    assert r.returncode == 0, r.stderr
    if not r.stdout.strip():
        return {"decision": "allow"}
    out = json.loads(r.stdout)["hookSpecificOutput"]
    return {
        "decision": out["permissionDecision"],
        "reason": out.get("permissionDecisionReason", ""),
    }


def session_file(home) -> Path:
    return home / ".claude" / "hooks" / "path-gate-sessions" / f"{SID}.json"


class TestHookMode:
    def test_workspace_read_allows_silently(self, home, project):
        f = project / "a.txt"
        assert run_hook(home, tool="Read", path=f, cwd=project)["decision"] == "allow"
        assert not session_file(home).exists()

    def test_foreign_read_asks_and_records_pending(self, home, project):
        target = home / "foreign.txt"
        out = run_hook(home, tool="Read", path=target, cwd=project)
        assert out["decision"] == "ask"
        assert "auto-recorded" in out["reason"]
        data = json.loads(session_file(home).read_text())
        assert {"path": str(target), "mode": "r"} in data["pending"]
        assert data["granted"] == []

    def test_bypass_mode_writes_no_pending(self, home, project):
        out = run_hook(
            home, tool="Read", path=home / "f.txt", cwd=project, mode="bypassPermissions"
        )
        assert out["decision"] == "ask"
        assert not session_file(home).exists()

    def test_store_write_denies(self, home, project):
        grants = home / ".claude" / "hooks" / "path-gate-grants.json"
        out = run_hook(home, tool="Write", path=grants, cwd=project)
        assert out["decision"] == "deny"

    def test_grep_root_gated_by_path_param(self, home, project):
        out = run_hook(home, tool="Grep", path=home / "somewhere", cwd=project)
        assert out["decision"] == "ask"
        data = json.loads(session_file(home).read_text())
        assert {"path": str(home / "somewhere"), "mode": "r"} in data["pending"]

    def test_glob_without_path_is_untouched(self, home, project):
        payload = {
            "tool_name": "Glob",
            "tool_input": {"pattern": "**/*.py"},
            "cwd": str(project),
            "session_id": SID,
            "permission_mode": "default",
        }
        r = subprocess.run(
            GATE_CMD,
            input=json.dumps(payload),
            text=True,
            capture_output=True,
            env=env_for(home),
        )
        assert r.returncode == 0 and not r.stdout.strip()

    def test_claude_project_dir_env_wins_over_cwd(self, home, project, sandbox):
        elsewhere = sandbox / "elsewhere"
        elsewhere.mkdir()
        out = run_hook(
            home,
            tool="Read",
            path=project / "x.txt",
            cwd=elsewhere,
            extra_env={"CLAUDE_PROJECT_DIR": str(project)},
        )
        assert out["decision"] == "allow"

    def test_malicious_session_id_is_ignored(self, home, project):
        out = run_hook(
            home, tool="Read", path=home / "f", cwd=project, session="../../../etc/pwned"
        )
        assert out["decision"] == "ask"
        # nothing outside the sessions dir may be created
        assert not (home / ".claude" / "etc").exists()
        assert not (home / "etc").exists()

    def test_pending_dedupes(self, home, project):
        for _ in range(3):
            run_hook(home, tool="Read", path=home / "f", cwd=project)
        data = json.loads(session_file(home).read_text())
        assert data["pending"].count({"path": str(home / "f"), "mode": "r"}) == 1


# --- record verb (PostToolUse / PostToolUseFailure) ---------------------------


def run_record(home, *, tool="Read", path, cwd, session=SID, mode="default"):
    key = {
        "Read": "file_path",
        "Edit": "file_path",
        "Write": "file_path",
        "NotebookEdit": "notebook_path",
        "Grep": "path",
        "Glob": "path",
    }[tool]
    payload = {
        "tool_name": tool,
        "tool_input": {key: str(path)},
        "cwd": str(cwd),
        "session_id": session,
        "permission_mode": mode,
    }
    r = subprocess.run(
        [*GATE_CMD, "record"],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        env=env_for(home),
    )
    assert r.returncode == 0, r.stderr


def seed_session(home, pending=(), granted=()):
    sdir = home / ".claude" / "hooks" / "path-gate-sessions"
    sdir.mkdir(parents=True, exist_ok=True)
    f = sdir / f"{SID}.json"
    f.write_text(json.dumps({"pending": list(pending), "granted": list(granted)}))
    return f


class TestRecord:
    def test_promote_happy_path(self, home, project):
        target = home / "foreign.txt"
        run_hook(home, tool="Read", path=target, cwd=project)  # ask + pending
        run_record(home, tool="Read", path=target, cwd=project)  # approval proven
        data = json.loads(session_file(home).read_text())
        assert {"path": str(target), "mode": "r"} in data["granted"]
        assert data["pending"] == []
        # and the grant now covers the whole check chain
        assert check(home, target, "r", project, session=SID) == ALLOW

    def test_promoted_grep_root_covers_subtree(self, home, project):
        root = home / "srctree"
        run_hook(home, tool="Grep", path=root, cwd=project)
        run_record(home, tool="Grep", path=root, cwd=project)
        assert check(home, root / "deep" / "f.py", "r", project, session=SID) == ALLOW
        assert check(home, home / "sibling", "r", project, session=SID) == ASK

    def test_forged_record_without_pending_is_noop(self, home, project):
        f = seed_session(home)  # empty pending
        run_record(home, tool="Read", path=home / "loot.txt", cwd=project)
        data = json.loads(f.read_text())
        assert data["granted"] == [] and data["pending"] == []

    def test_bypass_mode_never_promotes(self, home, project):
        target = home / "f.txt"
        f = seed_session(home, pending=[{"path": str(target), "mode": "r"}])
        run_record(home, tool="Read", path=target, cwd=project, mode="bypassPermissions")
        assert json.loads(f.read_text())["granted"] == []

    def test_durable_grant_blocks_promotion(self, home, project):
        # revoke immediacy: execution under a durable grant proves nothing
        target = home / "covered" / "f.txt"
        seed_session(home, pending=[{"path": str(target), "mode": "r"}])
        cli(home, "grant", str(home / "covered"), "r", "--global")
        run_record(home, tool="Read", path=target, cwd=project)
        assert json.loads(session_file(home).read_text())["granted"] == []
        cli(home, "revoke", str(home / "covered"), "--global")
        assert check(home, target, "r", project, session=SID) == ASK

    def test_denied_prompt_stays_pending_forever(self, home, project):
        run_hook(home, tool="Read", path=home / "no.txt", cwd=project)
        # no record call (tool never executed) — pending remains, granted empty
        data = json.loads(session_file(home).read_text())
        assert data["granted"] == []
        assert len(data["pending"]) == 1

    def test_mode_mismatch_does_not_promote(self, home, project):
        target = home / "f"
        seed_session(home, pending=[{"path": str(target), "mode": "r"}])
        run_record(home, tool="Write", path=target, cwd=project)  # w executed, r pending
        assert json.loads(session_file(home).read_text())["granted"] == []

    def test_sweep_deletes_stale_session_files(self, home, project):
        sdir = home / ".claude" / "hooks" / "path-gate-sessions"
        sdir.mkdir(parents=True, exist_ok=True)
        stale = sdir / "old-session.json"
        stale.write_text('{"pending":[],"granted":[]}')
        old = time.time() - 8 * 86400
        os.utime(stale, (old, old))
        target = home / "x"
        seed_session(home, pending=[{"path": str(target), "mode": "r"}])
        run_record(home, tool="Read", path=target, cwd=project)
        assert not stale.exists()

    def test_corrupt_session_file_is_inert(self, home, project):
        f = seed_session(home)
        f.write_text("{corrupt")
        run_record(home, tool="Read", path=home / "x", cwd=project)  # must not crash
        assert check(home, home / "x", "r", project, session=SID) == ASK


class TestConcurrency:
    def test_parallel_pendings_never_mint_grants(self, home, project):
        import concurrent.futures as cf

        paths = [home / f"c{i}.txt" for i in range(8)]
        with cf.ThreadPoolExecutor(8) as ex:
            list(ex.map(lambda p: run_hook(home, tool="Read", path=p, cwd=project), paths))
        data = json.loads(session_file(home).read_text())
        assert data["granted"] == []  # racing pendings may drop; grants never appear
        assert len(data["pending"]) >= 1
