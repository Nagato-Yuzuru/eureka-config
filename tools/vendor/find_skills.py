# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fork of the find-skills skill (vercel-labs/skills). It calls `npx skills`, which fetches past
the 7-day release cooldown and which block-package-managers denies; the skills CLI is
installed by mise (packages.toml), so the fork calls it by name. Keeps the layout `skills add`
makes: the file in ~/.agents/skills, which other agents read, and ~/.claude/skills/find-skills
a link to it (dot_claude/skills/symlink_find-skills). `skills update` may overwrite the
file; the next `chezmoi apply` restores the fork."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

from _upstream import NPX, REPO_ROOT, GitHub, UpstreamChanged, converge, forbid, replace

REPO, BRANCH = "vercel-labs/skills", "main"
PATH, TARGET = "skills/find-skills/SKILL.md", "dot_agents/skills/find-skills/SKILL.md"


def main() -> None:
    gh = GitHub(os.environ.get("GITHUB_TOKEN"))
    commit = gh.cooled_commit(REPO, BRANCH, datetime.now(UTC))
    original = gh.fetch(commit, PATH)
    try:
        text = replace(original, "npx skills", "skills")
        forbid(text, NPX)
    except UpstreamChanged as e:
        sys.exit(f"find_skills: {PATH}: {e}")
    originals_dir = Path(d) if (d := os.environ.get("VENDOR_ORIGINALS")) else None
    ref = Path(__file__).with_name("find_skills.ref")
    for p in converge(ref, commit, {REPO_ROOT / TARGET: text}, {PATH: original}, originals_dir):
        sys.stderr.write(f"find_skills: wrote {p.relative_to(REPO_ROOT)}\n")


if __name__ == "__main__":
    main()
