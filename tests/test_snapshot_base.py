"""The base snapshot must only advance when settings are verifiably converged.

Guards the ordering invariant: a base that runs ahead of settings (e.g. merge
conflicted but the run script still fired) would manufacture false conflicts on
every later apply.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from conftest import engine_argv

FRAGMENTS = {
    "core.json": {
        "$merge": {"permissions.ask": "ensure"},
        "$legacyHooks": [],
        "permissions": {"ask": ["Agent(model:*fable*)"]},
        "a": 1,
    },
    "extra.json": {"$merge": {"permissions.ask": "ensure"}, "permissions": {"ask": ["Extra"]}},
}
# What compose(FRAGMENTS) yields, plus the policy it was composed under — the
# base snapshot must store exactly this.
COMPOSED = {"permissions": {"ask": ["Agent(model:*fable*)", "Extra"]}, "a": 1}
RECORDED = {"$merge": {"permissions.ask": "ensure"}, **COMPOSED}
CONVERGED = {"permissions": {"ask": ["Agent(model:*fable*)", "Extra"]}, "a": 1, "unmanaged": True}


def run_snapshot(
    tmp_path: Path,
    settings: dict | list | str | None,
    fragments: dict[str, dict] = FRAGMENTS,
    base: dict | None = None,
):
    """Run the snapshot. A ``str`` settings value is written verbatim
    (for deliberately-invalid content); anything else is JSON-serialized."""
    env = dict(os.environ)
    if settings is not None:
        content = settings if isinstance(settings, str) else json.dumps(settings)
        (tmp_path / "settings.json").write_text(content)
    if base is not None:
        (tmp_path / "base.json").write_text(json.dumps(base))
    env |= {
        "CLAUDE_SETTINGS_FILE": str(tmp_path / "settings.json"),
        "CLAUDE_BASE_FILE": str(tmp_path / "base.json"),
    }
    return subprocess.run(
        engine_argv("snapshot", fragments), capture_output=True, text=True, env=env
    )


def run_filter(
    stdin: str, fragments: dict[str, dict], env_extra: dict[str, str]
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env.update(env_extra)
    env.pop("CLAUDE_MANAGED_FORCE", None)
    return subprocess.run(
        engine_argv("main", fragments, "src/claude-managed.d"),
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


class TestSnapshot:
    def test_converged_settings_advance_base_to_composed_fragment_and_policy(self, tmp_path):
        result = run_snapshot(tmp_path, CONVERGED)
        assert result.returncode == 0
        base = json.loads((tmp_path / "base.json").read_text())
        assert base == RECORDED

    def test_legacy_base_without_policy_is_upgraded_on_converged_apply(self, tmp_path):
        result = run_snapshot(tmp_path, CONVERGED, base=COMPOSED)
        assert result.returncode == 0
        assert "advanced" in result.stdout
        assert json.loads((tmp_path / "base.json").read_text()) == RECORDED

    def test_written_base_round_trips_through_the_filter(self, tmp_path):
        """The base the snapshot writes is read back by the filter: converged
        settings stay byte-identical, and deleting the sole declaring fragment
        then retires its ensure list per element via the recorded policy."""
        run_snapshot(tmp_path, CONVERGED)
        settings = json.dumps(CONVERGED)
        env = {"CLAUDE_BASE_FILE": str(tmp_path / "base.json")}
        converged = run_filter(settings, FRAGMENTS, env)
        assert (converged.returncode, converged.stdout) == (0, settings)

        live = {**CONVERGED, "permissions": {"ask": ["Agent(model:*fable*)", "user", "Extra"]}}
        retired = run_filter(json.dumps(live), {"core.json": {"a": 1}}, env)
        assert retired.returncode == 0, retired.stderr
        assert json.loads(retired.stdout) == {
            "permissions": {"ask": ["user"]},
            "a": 1,
            "unmanaged": True,
        }

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
        """Zero fragments compose to
        `{}`; once live settings reflect the retirement, the base must
        advance to `{}` too, not get stuck on the last non-empty snapshot.
        """
        result = run_snapshot(tmp_path, settings={}, fragments={}, base={"a": 1})
        assert result.returncode == 0
        assert "advanced" in result.stdout
        assert json.loads((tmp_path / "base.json").read_text()) == {"$merge": {}}

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

    def test_corrupt_settings_leaves_base_untouched(self, tmp_path):
        # same live file the modify_ filter would itself refuse to parse; a
        # quiet no-op here, like the "not composable"/"not converged" cases
        stale = {"old": "base"}
        result = run_snapshot(tmp_path, "not json at all", base=stale)
        assert result.returncode == 0
        assert "not valid JSON" in result.stderr
        assert json.loads((tmp_path / "base.json").read_text()) == stale

    def test_non_object_settings_leaves_base_untouched(self, tmp_path):
        result = run_snapshot(tmp_path, [1, 2, 3])
        assert result.returncode == 0
        assert "not a JSON object" in result.stderr
        assert not (tmp_path / "base.json").exists()

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
