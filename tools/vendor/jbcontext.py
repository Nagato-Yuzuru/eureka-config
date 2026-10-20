# /// script
# requires-python = ">=3.12"
# dependencies = []
# ///
"""Fork of the part of Air Context's harness we use (JetBrains/context): the context-search skill,
and the CLI version it is used with. CLI and prompts release together — release vX.Y.Z tags the
prompt repo, the binary is X.Y.Z.<build> — so one version moves both: the jbcontext pin in
.chezmoidata/packages.toml, which the install script installs, and the skill at that release's
tag (plugin.json's version follows the pin, for /plugin to show). Only the newest build is
published by number (version.txt), so a release still inside the cooldown is skipped, not
queued: the pin stays until a newer run finds one past it.

The rest of upstream's harness (instructions, hooks, subagents) is not adopted; upstream-watch
reports its changes."""

from __future__ import annotations

import json
import os
import re
import sys
import tomllib
import urllib.error
import urllib.request
from datetime import UTC, datetime, timedelta
from pathlib import Path

from _upstream import REPO_ROOT, GitHub, Release, UpstreamChanged, converge, forbid

REPO = "JetBrains/context"
VERSION_URL = "https://download.jetbrains.com/jetbrains-context/release/version.txt"
# Releases come about weekly; the 7-day cooldown the others use would rarely let one through.
COOLDOWN = timedelta(days=2)
PACKAGES = REPO_ROOT / ".chezmoidata" / "packages.toml"
PLUGIN = REPO_ROOT / "claude" / "mods" / "jbcontext"
SKILL = "skills/context-search/SKILL.md"
# Settings allow `jbcontext search` alone; any other subcommand the skill teaches would prompt,
# and `index` uploads code.
OTHER_SUBCOMMAND = r"jbcontext\s+(?!search\b)[a-z]"
DESCRIPTION = "Air Context semantic code search: the context-search skill over the jbcontext CLI"


def tag_of(version: str) -> str:
    """The release tag of CLI build `version`: 0.9.17.1213 -> v0.9.17."""
    if not re.fullmatch(r"\d+\.\d+\.\d+\.\d+", version):
        raise UpstreamChanged(f"version.txt reads {version!r}, not <major>.<minor>.<patch>.<build>")
    return "v" + version.rsplit(".", 1)[0]


def cooled(release: Release, now: datetime) -> bool:
    return now - release.published >= COOLDOWN


def manifest(version: str) -> str:
    plugin = {"name": "jbcontext", "version": version, "description": DESCRIPTION}
    return json.dumps(plugin, indent=2) + "\n"


def pin(packages: str, version: str) -> str:
    """packages.toml with the jbcontext pin moved to `version`, every other byte as it was."""
    old = tomllib.loads(packages)["packages"]["jbcontext"]["upstream"]["version"]
    line = f'version = "{old}"'
    if (n := packages.count(line)) != 1:
        raise UpstreamChanged(f"{line!r} occurs {n} times in packages.toml, not once")
    return packages.replace(line, f'version = "{version}"')


def latest_version() -> str:
    req = urllib.request.Request(VERSION_URL, headers={"User-Agent": "vendor-sync"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return resp.read().decode().strip()


def main() -> None:
    gh = GitHub(os.environ.get("GITHUB_TOKEN"))
    version = latest_version()
    try:
        tag = tag_of(version)
    except UpstreamChanged as e:
        sys.exit(f"jbcontext: {e}")
    try:
        release = gh.release(REPO, tag)
    except urllib.error.HTTPError as e:
        if e.code != 404:
            raise
        sys.stderr.write(f"jbcontext: {version} has no GitHub release {tag} yet; pin kept\n")
        return
    if not cooled(release, datetime.now(UTC)):
        sys.stderr.write(
            f"jbcontext: {tag} published {release.published:%Y-%m-%dT%H:%MZ}, inside the"
            f" {COOLDOWN.days}-day cooldown; pin kept\n"
        )
        return
    commit = gh.commit(REPO, tag)
    original = gh.fetch(commit, SKILL)
    # The form end-of-file-fixer commits, or the next sync would undo the hook's newline.
    skill = original if original.endswith("\n") else original + "\n"
    try:
        forbid(skill, OTHER_SUBCOMMAND)
    except UpstreamChanged as e:
        sys.exit(f"jbcontext: {SKILL}: {e}")
    try:
        packages = pin(PACKAGES.read_text(), version)
    except UpstreamChanged as e:
        sys.exit(f"jbcontext: {e}")
    targets = {
        PACKAGES: packages,
        PLUGIN / ".claude-plugin" / "plugin.json": manifest(version),
        PLUGIN / SKILL: skill,
    }
    originals = {SKILL: original, f"releases/{tag}.md": release.notes}
    originals_dir = Path(d) if (d := os.environ.get("VENDOR_ORIGINALS")) else None
    ref = Path(__file__).with_name("jbcontext.ref")
    for p in converge(ref, commit, targets, originals, originals_dir):
        sys.stderr.write(f"jbcontext: wrote {p.relative_to(REPO_ROOT)}\n")


if __name__ == "__main__":
    main()
