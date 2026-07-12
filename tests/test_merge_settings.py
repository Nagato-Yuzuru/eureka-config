"""Regression suite for the claude-managed settings merge engine.

Scenarios mirror the behaviors established while the engine lived in bash+jq,
including one test per bug found there (fail-open on malformed data, formatter
crash on engine errors, first-conflict-only reporting).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from conftest import ENGINE_PATH


def hook_entry(command: str, matcher: str = "Bash", timeout: int = 5) -> dict:
    return {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": command, "timeout": timeout}],
    }


FRAGMENT = {
    "$legacyHooks": ["old-legacy.sh"],
    "permissions": {"ask": ["Agent(model:*fable*)"]},
    "autoMode": {"soft_deny": ["$defaults", "RULE-V2"]},
    "hooks": {
        "PreToolUse": [
            hook_entry("/h/block-npm.sh"),
            hook_entry("/h/path-gate.sh", matcher="^X$"),
        ]
    },
}


# --- merge semantics ---------------------------------------------------------


class TestFreshInstall:
    def test_empty_settings_gains_all_managed_slices(self, engine):
        merged, conflicts = engine.merge_fragment({}, FRAGMENT, {})
        assert conflicts == []
        assert merged["permissions"]["ask"] == ["Agent(model:*fable*)"]
        assert merged["autoMode"]["soft_deny"] == ["$defaults", "RULE-V2"]
        assert [e["hooks"][0]["command"] for e in merged["hooks"]["PreToolUse"]] == [
            "/h/block-npm.sh",
            "/h/path-gate.sh",
        ]

    def test_reserved_dollar_keys_never_merged(self, engine):
        merged, _ = engine.merge_fragment({}, FRAGMENT, {})
        assert "$legacyHooks" not in merged

    def test_unmanaged_keys_pass_through(self, engine):
        merged, conflicts = engine.merge_fragment(
            {"model": "opus[1m]", "tui": "fullscreen"}, FRAGMENT, {}
        )
        assert conflicts == []
        assert merged["model"] == "opus[1m]"
        assert merged["tui"] == "fullscreen"


class TestIdempotence:
    def test_merging_twice_is_stable(self, engine):
        once, _ = engine.merge_fragment({}, FRAGMENT, {})
        twice, conflicts = engine.merge_fragment(once, FRAGMENT, {})
        assert conflicts == []
        assert twice == once


class TestHookArrays:
    def test_foreign_entry_preserved(self, engine):
        current = {"hooks": {"PreToolUse": [hook_entry("/other/peon.sh", timeout=9)]}}
        merged, conflicts = engine.merge_fragment(current, FRAGMENT, {})
        assert conflicts == []
        commands = [e["hooks"][0]["command"] for e in merged["hooks"]["PreToolUse"]]
        assert commands == ["/other/peon.sh", "/h/block-npm.sh", "/h/path-gate.sh"]

    def test_drifted_own_entry_replaced(self, engine):
        current = {"hooks": {"PreToolUse": [hook_entry("/h/block-npm.sh", timeout=99)]}}
        merged, _ = engine.merge_fragment(current, FRAGMENT, {})
        (block_npm,) = [
            e
            for e in merged["hooks"]["PreToolUse"]
            if e["hooks"][0]["command"] == "/h/block-npm.sh"
        ]
        assert block_npm["hooks"][0]["timeout"] == 5

    def test_retired_entry_removed_via_base(self, engine):
        base = {"hooks": {"PreToolUse": [hook_entry("/h/retired.sh")]}}
        current = {"hooks": {"PreToolUse": [hook_entry("/h/retired.sh")]}}
        merged, conflicts = engine.merge_fragment(current, FRAGMENT, base)
        assert conflicts == []
        commands = [e["hooks"][0]["command"] for e in merged["hooks"]["PreToolUse"]]
        assert "/h/retired.sh" not in commands

    def test_legacy_entry_purged_without_base(self, engine):
        current = {"hooks": {"PreToolUse": [hook_entry("/x/old-legacy.sh")]}}
        merged, _ = engine.merge_fragment(current, FRAGMENT, {})
        commands = [e["hooks"][0]["command"] for e in merged["hooks"]["PreToolUse"]]
        assert "/x/old-legacy.sh" not in commands

    def test_malformed_foreign_entry_kept_without_crash(self, engine):
        weird: dict = {"matcher": "Bash", "hooks": [{"type": "prompt", "prompt": "hi"}]}
        current = {"hooks": {"PreToolUse": [weird, "not-even-a-dict"]}}
        merged, conflicts = engine.merge_fragment(current, FRAGMENT, {})
        assert conflicts == []
        assert weird in merged["hooks"]["PreToolUse"]
        assert "not-even-a-dict" in merged["hooks"]["PreToolUse"]


class TestStringArrays:
    def test_user_added_element_kept(self, engine):
        current = {"autoMode": {"soft_deny": ["$defaults", "RULE-V2", "user-added"]}}
        merged, conflicts = engine.merge_fragment(current, FRAGMENT, {})
        assert conflicts == []
        assert merged["autoMode"]["soft_deny"] == ["$defaults", "RULE-V2", "user-added"]

    def test_our_retired_element_swapped_for_new(self, engine):
        # base still has RULE-V1; fragment moved on to RULE-V2
        base = {"autoMode": {"soft_deny": ["$defaults", "RULE-V1"]}}
        current = {"autoMode": {"soft_deny": ["$defaults", "RULE-V1", "user-added"]}}
        merged, conflicts = engine.merge_fragment(current, FRAGMENT, base)
        assert conflicts == []
        assert merged["autoMode"]["soft_deny"] == ["$defaults", "user-added", "RULE-V2"]

    def test_wrong_typed_base_treated_as_empty(self, engine):
        # regression: in the bash engine a wrong-typed base crashed jq and the
        # crash was mis-rendered by the conflict formatter
        base = {"autoMode": {"soft_deny": {"not": "a list"}}}
        merged, conflicts = engine.merge_fragment({}, FRAGMENT, base)
        assert conflicts == []
        assert merged["autoMode"]["soft_deny"] == ["$defaults", "RULE-V2"]

    def test_scalar_in_list_position_conflicts(self, engine):
        current = {"permissions": {"ask": "oops-a-string"}}
        merged, conflicts = engine.merge_fragment(current, FRAGMENT, {})
        assert [c.path for c in conflicts] == ["permissions.ask"]
        # conflicting subtree is left as-is in the (discarded) merge result
        assert merged["permissions"]["ask"] == "oops-a-string"


class TestScalarLeaves:
    FRAG = {"effortLevel": "xhigh"}

    def test_absent_is_set(self, engine):
        merged, conflicts = engine.merge_fragment({}, self.FRAG, {})
        assert conflicts == []
        assert merged["effortLevel"] == "xhigh"

    def test_equal_is_noop(self, engine):
        merged, conflicts = engine.merge_fragment({"effortLevel": "xhigh"}, self.FRAG, {})
        assert conflicts == []
        assert merged["effortLevel"] == "xhigh"

    def test_ours_evolved_converges(self, engine):
        merged, conflicts = engine.merge_fragment(
            {"effortLevel": "high"}, self.FRAG, {"effortLevel": "high"}
        )
        assert conflicts == []
        assert merged["effortLevel"] == "xhigh"

    def test_foreign_change_conflicts(self, engine):
        _, conflicts = engine.merge_fragment({"effortLevel": "low"}, self.FRAG, {})
        assert [(c.path, c.current, c.desired) for c in conflicts] == [
            ("effortLevel", "low", "xhigh")
        ]

    def test_explicit_null_is_not_absent(self, engine):
        _, conflicts = engine.merge_fragment({"effortLevel": None}, self.FRAG, {})
        assert [c.path for c in conflicts] == ["effortLevel"]


class TestConflicts:
    def test_all_conflicts_reported_not_just_first(self, engine):
        # regression: the bash engine aborted at the first conflict
        frag = {"a": 1, "b": {"c": 2}}
        current = {"a": 9, "b": {"c": 9}}
        _, conflicts = engine.merge_fragment(current, frag, {})
        assert sorted(c.path for c in conflicts) == ["a", "b.c"]

    def test_scalar_in_object_position_conflicts(self, engine):
        current = {"autoMode": "oops-a-string"}
        _, conflicts = engine.merge_fragment(current, FRAGMENT, {})
        assert [c.path for c in conflicts] == ["autoMode"]


# --- filter I/O (main) -------------------------------------------------------


def run_filter(stdin: str, tmp_path: Path, fragment=None, base=None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    fragment_file = tmp_path / "fragment.json"
    base_file = tmp_path / "base.json"
    if fragment is not None:
        fragment_file.write_text(json.dumps(fragment))
    if base is not None:
        base_file.write_text(json.dumps(base))
    env["CLAUDE_FRAGMENT_FILE"] = str(fragment_file)
    env["CLAUDE_BASE_FILE"] = str(base_file)
    return subprocess.run(
        [sys.executable, str(ENGINE_PATH)],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
    )


class TestFilterIO:
    def test_converged_input_passes_through_byte_identical(self, tmp_path):
        # regression: textual (not semantic) change detection caused format churn
        merged_obj = {"permissions": {"ask": ["Agent(model:*fable*)"]}}
        quirky = '{\n      "permissions": {"ask":["Agent(model:*fable*)"]}}'  # odd formatting
        result = run_filter(quirky, tmp_path, fragment=merged_obj)
        assert result.returncode == 0
        assert result.stdout == quirky

    def test_empty_stdin_produces_complete_file(self, tmp_path):
        result = run_filter("", tmp_path, fragment={"a": 1})
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"a": 1}
        assert result.stdout.endswith("\n")

    def test_conflict_exits_1_with_report_and_no_stdout(self, tmp_path):
        result = run_filter('{"a": 9}', tmp_path, fragment={"a": 1})
        assert result.returncode == 1
        assert result.stdout == ""
        assert "conflict at `a`" in result.stderr
        assert "current: 9" in result.stderr
        assert "desired: 1" in result.stderr

    def test_missing_fragment_passes_through(self, tmp_path):
        result = run_filter('{"keep": true}', tmp_path)
        assert result.returncode == 0
        assert result.stdout == '{"keep": true}'
        assert "passthrough" in result.stderr

    def test_corrupt_base_is_failsafe_not_fatal(self, tmp_path):
        (tmp_path / "base.json").write_text("not json at all")
        env = dict(os.environ)
        fragment_file = tmp_path / "fragment.json"
        fragment_file.write_text(json.dumps({"a": 1}))
        env["CLAUDE_FRAGMENT_FILE"] = str(fragment_file)
        env["CLAUDE_BASE_FILE"] = str(tmp_path / "base.json")
        result = subprocess.run(
            [sys.executable, str(ENGINE_PATH)],
            input='{"a": 9}',
            capture_output=True,
            text=True,
            env=env,
        )
        # without a base, the foreign-looking value must surface as a conflict
        assert result.returncode == 1
        assert "ignoring corrupt base" in result.stderr
        assert "conflict at `a`" in result.stderr

    def test_non_object_settings_is_hard_error(self, tmp_path):
        result = run_filter("[1, 2, 3]", tmp_path, fragment={"a": 1})
        assert result.returncode == 1
        assert "not a JSON object" in result.stderr

    def test_unicode_survives_round_trip(self, tmp_path):
        fragment = {"autoMode": {"soft_deny": ["规则 — em dash"]}}
        result = run_filter("{}", tmp_path, fragment=fragment)
        assert result.returncode == 0
        assert "规则 — em dash" in result.stdout
