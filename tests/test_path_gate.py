"""path-gate v2. Drives the hook scripts as subprocesses with a fake $HOME.

The gate's decision chain is tested through the `check` verb (exit codes:
0=allow, 1=ask, 2=deny). Hook mode (stdin JSON) and the record verb are
tested in their own sections.

Two roots carry the whole suite, and neither hardcodes a platform:

- TMP_ROOT is the OS shared scratch the gate allows, spelled the way the
  filesystem spells it. pytest's own tmp_path is NOT usable as a foreign path,
  because it lands inside TMP_ROOT on Linux.
- SANDBOX is a repo-relative root the gate must call foreign. test_sandbox_is_foreign
  asserts that, so a platform where it stops holding fails once and legibly.
"""

from __future__ import annotations

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

    def test_dotdot_through_symlink_does_not_escape(self, home, project, sandbox):
        outside = sandbox / "outside" / "nest"
        outside.mkdir(parents=True)
        (outside.parent / "grab_existing").write_text("secret")
        link = project / "mylink"
        link.symlink_to(outside)
        # existing target one level above the symlink's target dir
        assert check(home, link / ".." / "grab_existing", "r", project) == ASK
        # nonexistent target, same climb
        assert check(home, link / ".." / "grab_missing", "w", project) == ASK

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


# A case-insensitive volume (APFS/HFS+ default) lands a write spelled in any case
# on the real store, and realpath can only canonicalize names that already exist,
# so the store's absence (every fresh machine) is the state that matters. The
# gate must deny on the spelling alone, which also keeps these tests independent
# of the host filesystem. project == home puts the stores inside the workspace:
# a variant that slipped the carve-out would read as ALLOW, not merely ASK.
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


class TestStoreSpellings:
    @pytest.mark.parametrize("spelling", CASE_VARIANTS)
    @pytest.mark.parametrize("present", [False, True], ids=["absent", "present"])
    @pytest.mark.parametrize("store", STORE_NAMES)
    def test_case_variant_write_denied(self, home, store, present, spelling):
        if present:
            materialize(home, store)
        assert check(home, store_target(home, store, spelling), "w", home) == DENY

    @pytest.mark.parametrize("spelling", ["upper", "mixed"])
    @pytest.mark.parametrize("store", STORE_NAMES)
    def test_case_variant_read_asks(self, home, store, spelling):
        assert check(home, store_target(home, store, spelling), "r", home) == ASK

    def test_case_variant_of_parent_dirs_denied(self, home):
        """Nothing under ~/.claude exists yet: every component keeps the
        caller's spelling, not just the store's own name."""
        shutil.rmtree(home / ".claude")
        target = home / ".CLAUDE" / "Hooks" / "PATH-GATE-GRANTS.JSON"
        assert check(home, target, "w", home) == DENY

    @pytest.mark.parametrize(
        "name",
        [
            "path-gate-seßions",  # ß folds to ss (full case folding)
            "path-gate-ſessions",  # ſ (long s) folds to s
            "path-gate-grantſ.json",
        ],
    )
    def test_unicode_fold_variant_write_denied(self, home, name):
        """APFS folds case with full Unicode rules, so non-ASCII spellings alias
        the ASCII store names too."""
        assert check(home, home / ".claude" / "hooks" / name, "w", home) == DENY

    @pytest.mark.parametrize(
        "name",
        [
            "path-gate-grants.json.bak",  # sibling, not inside the store
            "path-gate-grants",
            "path-gate-sessions.json",
            "café.txt",  # non-ASCII that cannot spell a store name
            "path.gate.grants.jsoné",  # '.' is literal, not any character
            "工作",  # non-ASCII alone cannot spell a store name's '-' or '.'
        ],
    )
    def test_non_store_neighbors_still_allowed(self, home, name):
        assert check(home, home / ".claude" / "hooks" / name, "w", home) == ALLOW

    def test_non_ascii_workspace_file_allowed(self, home, project):
        assert check(home, project / "résumé.md", "w", project) == ALLOW

    # The store match must stay confined to the store's own depth: a non-ASCII
    # name anywhere above or beside the stores is an ordinary path.
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

    # HOME itself may carry glob metacharacters. A non-ASCII byte in the same
    # component sends it through the glob comparison, where each metacharacter
    # must stay literal: the store is denied under its own spelling, and a
    # sibling that the unescaped pattern would match is not a store.
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
            assert check(h, store_target(h, "grants", spelling), "w", h) == DENY
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

    def test_longest_real_name_at_store_depth_decides_fast(self, home):
        """255 characters is every volume's NAME_MAX; non-ASCII takes the
        per-byte fold, the slow path."""
        path = home / ".claude" / "hooks" / ("é" * 255)
        rc, dt = self.timed_check(home, path, home)
        assert rc == ALLOW
        assert dt < self.BOUND_S, f"{dt:.2f}s"

    def test_impossible_name_at_store_depth_denied_fast(self, home):
        """Past any NAME_MAX no volume creates the name, so the fold is skipped
        and the name counts as a possible store alias: fail closed, for free."""
        path = home / ".claude" / "hooks" / ("é" * 2000)
        rc, dt = self.timed_check(home, path, home)
        assert rc == DENY
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
        assert "cannot be resolved" in r.stdout

    def test_unresolvable_write_denied_in_hook_mode(self, home, project):
        (project / "a").symlink_to("a")
        out = run_hook(home, tool="Write", path=project / "a", cwd=project)
        assert out["decision"] == "deny"
        assert "cannot be resolved" in out["reason"]

    def test_unresolvable_read_asks_without_pending(self, home, project):
        (project / "a").symlink_to("a")
        out = run_hook(home, tool="Read", path=project / "a", cwd=project)
        assert out["decision"] == "ask"
        assert not session_file(home).exists()

    def test_grant_refuses_unresolvable_path(self, home, project):
        (project / "a").symlink_to("a")
        r = cli(home, "grant", project / "a", "w", "--global")
        assert r.returncode == 3
        assert "cannot resolve" in r.stderr
        assert not (home / ".claude" / "hooks" / "path-gate-grants.json").exists()


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
        assert "session" in out["reason"]
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
