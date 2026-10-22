"""The chezmoi wrappers around the settings engine, rendered through chezmoi itself.

Both wrappers embed the claude-managed.d fragments as the source renders them now
(.chezmoitemplates/claude-managed-fragments), never files deployed by an earlier apply:
- the settings.json filter merges exactly those fragments, so `chezmoi diff` shows a
  fragment edit's effect and applying settings.json alone uses the current fragments
- the snapshot script's rendered text changes whenever a fragment renders differently,
  including through .chezmoidata, which is what makes chezmoi's run_onchange_ re-run it
- a rendered wrapper is a runnable script
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
from conftest import FILTER_WRAPPER, FRAGMENT_DIR, REPO_ROOT, SNAPSHOT_WRAPPER, chezmoi_source

PARTIAL = REPO_ROOT / ".chezmoitemplates" / "claude-managed-fragments"


def execute(tmpl: Path, source: Path | None = None, data: dict[str, Any] | None = None) -> str:
    """`chezmoi execute-template` over a source dir (a copy of the real one by default). An empty config (not the user's): its
    read-source-state hook would run `make` from inside the test run."""
    argv = [
        "chezmoi", "execute-template", "--source", str(source or chezmoi_source()),
        "--config-format", "toml", "--config", os.devnull, "--file", str(tmpl),
    ]  # fmt: skip
    if data is not None:
        argv[2:2] = ["--override-data", json.dumps(data)]
    proc = subprocess.run(argv, text=True, capture_output=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def embedded(wrapper_text: str) -> list[list[str]]:
    """The fragments a rendered wrapper embeds: its FRAGMENTS line is a JSON string literal."""
    (line,) = [x for x in wrapper_text.splitlines() if x.startswith("FRAGMENTS = ")]
    return json.loads(json.loads(line.removeprefix("FRAGMENTS = ")))


def source_fragments(data: dict[str, Any] | None = None) -> list[list[str]]:
    """Each source fragment rendered on its own, named as the engine sees it."""
    out = []
    for path in sorted(FRAGMENT_DIR.iterdir()):
        text = execute(path, data=data) if path.suffix == ".tmpl" else path.read_text()
        out.append([path.name.removesuffix(".tmpl"), text])
    return out


@pytest.mark.parametrize("wrapper", [FILTER_WRAPPER, SNAPSHOT_WRAPPER])
def test_wrapper_embeds_every_source_fragment_as_it_renders_now(wrapper: Path):
    assert embedded(execute(wrapper)) == source_fragments()


def test_embedded_fragments_follow_the_data_a_fragment_template_reads():
    data = {"claude": {"routing": {"gated": ["probe-model"]}}}
    fragments = dict(embedded(execute(FILTER_WRAPPER, data=data)))
    assert (
        "Agent(model:*probe-model*)"
        in json.loads(fragments["agent-routing.json"])["permissions"]["ask"]
    )


def test_snapshot_wrapper_text_changes_with_a_fragment_rendering():
    """run_onchange_ keys on the rendered text: a data-only change must reach it."""
    data = {"claude": {"routing": {"gated": ["probe-model"]}}}
    assert execute(SNAPSHOT_WRAPPER) != execute(SNAPSHOT_WRAPPER, data=data)


def test_platform_fragment_renders_empty_off_its_platform():
    fragments = dict(embedded(execute(FILTER_WRAPPER, data={"chezmoi": {"os": "linux"}})))
    assert json.loads(fragments["plugins-darwin.json"]) == {}


def test_partial_rejects_a_fragment_that_is_not_json(tmp_path: Path):
    (tmp_path / ".chezmoitemplates").mkdir()
    shutil.copy(PARTIAL, tmp_path / ".chezmoitemplates")
    (tmp_path / "claude-managed.d").mkdir()
    (tmp_path / "claude-managed.d" / "notes.txt").write_text("x")
    tmpl = tmp_path / "t.tmpl"
    tmpl.write_text('{{ includeTemplate "claude-managed-fragments" . }}')
    proc = subprocess.run(
        [
            "chezmoi", "execute-template", "--source", str(tmp_path),
            "--config-format", "toml", "--config", os.devnull, "--file", str(tmpl),
        ],
        text=True, capture_output=True, check=False,
    )  # fmt: skip
    assert proc.returncode != 0
    assert "claude-managed.d/notes.txt: a fragment is *.json or *.json.tmpl" in proc.stderr


def test_rendered_wrappers_merge_then_snapshot_end_to_end(tmp_path: Path):
    """The filter's output, written back as settings, converges: the snapshot advances."""
    scripts = {}
    for name, wrapper in (("filter", FILTER_WRAPPER), ("snapshot", SNAPSHOT_WRAPPER)):
        scripts[name] = tmp_path / f"{name}.py"
        scripts[name].write_text(execute(wrapper))
    settings, base = tmp_path / "settings.json", tmp_path / "base.json"
    env = dict(os.environ)
    env.pop("CLAUDE_MANAGED_FORCE", None)
    env |= {"CLAUDE_SETTINGS_FILE": str(settings), "CLAUDE_BASE_FILE": str(base)}

    merged = subprocess.run(
        [sys.executable, str(scripts["filter"])],
        input="{}",
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert merged.returncode == 0, merged.stderr
    assert "Agent(model:*fable*)" in json.loads(merged.stdout)["permissions"]["ask"]
    settings.write_text(merged.stdout)

    snap = subprocess.run(
        [sys.executable, str(scripts["snapshot"])],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    assert snap.returncode == 0, snap.stderr
    assert "advanced" in snap.stdout
    assert base.exists()
