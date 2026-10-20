"""Executable spec for tools/upstream-watch.sh, the weekly diff of a watched upstream into an
issue. Upstream is a local git repository; gh is a fake that answers the issue queries the way
`--jq` would and records every write.

- without a reviewed point (no open issue, no marker on a closed one) it fails and writes nothing
- with nothing changed under PATHSPEC since the reviewed point it writes nothing
- changes since the newest closed issue's head open an issue whose marker carries that head as
  base and the upstream HEAD as head, listing only the commits under PATHSPEC
- an open issue is edited in place and keeps its own base
- PATHSPEC takes several pathspecs, `:!x` excluding; the summary sizes each included one
- TRACK=releases reviews at the newest v* tag, not HEAD, and carries the notes of the releases
  past the reviewed point
"""

from __future__ import annotations

import itertools
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "upstream-watch.sh"
PATHSPEC = "skills/principle-*/SKILL.md"

FAKE_GH = """#!/usr/bin/env bash
echo "$*" >> "$FAKE_LOG"
case "$1 $2" in
  "issue list")
    if [[ "$*" == *"--state open"* ]]; then printf '%s\\n' "$FAKE_OPEN"
    else printf '%s\\n' "$FAKE_CLOSED_BODY"; fi ;;
  "issue view") printf '%s\\n' "$FAKE_OPEN_BODY" ;;
  "release view") printf 'notes for %s\\n' "$3" ;;
  *) for ((i = 1; i <= $#; i++)); do
       if [[ "${!i}" == --body-file ]]; then j=$((i + 1)); cp "${!j}" "$FAKE_BODY"; fi
     done ;;
esac
"""


@dataclass(frozen=True, slots=True, kw_only=True)
class Upstream:
    path: Path

    def git(self, *args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(self.path), *args], check=True, capture_output=True, text=True
        ).stdout.strip()

    def commit(self, rel: str, text: str, message: str) -> str:
        f = self.path / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def tag(self, name: str) -> None:
        self.git("tag", name)


@dataclass(frozen=True, slots=True, kw_only=True)
class Run:
    proc: subprocess.CompletedProcess[str]
    writes: list[str]
    body: str | None


@pytest.fixture
def upstream(tmp_path: Path) -> Upstream:
    up = Upstream(path=tmp_path / "upstream")
    up.path.mkdir()
    up.git("init", "-q")
    up.git("config", "user.email", "t@example.com")
    up.git("config", "user.name", "t")
    up.git("config", "commit.gpgsign", "false")
    return up


def marker(base: str | None = None, head: str | None = None) -> str:
    keys = (f"base={base}" if base else "", f"head={head}" if head else "")
    return f"text\n<!-- upstream-watch {' '.join(k for k in keys if k)} -->\nmore"


def watch(
    tmp_path: Path,
    up: Upstream,
    *,
    open_issue: str = "",
    open_body: str = "",
    closed_body: str = "",
    pathspec: str = PATHSPEC,
    track: str | None = None,
) -> Run:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    gh.write_text(FAKE_GH)
    gh.chmod(0o755)
    log, body = tmp_path / "gh.log", tmp_path / "body.md"
    env = (
        os.environ
        | {
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "UPSTREAM": "o/r",
            "PATHSPEC": pathspec,
            "LABEL": "upstream-watch",
            "FAKE_LOG": str(log),
            "FAKE_BODY": str(body),
            "FAKE_OPEN": open_issue,
            "FAKE_OPEN_BODY": open_body,
            "FAKE_CLOSED_BODY": closed_body,
        }
        | ({"TRACK": track} if track else {})
    )
    proc = subprocess.run(
        ["bash", str(SCRIPT), str(up.path)], capture_output=True, text=True, env=env
    )
    calls = log.read_text().splitlines() if log.exists() else []
    writes = [c for c in calls if not c.startswith(("issue list", "issue view", "release view"))]
    return Run(proc=proc, writes=writes, body=body.read_text() if body.exists() else None)


def section(body: str, title: str) -> list[str]:
    """The non-blank lines under `## <title>`, up to the next `## ` heading or the diff."""
    lines = body.split(f"## {title}\n", 1)[1].splitlines()
    return [
        ln
        for ln in itertools.takewhile(lambda ln: not ln.startswith(("## ", "<details>")), lines)
        if ln
    ]


def commits(body: str) -> list[str]:
    return [line.split(maxsplit=3)[3] for line in section(body, "Commits")]


def test_without_a_reviewed_point_it_fails_and_writes_nothing(tmp_path: Path, upstream: Upstream):
    upstream.commit("skills/principle-a/SKILL.md", "a1\n", "add a")
    run = watch(tmp_path, upstream, closed_body="closed without a marker")
    assert run.proc.returncode == 1
    assert "no reviewed point" in run.proc.stdout
    assert run.writes == []


def test_changes_outside_the_pathspec_write_nothing(tmp_path: Path, upstream: Upstream):
    reviewed = upstream.commit("skills/principle-a/SKILL.md", "a1\n", "add a")
    upstream.commit("skills/other/SKILL.md", "o1\n", "add other")
    run = watch(tmp_path, upstream, closed_body=marker(base="1" * 40, head=reviewed))
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.writes == []


