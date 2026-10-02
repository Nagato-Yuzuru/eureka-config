"""Helpers for the upstream scripts beside this file. Each `tools/vendor/<name>.py` forks the
files of one upstream into the chezmoi source, is a PEP 723 script declaring its own
dependencies, and may use these helpers or not. What it owes .github/workflows/vendor-sync.yml:

- it runs as `uv run --script tools/vendor/<name>.py`, from any directory;
- it converges its targets: writes them only when their content changes, and then records
  the upstream commit in `tools/vendor/<name>.ref` as `<repo> <sha> <date>`;
- when $VENDOR_ORIGINALS is set, it leaves the upstream files it read under
  $VENDOR_ORIGINALS/<name>/<upstream path>, which the PR body shows in full;
- when upstream no longer fits its rewrites, it exits non-zero saying which one broke.
"""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
COOLDOWN = timedelta(days=7)  # the release cooldown pnpm, uv and mise are configured with


class UpstreamChanged(Exception):
    """Upstream no longer reads the way a script's rewrites expect."""


@dataclass(frozen=True, slots=True, kw_only=True)
class Commit:
    repo: str
    sha: str
    date: datetime


class GitHub:
    def __init__(self, token: str | None) -> None:
        self._headers = {"User-Agent": "vendor-sync", "Accept": "application/vnd.github+json"}
        if token:
            self._headers["Authorization"] = f"Bearer {token}"

    def _get(self, url: str) -> bytes:
        req = urllib.request.Request(url, headers=self._headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.read()

    def cooled_commit(self, repo: str, branch: str, now: datetime) -> Commit:
        """The newest commit on `branch` at least COOLDOWN older than `now`."""
        until = (now - COOLDOWN).astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        query = urllib.parse.urlencode({"sha": branch, "until": until, "per_page": 1})
        commits = json.loads(self._get(f"https://api.github.com/repos/{repo}/commits?{query}"))
        if not commits:
            raise UpstreamChanged(f"{repo}@{branch} has no commit before {until}")
        c = commits[0]
        date = datetime.fromisoformat(c["commit"]["committer"]["date"])
        return Commit(repo=repo, sha=c["sha"], date=date)

    def fetch(self, commit: Commit, path: str) -> str:
        quoted = urllib.parse.quote(path)
        return self._get(
            f"https://raw.githubusercontent.com/{commit.repo}/{commit.sha}/{quoted}"
        ).decode()


def replace(text: str, old: str, new: str) -> str:
    """Every `old` in `text` replaced; `old` must occur, or upstream has reworded it."""
    if old not in text:
        raise UpstreamChanged(f"{old!r} no longer occurs")
    return text.replace(old, new)


def forbid(text: str, pattern: str) -> None:
    """Fail when `pattern` (a regex, multiline) still matches after the rewrites."""
    if m := re.search(pattern, text, re.MULTILINE):
        # Locate by the match's last character: a leading separator group can start the
        # match on the previous line's newline.
        end = max(m.end() - 1, m.start())
        start = text.rfind("\n", 0, end) + 1
        stop = text.find("\n", end)
        line = text[start : None if stop == -1 else stop]
        n = text.count("\n", 0, start) + 1
        raise UpstreamChanged(f"line {n} still matches {pattern!r}: {line.strip()!r}")


def converge(
    ref: Path,
    commit: Commit,
    targets: Mapping[Path, str],
    originals: Mapping[str, str],
    originals_dir: Path | None,
) -> list[Path]:
    """Bring `targets` to their content and return the ones that changed. `ref` is the
    script's `<name>.ref`; the originals go under `originals_dir`/<name>. The ref moves only
    with a changed target (or when it is missing), so a new upstream commit that leaves the
    output as it was changes nothing. The ref is written last: a run that dies partway leaves
    the old ref, and the next run finishes the job."""
    if originals_dir:
        for path, text in originals.items():
            out = originals_dir / ref.stem / path
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(text.encode())
    changed = [
        p for p, text in targets.items() if not p.is_file() or p.read_bytes() != text.encode()
    ]
    for p in changed:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(targets[p].encode())
    if changed or not ref.is_file():
        ref.write_text(f"{commit.repo} {commit.sha} {commit.date:%Y-%m-%dT%H:%M:%SZ}\n")
    return changed
