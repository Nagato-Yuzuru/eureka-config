# /// script
# requires-python = ">=3.12"
# dependencies = [
#     "ruff>=0.16.4",
# ]
# ///
"""Diff-scoped ruff: report findings only on lines added since <base-rev>.

Usage:
    uv run lint-diff.py <base-rev> [--config RUFF_TOML]

Exit 1 if any finding lands on an added line; 0 otherwise.
"""

import argparse
import json
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@")


def repo_root() -> Path:
    out = subprocess.run(
        ["git", "rev-parse", "--show-toplevel"],
        capture_output=True,
        text=True,
        check=True,
    )
    return Path(out.stdout.strip())


def added_lines(base: str, root: Path) -> dict[str, set[int]]:
    """Map repo-relative .py path -> set of line numbers added since base."""
    out = subprocess.run(
        ["git", "diff", "-U0", "--diff-filter=ACMR", base, "--", "*.py"],
        capture_output=True,
        text=True,
        check=True,
        cwd=root,
    )
    lines: dict[str, set[int]] = defaultdict(set)
    current: str | None = None
    for raw in out.stdout.splitlines():
        if raw.startswith("+++ b/"):
            current = raw[6:]
        elif current is not None and (m := HUNK.match(raw)):
            start = int(m.group(1))
            count = 1 if m.group(2) is None else int(m.group(2))
            lines[current].update(range(start, start + count))
    return {p: rows for p, rows in lines.items() if rows}


def ruff_findings(files: list[str], config: str | None, root: Path) -> list[dict]:
    cmd = ["ruff", "check", "--output-format=json", "--exit-zero", "--force-exclude"]
    if config:
        cmd += ["--config", config]
    proc = subprocess.run(cmd + files, capture_output=True, text=True, cwd=root)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        sys.exit(2)
    return json.loads(proc.stdout)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("base", help="git rev to diff against (e.g. origin/main)")
    parser.add_argument("--config", help="ruff config for repos that have none")
    args = parser.parse_args()

    root = repo_root()
    changed = added_lines(args.base, root)
    if not changed:
        print("lint-diff: no added python lines since", args.base)
        return 0

    hits = []
    for f in ruff_findings(sorted(changed), args.config, root):
        rel = str(Path(f["filename"]).resolve().relative_to(root))
        row = f["location"]["row"]
        if row in changed.get(rel, ()):
            hits.append(f"{rel}:{row}:{f['location']['column']}: {f['code']} {f['message']}")

    for hit in hits:
        print(hit)
    print(f"lint-diff: {len(hits)} finding(s) on added lines (base {args.base})")
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
