"""The base snapshot must only advance when settings are verifiably converged.

Guards the ordering invariant: a base that runs ahead of settings (e.g. merge
conflicted but the run script still fired) would manufacture false conflicts on
every later apply.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import ENGINE_PATH, SNAPSHOT_PATH

FRAGMENTS = {
    "core.json": {
        "$merge": {"permissions.ask": "ensure"},
        "$legacyHooks": [],
        "permissions": {"ask": ["Agent(model:*fable*)"]},
        "a": 1,
    },
    "extra.json": {"$merge": {"permissions.ask": "ensure"}, "permissions": {"ask": ["Extra"]}},
}
# What compose(FRAGMENTS) yields — the base snapshot must store exactly this.
COMPOSED = {"permissions": {"ask": ["Agent(model:*fable*)", "Extra"]}, "a": 1}
CONVERGED = {"permissions": {"ask": ["Agent(model:*fable*)", "Extra"]}, "a": 1, "unmanaged": True}


def run_snapshot(
    tmp_path: Path,
    settings: dict | None,
    fragments: dict[str, dict] | None = FRAGMENTS,
    base: dict | None = None,
):
    env = dict(os.environ)
    fragment_dir = tmp_path / "claude-managed.d"
    if fragments is not None:
        fragment_dir.mkdir(exist_ok=True)
        for name, fragment in fragments.items():
            (fragment_dir / name).write_text(json.dumps(fragment))
    if settings is not None:
        (tmp_path / "settings.json").write_text(json.dumps(settings))
    if base is not None:
        (tmp_path / "base.json").write_text(json.dumps(base))
    env |= {
        "CLAUDE_ENGINE_FILE": str(ENGINE_PATH),
        "CLAUDE_FRAGMENT_DIR": str(fragment_dir),
        "CLAUDE_SETTINGS_FILE": str(tmp_path / "settings.json"),
        "CLAUDE_BASE_FILE": str(tmp_path / "base.json"),
    }
    return subprocess.run(
        [sys.executable, str(SNAPSHOT_PATH)], capture_output=True, text=True, env=env
    )


class TestSnapshot:
    def test_converged_settings_advance_base_to_composed_fragment(self, tmp_path):
        result = run_snapshot(tmp_path, CONVERGED)
        assert result.returncode == 0
        base = json.loads((tmp_path / "base.json").read_text())
        assert base == COMPOSED

    def test_bool_number_drift_is_not_converged(self, tmp_path):
        """Python's `==` unifies bool with int (`True == 1`); convergence is
        JSON equality. Base and live settings hold `true`, the fragment wants
        `1`: the merge moves the leaf with no conflict, so live settings have
        not caught up, and advancing the base would manufacture a false
        conflict on the next apply."""
        result = run_snapshot(
            tmp_path,
            settings={"x": True},
            fragments={"x.json": {"x": 1}},
            base={"x": True},
        )
        assert result.returncode == 0
        assert "not converged" in result.stderr
        assert json.loads((tmp_path / "base.json").read_text()) == {"x": True}

    def test_full_retirement_with_converged_settings_advances_base_to_empty(self, tmp_path):
        """An emptied fragment dir (existing, zero fragments) composes to
        `{}`; once live settings reflect the retirement, the base must
        advance to `{}` too, not get stuck on the last non-empty snapshot.
        """
        result = run_snapshot(tmp_path, settings={}, fragments={}, base={"a": 1})
        assert result.returncode == 0
        assert "advanced" in result.stdout
        assert json.loads((tmp_path / "base.json").read_text()) == {}

    def test_unconverged_settings_leave_base_untouched(self, tmp_path):
        result = run_snapshot(tmp_path, {"a": 1})  # ask rules not merged yet
        assert result.returncode == 0
        assert "not converged" in result.stderr
        assert not (tmp_path / "base.json").exists()

    def test_conflicted_settings_leave_base_untouched(self, tmp_path):
        stale = {"old": "base"}
        result = run_snapshot(tmp_path, {**CONVERGED, "a": 9}, base=stale)
        assert result.returncode == 0
        assert "not converged" in result.stderr
        assert json.loads((tmp_path / "base.json").read_text()) == stale

    def test_uncomposable_fragments_leave_base_untouched(self, tmp_path):
        result = run_snapshot(
            tmp_path, CONVERGED, fragments={"one.json": {"a": 1}, "two.json": {"a": 2}}
        )
        assert result.returncode == 0
        assert "not composable" in result.stderr
        assert not (tmp_path / "base.json").exists()

    def test_rerun_on_same_state_is_silent_noop(self, tmp_path):
        run_snapshot(tmp_path, CONVERGED)
        mtime = (tmp_path / "base.json").stat().st_mtime_ns
        result = run_snapshot(tmp_path, CONVERGED)
        assert result.returncode == 0
        assert result.stdout == ""
        assert (tmp_path / "base.json").stat().st_mtime_ns == mtime

    def test_missing_fragment_dir_is_noop(self, tmp_path):
        result = run_snapshot(tmp_path, None, fragments=None)
        assert result.returncode == 0
        assert not (tmp_path / "base.json").exists()
