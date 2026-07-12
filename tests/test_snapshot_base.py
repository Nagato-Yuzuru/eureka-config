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

FRAGMENT = {"$legacyHooks": [], "permissions": {"ask": ["Agent(model:*fable*)"]}, "a": 1}


def run_snapshot(tmp_path: Path, settings: dict, fragment=FRAGMENT, base=None):
    env = dict(os.environ)
    (tmp_path / "fragment.json").write_text(json.dumps(fragment))
    (tmp_path / "settings.json").write_text(json.dumps(settings))
    if base is not None:
        (tmp_path / "base.json").write_text(json.dumps(base))
    env |= {
        "CLAUDE_ENGINE_FILE": str(ENGINE_PATH),
        "CLAUDE_FRAGMENT_FILE": str(tmp_path / "fragment.json"),
        "CLAUDE_SETTINGS_FILE": str(tmp_path / "settings.json"),
        "CLAUDE_BASE_FILE": str(tmp_path / "base.json"),
    }
    return subprocess.run(
        [sys.executable, str(SNAPSHOT_PATH)], capture_output=True, text=True, env=env
    )


CONVERGED = {"permissions": {"ask": ["Agent(model:*fable*)"]}, "a": 1, "unmanaged": True}


class TestSnapshot:
    def test_converged_settings_advance_base(self, tmp_path):
        result = run_snapshot(tmp_path, CONVERGED)
        assert result.returncode == 0
        base = json.loads((tmp_path / "base.json").read_text())
        assert base == {"permissions": {"ask": ["Agent(model:*fable*)"]}, "a": 1}
        assert "$legacyHooks" not in base

    def test_unconverged_settings_leave_base_untouched(self, tmp_path):
        result = run_snapshot(tmp_path, {"a": 1})  # ask rule not merged yet
        assert result.returncode == 0
        assert "not converged" in result.stderr
        assert not (tmp_path / "base.json").exists()

    def test_conflicted_settings_leave_base_untouched(self, tmp_path):
        stale = {"old": "base"}
        result = run_snapshot(tmp_path, {**CONVERGED, "a": 9}, base=stale)
        assert result.returncode == 0
        assert "not converged" in result.stderr
        assert json.loads((tmp_path / "base.json").read_text()) == stale

    def test_rerun_on_same_state_is_silent_noop(self, tmp_path):
        run_snapshot(tmp_path, CONVERGED)
        mtime = (tmp_path / "base.json").stat().st_mtime_ns
        result = run_snapshot(tmp_path, CONVERGED)
        assert result.returncode == 0
        assert result.stdout == ""
        assert (tmp_path / "base.json").stat().st_mtime_ns == mtime

    def test_missing_fragment_is_noop(self, tmp_path):
        env = dict(os.environ)
        env |= {
            "CLAUDE_ENGINE_FILE": str(ENGINE_PATH),
            "CLAUDE_FRAGMENT_FILE": str(tmp_path / "nope.json"),
            "CLAUDE_SETTINGS_FILE": str(tmp_path / "settings.json"),
            "CLAUDE_BASE_FILE": str(tmp_path / "base.json"),
        }
        result = subprocess.run(
            [sys.executable, str(SNAPSHOT_PATH)], capture_output=True, text=True, env=env
        )
        assert result.returncode == 0
        assert not (tmp_path / "base.json").exists()
