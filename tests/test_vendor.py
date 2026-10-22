"""Executable spec for tools/vendor/: the upstream scripts' shared helpers and the guard they
all run their output through.

- `replace` rewrites every occurrence and fails when upstream no longer has the text.
- The scripts' NPX pattern catches npm/npx in command position and nothing inside a word.
- `converge` writes only changed targets, moves the ref only with them, compares bytes (so a
  CRLF upstream converges too), and leaves the originals for the PR body.
- jbcontext maps a CLI build to its release tag, adopts a release only past its cooldown, moves
  the packages.toml pin and nothing else, and refuses a skill that teaches any subcommand but
  `search`.

The scripts themselves need the network; the workflow runs them.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import _upstream
import jbcontext
import pytest
from _upstream import Commit, Release, UpstreamChanged, converge, forbid, replace

VENDOR = Path(__file__).resolve().parent.parent / "tools" / "vendor"

# --- replace and forbid ------------------------------------------------------------------------


def test_replace_rewrites_every_occurrence():
    assert replace("npx a; npx b", "npx ", "") == "a; b"


def test_replace_fails_when_upstream_no_longer_has_the_text():
    with pytest.raises(UpstreamChanged, match=r"^'npx ctx7@latest' no longer occurs$"):
        replace("ctx7 library x", "npx ctx7@latest", "ctx7")


@pytest.mark.parametrize(
    "text",
    ["npx x", "run `npx x`", "(npm run x)", "a npm i", "run npx"],
)
def test_npx_pattern_catches_npm_and_npx_in_command_position(text: str):
    with pytest.raises(UpstreamChanged, match="still matches"):
        forbid(text, _upstream.NPX)


@pytest.mark.parametrize("text", ["pnpm dlx x", "npm-run-all"])
def test_npx_pattern_passes_what_the_hook_passes(text: str):
    forbid(text, _upstream.NPX)


def test_forbid_names_the_line_and_the_match():
    with pytest.raises(UpstreamChanged, match=r"^line 3 still matches .*: 'npx x'$"):
        forbid("a\nb\nnpx x\n", _upstream.NPX)


# --- converge ----------------------------------------------------------------------------------

C1 = Commit(repo="o/r", sha="c1", date=datetime(2026, 9, 1, tzinfo=UTC))
C2 = Commit(repo="o/r", sha="c2", date=datetime(2026, 9, 8, tzinfo=UTC))


def run(tmp_path: Path, commit: Commit, text: str, originals_dir: Path | None = None):
    target = tmp_path / "src" / "dot_x" / "a.md"
    ref = tmp_path / "up.ref"
    return (
        converge(ref, commit, {target: text}, {"u/a.md": f"orig {text}"}, originals_dir),
        target,
        ref,
    )


def test_first_run_writes_the_target_and_records_the_commit(tmp_path: Path):
    changed, target, ref = run(tmp_path, C1, "A")
    assert changed == [target]
    assert target.read_text() == "A"
    assert ref.read_text() == "o/r c1 2026-09-01T00:00:00Z\n"


def test_a_new_commit_with_the_same_output_changes_nothing(tmp_path: Path):
    run(tmp_path, C1, "A")
    changed, _, ref = run(tmp_path, C2, "A")
    assert changed == []
    assert ref.read_text().split()[1] == "c1"


def test_a_changed_output_moves_the_ref_with_it(tmp_path: Path):
    run(tmp_path, C1, "A")
    changed, target, ref = run(tmp_path, C2, "B")
    assert changed == [target]
    assert (target.read_text(), ref.read_text().split()[1]) == ("B", "c2")


def test_a_missing_ref_is_recorded_even_when_the_targets_already_match(tmp_path: Path):
    _, _, ref = run(tmp_path, C1, "A")
    ref.unlink()
    changed, _, ref = run(tmp_path, C2, "A")
    assert changed == []
    assert ref.read_text().split()[1] == "c2"


def test_crlf_output_converges(tmp_path: Path):
    run(tmp_path, C1, "a\r\nb\r\n")
    changed, target, _ = run(tmp_path, C2, "a\r\nb\r\n")
    assert changed == []
    assert target.read_bytes() == b"a\r\nb\r\n"


def test_originals_land_under_the_scripts_name(tmp_path: Path):
    run(tmp_path, C1, "A", originals_dir=tmp_path / "orig")
    assert (tmp_path / "orig" / "up" / "u" / "a.md").read_text() == "orig A"


# --- jbcontext ---------------------------------------------------------------------------------


def test_a_cli_build_maps_to_its_release_tag():
    assert jbcontext.tag_of("0.9.17.1213") == "v0.9.17"


@pytest.mark.parametrize("version", ["0.9.17", "<html>302 Found</html>", "1.0.0.12-eap"])
def test_a_version_txt_that_is_no_build_number_fails(version: str):
    with pytest.raises(UpstreamChanged, match="not <major>.<minor>.<patch>.<build>"):
        jbcontext.tag_of(version)


@pytest.mark.parametrize(
    ("age", "cooled"),
    [
        (timedelta(days=2) - timedelta(seconds=1), False),
        (timedelta(days=2), True),
    ],
)
def test_a_release_is_adopted_once_the_cooldown_has_passed(age: timedelta, cooled: bool):
    now = datetime(2026, 10, 21, 4, 23, tzinfo=UTC)
    release = Release(tag="v0.9.17", published=now - age, notes="")
    assert jbcontext.cooled(release, now) is cooled


@pytest.mark.parametrize(
    "line",
    ['jbcontext search "<query>"', "Use `jbcontext search`"],
)
def test_the_skill_may_teach_jbcontext_search(line: str):
    forbid(line, jbcontext.OTHER_SUBCOMMAND)


@pytest.mark.parametrize(
    "line", ["jbcontext index", "run `jbcontext  repos x`", "jbcontext searches"]
)
def test_the_skill_may_not_teach_another_subcommand(line: str):
    with pytest.raises(UpstreamChanged, match="still matches"):
        forbid(line, jbcontext.OTHER_SUBCOMMAND)


PACKAGES = """\
[packages.uv]
upstream = { command = "i", creates = "$HOME/uv" }

[packages.jbcontext.upstream]
version = "0.9.16.1100"
command = "i {version}"
"""


def test_the_pin_moves_and_nothing_else_does():
    moved = jbcontext.pin(PACKAGES, "0.9.17.1213")
    assert moved == PACKAGES.replace('version = "0.9.16.1100"', 'version = "0.9.17.1213"')
    assert moved != PACKAGES


def test_a_pin_line_shared_with_another_package_is_refused():
    shared = PACKAGES + '\n[packages.other.upstream]\nversion = "0.9.16.1100"\n'
    with pytest.raises(UpstreamChanged, match="occurs 2 times in packages.toml, not once"):
        jbcontext.pin(shared, "0.9.17.1213")


def test_helpers_take_the_ref_from_the_caller(tmp_path: Path):
    """converge must not derive the ref from its own location: a test (or a script elsewhere)
    would then write into tools/vendor/."""
    before = sorted(p.name for p in VENDOR.glob("*.ref"))
    run(tmp_path, C1, "A")
    assert sorted(p.name for p in VENDOR.glob("*.ref")) == before
    assert VENDOR.parent.parent == _upstream.REPO_ROOT
