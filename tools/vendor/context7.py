# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fork of Context7's CLI skill and rule (upstash/context7). They call `npx ctx7@latest`, which
fetches past the 7-day release cooldown and which block-package-managers denies; ctx7 is
installed by mise (packages.toml), so the fork calls it by name. Replaces `ctx7 setup`, which
must not run again: it would rewrite the targets in place."""

from __future__ import annotations

import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from _upstream import REPO_ROOT, GitHub, UpstreamChanged, converge, forbid, replace

REPO, BRANCH = "upstash/context7", "master"
NPX = r"(^|[\s`(;&|])np[mx]([\s;&|)`]|$)"  # npm/npx where block-package-managers.sh sees a command


def skill(text: str) -> str:
    text = replace(
        text,
        "Run commands with `npx ctx7@latest` so setup always uses the latest CLI"
        " without a global install:",
        "The `ctx7` CLI is installed; run it by name:",
    )
    text = replace(
        text,
        "Optionally install globally if you prefer a bare `ctx7` command:\n\n"
        "```bash\nnpm install -g ctx7@latest\n```\n\n",
        "",
    )
    return replace(text, "npx ctx7@latest", "ctx7")


def rule(text: str) -> str:
    return replace(text, "npx ctx7@latest", "ctx7")


# upstream path -> (target in the chezmoi source, rewrite)
FILES: dict[str, tuple[str, Callable[[str], str]]] = {
    "skills/find-docs/SKILL.md": ("dot_claude/skills/find-docs/SKILL.md", skill),
    "rules/context7-cli.md": ("dot_claude/rules/context7.md", rule),
}


def main() -> None:
    gh = GitHub(os.environ.get("GITHUB_TOKEN"))
    commit = gh.cooled_commit(REPO, BRANCH, datetime.now(UTC))
    originals = {path: gh.fetch(commit, path) for path in FILES}
    targets: dict[Path, str] = {}
    for path, (target, rewrite) in FILES.items():
        try:
            text = rewrite(originals[path])
            forbid(text, NPX)
        except UpstreamChanged as e:
            sys.exit(f"context7: {path}: {e}")
        targets[REPO_ROOT / target] = text
    originals_dir = Path(d) if (d := os.environ.get("VENDOR_ORIGINALS")) else None
    for p in converge(
        Path(__file__).with_name("context7.ref"), commit, targets, originals, originals_dir
    ):
        sys.stderr.write(f"context7: wrote {p.relative_to(REPO_ROOT)}\n")


if __name__ == "__main__":
    main()