def test_changes_since_the_closed_head_open_an_issue(tmp_path: Path, upstream: Upstream):
    reviewed = upstream.commit("skills/principle-a/SKILL.md", "a1\n", "add a")
    upstream.commit("skills/principle-a/SKILL.md", "a2\n", "reword a")
    upstream.commit("skills/other/SKILL.md", "o1\n", "add other")
    tip = upstream.commit("skills/principle-b/SKILL.md", "b1\n", "add b")
    run = watch(tmp_path, upstream, closed_body=marker(base="1" * 40, head=reviewed))
    assert run.proc.returncode == 0, run.proc.stderr
    assert [w.split()[:2] for w in run.writes] == [["label", "create"], ["issue", "create"]]
    assert run.body is not None
    assert f"<!-- upstream-watch base={reviewed} head={tip} -->" in run.body
    assert commits(run.body) == ["add b", "reword a"]
    assert "-a1\n+a2" in run.body
    assert "+++ b/skills/principle-b/SKILL.md" in run.body


def test_an_open_issue_is_edited_and_keeps_its_base(tmp_path: Path, upstream: Upstream):
    first = upstream.commit("skills/principle-a/SKILL.md", "a1\n", "add a")
    reviewed = upstream.commit("skills/principle-a/SKILL.md", "a2\n", "reword a")
    tip = upstream.commit("skills/principle-a/SKILL.md", "a3\n", "reword a again")
    run = watch(
        tmp_path,
        upstream,
        open_issue="7",
        open_body=marker(base=first, head=reviewed),
        closed_body=marker(head=reviewed),
    )
    assert run.proc.returncode == 0, run.proc.stderr
    assert [w.split()[:3] for w in run.writes] == [["issue", "edit", "7"]]
    assert run.body is not None
    assert f"<!-- upstream-watch base={first} head={tip} -->" in run.body
    assert "-a1\n+a3" in run.body


def test_excluded_paths_count_neither_as_change_nor_in_the_summary(
    tmp_path: Path, upstream: Upstream
):
    reviewed = upstream.commit("skills/a/SKILL.md", "a1\n", "add a")
    upstream.commit("skills/vendored/SKILL.md", "v1\n", "add vendored")
    upstream.commit("hooks/h.sh", "h1\nh2\n", "add hook")
    upstream.commit("skills/a/SKILL.md", "a2\n", "reword a")
    run = watch(
        tmp_path,
        upstream,
        closed_body=marker(head=reviewed),
        pathspec="skills hooks :!skills/vendored",
    )
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.body is not None
    assert section(run.body, "Summary") == [
        "- `skills`: 1 file changed, 1 insertion(+), 1 deletion(-)",
        "- `hooks`: 1 file changed, 2 insertions(+)",
    ]
    assert commits(run.body) == ["reword a", "add hook"]
    assert "vendored" not in run.body.split("## Summary", 1)[1]


def test_only_excluded_changes_write_nothing(tmp_path: Path, upstream: Upstream):
    reviewed = upstream.commit("skills/a/SKILL.md", "a1\n", "add a")
    upstream.commit("skills/vendored/SKILL.md", "v1\n", "add vendored")
    run = watch(
        tmp_path, upstream, closed_body=marker(head=reviewed), pathspec="skills :!skills/vendored"
    )
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.writes == []


def test_releases_are_reviewed_at_the_newest_tag_with_their_notes(
    tmp_path: Path, upstream: Upstream
):
    reviewed = upstream.commit("mcp/tool.txt", "t1\n", "add tool")
    upstream.tag("v0.9.9")
    upstream.commit("mcp/tool.txt", "t2\n", "reword tool")
    upstream.tag("v0.9.10")
    released = upstream.commit("mcp/tool.txt", "t3\n", "reword tool again")
    upstream.tag("v0.9.11")
    upstream.commit("mcp/tool.txt", "t4\n", "unreleased")
    run = watch(
        tmp_path, upstream, closed_body=marker(head=reviewed), pathspec="mcp", track="releases"
    )
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.body is not None
    assert f"<!-- upstream-watch base={reviewed} head={released} -->" in run.body
    assert commits(run.body) == ["reword tool again", "reword tool"]
    notes = [ln for ln in section(run.body, "Summary") if not ln.startswith("- ")]
    assert notes == ["### v0.9.11", "notes for v0.9.11", "### v0.9.10", "notes for v0.9.10"]
    assert "+t3" in run.body and "t4" not in run.body


def test_unreleased_changes_alone_write_nothing(tmp_path: Path, upstream: Upstream):
    reviewed = upstream.commit("mcp/tool.txt", "t1\n", "add tool")
    upstream.tag("v0.9.9")
    upstream.commit("mcp/tool.txt", "t2\n", "unreleased")
    run = watch(
        tmp_path, upstream, closed_body=marker(head=reviewed), pathspec="mcp", track="releases"
    )
    assert run.proc.returncode == 0, run.proc.stderr
    assert run.writes == []
