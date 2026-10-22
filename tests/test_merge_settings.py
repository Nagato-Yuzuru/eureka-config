"""Regression suite for the claude-managed settings merge engine.

Covers the declared merge-strategy semantics (exact / ensure / keyed) and
ownership retirement, plus invariants any three-way-merge implementation must
hold: a malformed base never crashes the merge (treated as absent instead),
and every conflict in the tree is collected and reported, not just the first.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from conftest import engine_argv


def hook_entry(command: str, matcher: str = "Bash", timeout: int = 5) -> dict:
    return {
        "matcher": matcher,
        "hooks": [{"type": "command", "command": command, "timeout": timeout}],
    }


FRAGMENT = {
    "$merge": {
        "permissions.ask": "ensure",
        "autoMode.soft_deny": "ensure",
        "hooks.*": "keyed",
    },
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


def merge(engine, current, fragment=FRAGMENT, base=None, force=frozenset()):
    """Compose a single fragment (it must compose cleanly: compose raises) and merge it.

    Returns the completed Merger — read ``.merged``, ``.conflicts``, ``.forced``,
    ``.warnings``.
    """
    composed = engine.compose([("test.json", fragment)])
    return engine.merge_composed(current, composed, engine.parse_base(base or {}), force=force)


def recorded(fragment: dict, data: dict) -> dict:
    """A base as the snapshot writes it: data under the fragment's recorded policy."""
    return {"$merge": fragment.get("$merge", {}), **data}


# --- merge semantics ---------------------------------------------------------


class TestFreshInstall:
    def test_empty_settings_gains_all_managed_slices(self, engine):
        r = merge(engine, {})
        assert r.conflicts == []
        assert r.warnings == []
        assert r.merged["permissions"]["ask"] == ["Agent(model:*fable*)"]
        assert r.merged["autoMode"]["soft_deny"] == ["$defaults", "RULE-V2"]
        assert [e["hooks"][0]["command"] for e in r.merged["hooks"]["PreToolUse"]] == [
            "/h/block-npm.sh",
            "/h/path-gate.sh",
        ]

    def test_unmanaged_keys_pass_through(self, engine):
        r = merge(engine, {"model": "opus[1m]", "tui": "fullscreen"})
        assert r.conflicts == []
        assert r.merged["model"] == "opus[1m]"
        assert r.merged["tui"] == "fullscreen"


class TestIdempotence:
    def test_merging_twice_is_stable(self, engine):
        once = merge(engine, {}).merged
        r = merge(engine, once)
        assert r.conflicts == []
        assert r.merged == once


class TestKeyedLists:
    def test_foreign_entry_preserved(self, engine):
        current = {"hooks": {"PreToolUse": [hook_entry("/other/peon.sh", timeout=9)]}}
        r = merge(engine, current)
        assert r.conflicts == []
        commands = [e["hooks"][0]["command"] for e in r.merged["hooks"]["PreToolUse"]]
        assert commands == ["/other/peon.sh", "/h/block-npm.sh", "/h/path-gate.sh"]

    def test_drifted_own_entry_replaced(self, engine):
        current = {"hooks": {"PreToolUse": [hook_entry("/h/block-npm.sh", timeout=99)]}}
        merged = merge(engine, current).merged
        (block_npm,) = [
            e
            for e in merged["hooks"]["PreToolUse"]
            if e["hooks"][0]["command"] == "/h/block-npm.sh"
        ]
        assert block_npm["hooks"][0]["timeout"] == 5

    def test_retired_entry_removed_via_base(self, engine):
        base = {"hooks": {"PreToolUse": [hook_entry("/h/retired.sh")]}}
        current = {"hooks": {"PreToolUse": [hook_entry("/h/retired.sh")]}}
        r = merge(engine, current, base=base)
        assert r.conflicts == []
        commands = [e["hooks"][0]["command"] for e in r.merged["hooks"]["PreToolUse"]]
        assert commands == ["/h/block-npm.sh", "/h/path-gate.sh"]

    def test_legacy_entry_purged_without_base(self, engine):
        current = {"hooks": {"PreToolUse": [hook_entry("/x/old-legacy.sh")]}}
        merged = merge(engine, current).merged
        commands = [e["hooks"][0]["command"] for e in merged["hooks"]["PreToolUse"]]
        assert commands == ["/h/block-npm.sh", "/h/path-gate.sh"]

    def test_malformed_foreign_entry_kept_without_crash(self, engine):
        weird: dict = {"matcher": "Bash", "hooks": [{"type": "prompt", "prompt": "hi"}]}
        current = {"hooks": {"PreToolUse": [weird, "not-even-a-dict"]}}
        r = merge(engine, current)
        assert r.conflicts == []
        assert weird in r.merged["hooks"]["PreToolUse"]
        assert "not-even-a-dict" in r.merged["hooks"]["PreToolUse"]

    def test_non_list_in_keyed_position_conflicts(self, engine):
        # regression: a foreign non-list here used to be silently destroyed
        current = {"hooks": {"PreToolUse": {"oops": "user set a dict here"}}}
        r = merge(engine, current)
        assert [c.path for c in r.conflicts] == ["hooks.PreToolUse"]
        # conflicting subtree is left as-is in the (discarded) merge result
        assert r.merged["hooks"]["PreToolUse"] == {"oops": "user set a dict here"}


class TestEnsureLists:
    def test_user_added_element_kept(self, engine):
        current = {"autoMode": {"soft_deny": ["$defaults", "RULE-V2", "user-added"]}}
        r = merge(engine, current)
        assert r.conflicts == []
        assert r.merged["autoMode"]["soft_deny"] == ["$defaults", "RULE-V2", "user-added"]

    def test_our_retired_element_swapped_for_new(self, engine):
        # base still has RULE-V1; fragment moved on to RULE-V2
        base = {"autoMode": {"soft_deny": ["$defaults", "RULE-V1"]}}
        current = {"autoMode": {"soft_deny": ["$defaults", "RULE-V1", "user-added"]}}
        r = merge(engine, current, base=base)
        assert r.conflicts == []
        assert r.merged["autoMode"]["soft_deny"] == ["$defaults", "user-added", "RULE-V2"]

    def test_wrong_typed_base_treated_as_empty(self, engine):
        # invariant: a malformed base (wrong shape for the path) never crashes
        # the merge or surfaces as a conflict — it is simply treated as absent
        base = {"autoMode": {"soft_deny": {"not": "a list"}}}
        r = merge(engine, {}, base=base)
        assert r.conflicts == []
        assert r.merged["autoMode"]["soft_deny"] == ["$defaults", "RULE-V2"]

    def test_scalar_in_list_position_conflicts(self, engine):
        current = {"permissions": {"ask": "oops-a-string"}}
        r = merge(engine, current)
        assert [c.path for c in r.conflicts] == ["permissions.ask"]
        # conflicting subtree is left as-is in the (discarded) merge result
        assert r.merged["permissions"]["ask"] == "oops-a-string"

    def test_non_string_elements_supported(self, engine):
        # ensure is not string-only: any JSON element merges by deep equality
        fragment = {"$merge": {"servers.pool": "ensure"}, "servers": {"pool": [{"host": "a"}]}}
        current = {"servers": {"pool": [{"host": "user"}]}}
        r = merge(engine, current, fragment=fragment)
        assert r.conflicts == []
        assert r.merged["servers"]["pool"] == [{"host": "user"}, {"host": "a"}]

    def test_own_element_hand_deleted_is_reasserted_without_warning(self, engine):
        # the fragment still declares the element (unlike retirement, where the
        # fragment itself drops it); a hand-deletion is silently reasserted
        base = {"autoMode": {"soft_deny": ["$defaults", "RULE-V2"]}}
        current = {"autoMode": {"soft_deny": ["RULE-V2"]}}
        r = merge(engine, current, base=base)
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged["autoMode"]["soft_deny"] == ["RULE-V2", "$defaults"]


class TestScalarLeaves:
    FRAG = {"effortLevel": "xhigh"}

    def test_absent_is_set(self, engine):
        r = merge(engine, {}, fragment=self.FRAG)
        assert r.conflicts == []
        assert r.merged["effortLevel"] == "xhigh"

    def test_equal_is_noop(self, engine):
        r = merge(engine, {"effortLevel": "xhigh"}, fragment=self.FRAG)
        assert r.conflicts == []
        assert r.merged["effortLevel"] == "xhigh"

    def test_ours_evolved_converges(self, engine):
        r = merge(engine, {"effortLevel": "high"}, fragment=self.FRAG, base={"effortLevel": "high"})
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged["effortLevel"] == "xhigh"

    def test_foreign_change_conflicts(self, engine):
        conflicts = merge(engine, {"effortLevel": "low"}, fragment=self.FRAG).conflicts
        assert [(c.path, c.current, c.desired) for c in conflicts] == [
            ("effortLevel", "low", "xhigh")
        ]

    def test_foreign_change_conflicts_with_base_present(self, engine):
        # base present but current has drifted from it too: neither the
        # current==base nor current==desired escape hatch applies, so a base
        # being present at all does not by itself excuse a foreign value.
        r = merge(
            engine,
            {"effortLevel": "low"},
            fragment=self.FRAG,
            base={"effortLevel": "high"},
        )
        assert [(c.path, c.current, c.desired) for c in r.conflicts] == [
            ("effortLevel", "low", "xhigh")
        ]

    def test_explicit_null_is_not_absent(self, engine):
        conflicts = merge(engine, {"effortLevel": None}, fragment=self.FRAG).conflicts
        assert [c.path for c in conflicts] == ["effortLevel"]


class TestJsonTypedEquality:
    """Python's True == 1 must never let a bool/number drift converge."""

    def test_bool_does_not_converge_with_number_leaf(self, engine):
        # regression: `current in (desired, base)` accepted True for 1
        conflicts = merge(engine, {"x": True}, fragment={"x": 1}).conflicts
        assert [(c.path, c.current, c.desired) for c in conflicts] == [("x", True, 1)]

    def test_ensure_appends_number_alongside_bool(self, engine):
        # regression: the declared 1 was considered "present" because of True
        fragment = {"$merge": {"s.ask": "ensure"}, "s": {"ask": [1]}}
        r = merge(engine, {"s": {"ask": [True]}}, fragment=fragment)
        assert r.conflicts == []
        assert r.merged["s"]["ask"] == [True, 1]

    def test_retirement_distinguishes_bool_from_number(self, engine):
        # base recorded 1; the live true is a foreign modification, not ours
        r = merge(engine, {"x": True}, fragment={}, base={"x": 1})
        assert r.conflicts == []
        assert r.merged == {"x": True}
        assert any("retired `x`" in w for w in r.warnings)


class TestUndeclaredListsAreExact:
    """A list with no $merge declaration is one atomic leaf, not a set merge."""

    FRAG = {"statusLine": ["a", "b"]}

    def test_foreign_element_conflicts_instead_of_set_merging(self, engine):
        conflicts = merge(engine, {"statusLine": ["a", "b", "user"]}, fragment=self.FRAG).conflicts
        assert [c.path for c in conflicts] == ["statusLine"]

    def test_ours_evolved_replaced_wholesale(self, engine):
        r = merge(engine, {"statusLine": ["old"]}, fragment=self.FRAG, base={"statusLine": ["old"]})
        assert r.conflicts == []
        assert r.merged["statusLine"] == ["a", "b"]


class TestExactSubtrees:
    """`exact` declared on an object owns the whole subtree atomically."""

    FRAG = {"$merge": {"env": "exact"}, "env": {"A": "1"}}

    def test_foreign_key_inside_subtree_conflicts(self, engine):
        conflicts = merge(engine, {"env": {"A": "1", "B": "2"}}, fragment=self.FRAG).conflicts
        assert [c.path for c in conflicts] == ["env"]

    def test_ours_evolved_replaced_wholesale(self, engine):
        r = merge(
            engine,
            {"env": {"A": "0", "B": "old"}},
            fragment=self.FRAG,
            base={"env": {"A": "0", "B": "old"}},
        )
        assert r.conflicts == []
        assert r.merged["env"] == {"A": "1"}


class TestRetirement:
    """Paths in base but gone from the fragment are withdrawn declaratively."""

    def test_unmodified_scalar_removed(self, engine):
        r = merge(
            engine, {"effortLevel": "xhigh", "keep": 1}, fragment={}, base={"effortLevel": "xhigh"}
        )
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged == {"keep": 1}

    def test_modified_scalar_kept_with_warning(self, engine):
        r = merge(engine, {"effortLevel": "low"}, fragment={}, base={"effortLevel": "xhigh"})
        assert r.conflicts == []
        assert r.merged == {"effortLevel": "low"}
        assert any("retired `effortLevel`" in w for w in r.warnings)

    def test_dict_retires_per_key_keeping_foreign(self, engine):
        base = {"autoMode": {"soft_deny": ["$defaults"]}}
        current = {"autoMode": {"soft_deny": ["$defaults"], "mine": True}}
        r = merge(engine, current, fragment={}, base=base)
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged == {"autoMode": {"mine": True}}

    def test_wholly_ours_dict_removed_entirely(self, engine):
        base = {"autoMode": {"soft_deny": ["$defaults"]}}
        current = {"autoMode": {"soft_deny": ["$defaults"]}}
        r = merge(engine, current, fragment={}, base=base)
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged == {}

    def test_modified_undeclared_list_kept_whole_with_warning(self, engine):
        # No $merge declaration covers the path, so the list retires as one
        # exact leaf: any foreign change keeps all of it, our elements included.
        base = {"autoMode": {"soft_deny": ["$defaults"]}}
        current = {"autoMode": {"soft_deny": ["$defaults", "user-added"]}}
        r = merge(engine, current, fragment={}, base=base)
        assert r.conflicts == []
        assert r.merged == current
        assert r.warnings == [
            "retired `autoMode.soft_deny` was modified since last apply; left in place"
        ]


class TestListRetirement:
    """A whole ensure/keyed list leaving the fragment drops exactly our cells.

    Unless a test says otherwise the base records the policy the fragment
    declares, and the declaration stays in the fragment (another list it
    governs is still live); only the retired list's data is gone.
    """

    KEYED = {
        "$merge": {"hooks.*": "keyed"},
        "$legacyHooks": ["old-legacy.sh"],
        "hooks": {"PreToolUse": [hook_entry("/h/live.sh")]},
    }
    ENSURE = {
        "$merge": {"permissions.ask": "ensure", "permissions.deny": "ensure"},
        "permissions": {"ask": ["A"]},
    }

    def test_keyed_keeps_only_foreign_entries(self, engine):
        base = {"hooks": {"PostToolUse": [hook_entry("/h/a.sh"), hook_entry("/h/b.sh")]}}
        current = {
            "hooks": {
                "PostToolUse": [
                    hook_entry("/h/a.sh"),
                    hook_entry("/other/peon.sh", timeout=9),
                    hook_entry("/h/b.sh"),
                ]
            }
        }
        r = merge(engine, current, fragment=self.KEYED, base=recorded(self.KEYED, base))
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged["hooks"] == {
            "PreToolUse": [hook_entry("/h/live.sh")],
            "PostToolUse": [hook_entry("/other/peon.sh", timeout=9)],
        }

    def test_ensure_keeps_only_foreign_elements(self, engine):
        base = {"permissions": {"ask": ["A"], "deny": ["X", "Y"]}}
        current = {"permissions": {"ask": ["A"], "deny": ["X", "user", "Y", "user2"]}}
        r = merge(engine, current, fragment=self.ENSURE, base=recorded(self.ENSURE, base))
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged["permissions"] == {"ask": ["A"], "deny": ["user", "user2"]}

    def test_keyed_all_own_removes_key(self, engine):
        base = {"hooks": {"PostToolUse": [hook_entry("/h/a.sh"), hook_entry("/h/b.sh")]}}
        # Order drifted, so the list is no longer == base, yet every entry is ours.
        current = {"hooks": {"PostToolUse": [hook_entry("/h/b.sh"), hook_entry("/h/a.sh")]}}
        r = merge(engine, current, fragment=self.KEYED, base=recorded(self.KEYED, base))
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged == {"hooks": {"PreToolUse": [hook_entry("/h/live.sh")]}}

    def test_ensure_all_own_removes_key(self, engine):
        base = {"permissions": {"ask": ["A"], "deny": ["X", "Y"]}}
        # A subset of our elements (one already deleted by hand) is still all ours.
        current = {"permissions": {"ask": ["A"], "deny": ["Y"]}}
        r = merge(engine, current, fragment=self.ENSURE, base=recorded(self.ENSURE, base))
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged == {"permissions": {"ask": ["A"]}}

    def test_keyed_drifted_own_entry_removed_silently(self, engine):
        # Keyed cells are addressed by claim key: a foreign edit to our entry's
        # body does not make it foreign, and list drift never warns.
        base = {"hooks": {"PostToolUse": [hook_entry("/h/a.sh", timeout=5)]}}
        current = {
            "hooks": {
                "PostToolUse": [
                    hook_entry("/h/a.sh", matcher="Edit", timeout=99),
                    hook_entry("/other/peon.sh"),
                ]
            }
        }
        r = merge(engine, current, fragment=self.KEYED, base=recorded(self.KEYED, base))
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged["hooks"]["PostToolUse"] == [hook_entry("/other/peon.sh")]

    def test_ensure_drifted_own_element_is_foreign_and_kept_silently(self, engine):
        # Ensure cells are addressed by value: an edited element is a different
        # value, i.e. foreign — kept, without a warning.
        fragment = {"$merge": {"s.live": "ensure", "s.pool": "ensure"}, "s": {"live": [1]}}
        base = {"s": {"live": [1], "pool": [{"host": "a", "port": 1}, {"host": "b"}]}}
        current = {"s": {"live": [1], "pool": [{"host": "a", "port": 2}, {"host": "b"}]}}
        r = merge(engine, current, fragment=fragment, base=recorded(fragment, base))
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged["s"]["pool"] == [{"host": "a", "port": 2}]

    def test_ensure_retirement_is_json_typed(self, engine):
        # base recorded 1; a live true is foreign, not ours
        fragment = {"$merge": {"s.live": "ensure", "s.pool": "ensure"}, "s": {"live": [0]}}
        base = {"s": {"live": [0], "pool": [1]}}
        current = {"s": {"live": [0], "pool": [True, 1]}}
        r = merge(engine, current, fragment=fragment, base=recorded(fragment, base))
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged["s"]["pool"] == [True]

    def test_keyed_legacy_entry_purged_on_retirement(self, engine):
        base = {"hooks": {"PostToolUse": [hook_entry("/h/a.sh")]}}
        current = {
            "hooks": {
                "PostToolUse": [
                    hook_entry("/x/old-legacy.sh"),
                    hook_entry("/h/a.sh"),
                    hook_entry("/other/peon.sh"),
                ]
            }
        }
        r = merge(engine, current, fragment=self.KEYED, base=recorded(self.KEYED, base))
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged["hooks"]["PostToolUse"] == [hook_entry("/other/peon.sh")]

    def test_keyed_malformed_foreign_entry_kept(self, engine):
        # An entry with no claim key ("") is never ours, even if base had one.
        weird: dict = {"matcher": "Bash", "hooks": [{"type": "prompt", "prompt": "hi"}]}
        base = {"hooks": {"PostToolUse": [hook_entry("/h/a.sh"), weird]}}
        current = {"hooks": {"PostToolUse": [hook_entry("/h/a.sh"), weird]}}
        r = merge(engine, current, fragment=self.KEYED, base=recorded(self.KEYED, base))
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged["hooks"]["PostToolUse"] == [weird]

    def test_whole_parent_object_retired_recurses_into_declared_lists(self, engine):
        # `hooks` left the fragment entirely; the keyed declaration remains.
        fragment = {"$merge": {"hooks.*": "keyed"}, "other": 1}
        base = {"hooks": {"PostToolUse": [hook_entry("/h/a.sh")], "Stop": [hook_entry("/h/s.sh")]}}
        current = {
            "other": 1,
            "hooks": {
                "PostToolUse": [hook_entry("/h/a.sh"), hook_entry("/other/peon.sh")],
                "Stop": [hook_entry("/h/s.sh")],
            },
        }
        r = merge(engine, current, fragment=fragment, base=recorded(fragment, base))
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged == {"other": 1, "hooks": {"PostToolUse": [hook_entry("/other/peon.sh")]}}

    def test_non_list_current_retires_as_exact_with_warning(self, engine):
        # A foreign non-list replaced our list: nothing of ours is addressable in
        # it, so the exact rule applies — kept, with the retirement warning.
        base = {"permissions": {"ask": ["A"], "deny": ["X"]}}
        current = {"permissions": {"ask": ["A"], "deny": "user-string"}}
        r = merge(engine, current, fragment=self.ENSURE, base=recorded(self.ENSURE, base))
        assert r.conflicts == []
        assert r.merged["permissions"]["deny"] == "user-string"
        assert r.warnings == [
            "retired `permissions.deny` was modified since last apply; left in place"
        ]


class TestRetirementUsesRecordedPolicy:
    """A retired path's strategy comes from the base's recorded policy."""

    def test_sole_declarer_deleted_ensure_list_keeps_only_foreign(self, engine):
        # The feature's fragment (the only one declaring the list) is gone, so
        # the current policy knows nothing about it; the base remembers.
        base = {
            "$merge": {"sandbox.filesystem.allowWrite": "ensure"},
            "sandbox": {"filesystem": {"allowWrite": ["/a", "/b"]}},
        }
        current = {"sandbox": {"filesystem": {"allowWrite": ["/a", "/user", "/b"]}}}
        r = merge(engine, current, fragment={}, base=base)
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged == {"sandbox": {"filesystem": {"allowWrite": ["/user"]}}}

    def test_list_written_as_exact_retires_as_exact_despite_new_declaration(self, engine):
        # x.b was an undeclared (exact) list when written; a fragment now
        # declares x.* ensure. Retirement follows the recorded policy: the
        # modified list is foreign and kept whole, with the warning.
        base = {"$merge": {}, "x": {"a": [1], "b": ["p", "q"]}}
        current = {"x": {"a": [1], "b": ["p", "q", "user"]}}
        fragment = {"$merge": {"x.*": "ensure"}, "x": {"a": [1]}}
        r = merge(engine, current, fragment=fragment, base=base)
        assert r.conflicts == []
        assert r.merged == {"x": {"a": [1], "b": ["p", "q", "user"]}}
        assert r.warnings == ["retired `x.b` was modified since last apply; left in place"]

    def test_legacy_base_without_policy_uses_current_policy(self, engine):
        # A base written before policies were recorded: retirement falls back
        # to the current composed policy, which still declares the list.
        fragment = {"$merge": {"s.live": "ensure", "s.pool": "ensure"}, "s": {"live": [0]}}
        base = {"s": {"live": [0], "pool": ["x"]}}
        current = {"s": {"live": [0], "pool": ["x", "user"]}}
        r = merge(engine, current, fragment=fragment, base=base)
        assert (r.conflicts, r.warnings) == ([], [])
        assert r.merged == {"s": {"live": [0], "pool": ["user"]}}

    def test_recorded_policy_does_not_govern_live_paths(self, engine):
        # The recorded policy only governs retirement: a live path merges
        # under the current one (here: undeclared, so exact, so it conflicts).
        base = {"$merge": {"p.ask": "ensure"}, "p": {"ask": ["A"]}}
        r = merge(engine, {"p": {"ask": ["A", "user"]}}, fragment={"p": {"ask": ["B"]}}, base=base)
        assert [c.path for c in r.conflicts] == ["p.ask"]


class TestConflicts:
    def test_all_conflicts_reported_not_just_first(self, engine):
        # invariant: every conflict in the tree is collected and reported, not
        # just the first one encountered
        fragment = {"a": 1, "b": {"c": 2}}
        current = {"a": 9, "b": {"c": 9}}
        conflicts = merge(engine, current, fragment=fragment).conflicts
        assert sorted(c.path for c in conflicts) == ["a", "b.c"]

    def test_scalar_in_object_position_conflicts(self, engine):
        current = {"autoMode": "oops-a-string"}
        conflicts = merge(engine, current).conflicts
        assert [c.path for c in conflicts] == ["autoMode"]


class TestForce:
    """Per-path conflict resolution in favor of desired (CLAUDE_MANAGED_FORCE)."""

    def test_force_never_touches_converged_paths(self, engine):
        r = merge(engine, {"a": 1}, fragment={"a": 1}, force=frozenset({"a"}))
        assert r.forced == [] and r.conflicts == []
        assert r.merged == {"a": 1}

    def test_forced_object_position_takes_whole_desired_subtree(self, engine):
        r = merge(engine, {"b": "oops"}, fragment={"b": {"c": 2}}, force=frozenset({"b"}))
        assert r.conflicts == []
        assert r.merged == {"b": {"c": 2}}
        assert [c.path for c in r.forced] == ["b"]


# --- filter I/O (main) -------------------------------------------------------


def run_filter(
    stdin: str,
    tmp_path: Path,
    fragments: dict[str, dict | str] | None = None,
    base: dict | None = None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    """Run the filter. A fragment value that is already a ``str`` is written
    verbatim (for deliberately-invalid fragment content); a ``dict`` is
    JSON-serialized."""
    env = dict(os.environ)
    env.pop("CLAUDE_MANAGED_FORCE", None)  # never inherit force from the developer's shell
    env.update(env_extra or {})
    if base is not None:
        (tmp_path / "base.json").write_text(json.dumps(base))
    env["CLAUDE_BASE_FILE"] = str(tmp_path / "base.json")
    return subprocess.run(
        engine_argv("main", fragments or {}, "src/claude-managed.d"),
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
        result = run_filter(quirky, tmp_path, fragments={"core.json": merged_obj})
        assert result.returncode == 0
        assert result.stdout == quirky

    def test_empty_stdin_produces_complete_file(self, tmp_path):
        result = run_filter("", tmp_path, fragments={"core.json": {"a": 1}})
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"a": 1}
        assert result.stdout.endswith("\n")

    def test_fragments_compose_across_files(self, tmp_path):
        result = run_filter(
            "",
            tmp_path,
            fragments={
                "one.json": {"$merge": {"p.ask": "ensure"}, "p": {"ask": ["A"]}},
                "two.json": {"$merge": {"p.ask": "ensure"}, "p": {"ask": ["B"]}},
            },
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"p": {"ask": ["A", "B"]}}

    def test_conflict_exits_1_with_report_and_no_stdout(self, tmp_path):
        result = run_filter('{"a": 9}', tmp_path, fragments={"core.json": {"a": 1}})
        assert result.returncode == 1
        assert result.stdout == ""
        assert "conflict at `a`" in result.stderr
        assert "current: 9" in result.stderr
        assert "desired: 1" in result.stderr

    def test_compose_conflict_exits_1_naming_both_files(self, tmp_path):
        result = run_filter("{}", tmp_path, fragments={"one.json": {"a": 1}, "two.json": {"a": 2}})
        assert result.returncode == 1
        assert result.stdout == ""
        assert "compose conflict at `a`" in result.stderr
        assert "one.json" in result.stderr
        assert "two.json" in result.stderr

    def test_non_object_and_invalid_json_fragments_together_name_both_files(self, tmp_path):
        # load_fragments collects errors across the whole directory, not just
        # the first bad file
        result = run_filter(
            "{}", tmp_path, fragments={"bad.json": "not json", "list.json": "[1, 2, 3]"}
        )
        assert result.returncode == 1
        assert result.stdout == ""
        assert "bad.json" in result.stderr
        assert "invalid JSON" in result.stderr
        assert "list.json" in result.stderr
        assert "fragment must be a JSON object" in result.stderr

    def test_invalid_json_stdin_exits_1_with_clean_message(self, tmp_path):
        result = run_filter("{not valid json", tmp_path, fragments={"core.json": {"a": 1}})
        assert result.returncode == 1
        assert result.stdout == ""
        assert "settings.json is not valid JSON" in result.stderr

    def test_zero_fragments_retire_everything_we_managed(self, tmp_path):
        result = run_filter('{"a": 1, "keep": true}', tmp_path, fragments={}, base={"a": 1})
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"keep": True}

    def test_retired_but_modified_warns_and_still_applies(self, tmp_path):
        result = run_filter('{"a": 2}', tmp_path, fragments={}, base={"a": 1})
        assert result.returncode == 0
        assert result.stdout == '{"a": 2}'
        assert "retired `a`" in result.stderr

    def test_malformed_base_policy_drops_only_the_policy(self, tmp_path):
        # The data still retires (as exact: no policy to fall back on here).
        result = run_filter(
            '{"a": 1, "keep": true}', tmp_path, fragments={}, base={"$merge": "bogus", "a": 1}
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"keep": True}
        assert "ignoring base policy" in result.stderr
        assert "$merge must be an object" in result.stderr

    def test_unknown_strategy_in_base_policy_is_ignored_with_warning(self, tmp_path):
        result = run_filter(
            '{"x": 1}', tmp_path, fragments={"core.json": {"x": 1}}, base={"$merge": {"x": "bogus"}}
        )
        assert result.returncode == 0, result.stderr
        assert "ignoring base policy" in result.stderr
        assert "unknown merge strategy 'bogus' for `x`" in result.stderr

    def test_malformed_base_policy_falls_back_to_composed_policy(self, tmp_path):
        # Not "no policy at all": the fragment still declares x.p ensure, so
        # the retired x.p retires per element (foreign kept), without a warning.
        fragment = {"$merge": {"x.q": "ensure", "x.p": "ensure"}, "x": {"q": ["Q"]}}
        base = {"$merge": {"x.p": 7}, "x": {"q": ["Q"], "p": ["ours"]}}
        stdin = json.dumps({"x": {"q": ["Q"], "p": ["ours", "user"]}})
        result = run_filter(stdin, tmp_path, fragments={"core.json": fragment}, base=base)
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"x": {"q": ["Q"], "p": ["user"]}}
        assert "ignoring base policy" in result.stderr
        assert "retired" not in result.stderr

    def test_corrupt_base_is_failsafe_not_fatal(self, tmp_path):
        (tmp_path / "base.json").write_text("not json at all")
        result = run_filter('{"a": 9}', tmp_path, fragments={"core.json": {"a": 1}})
        # without a base, the foreign-looking value must surface as a conflict
        assert result.returncode == 1
        assert "ignoring corrupt base" in result.stderr
        assert "conflict at `a`" in result.stderr

    def test_non_object_settings_is_hard_error(self, tmp_path):
        result = run_filter("[1, 2, 3]", tmp_path, fragments={"core.json": {"a": 1}})
        assert result.returncode == 1
        assert "not a JSON object" in result.stderr

    def test_bool_number_change_is_written_not_passed_through(self, tmp_path):
        """Python's `==` unifies `1` with `true`; the no-op passthrough must not, or the
        fragment's `true` never reaches the file and settings never converge."""
        result = run_filter(
            '{"a": 1}', tmp_path, fragments={"core.json": {"a": True}}, base={"a": 1}
        )
        assert result.returncode == 0, result.stderr
        assert json.loads(result.stdout) == {"a": True}
        assert '"a": true' in result.stdout

    def test_unicode_survives_round_trip(self, tmp_path):
        fragment = {
            "$merge": {"autoMode.soft_deny": "ensure"},
            "autoMode": {"soft_deny": ["规则 — em dash"]},
        }
        result = run_filter("{}", tmp_path, fragments={"core.json": fragment})
        assert result.returncode == 0
        assert "规则 — em dash" in result.stdout


class TestForceFilterIO:
    """CLAUDE_MANAGED_FORCE end to end: the env var, the report, the retry command."""

    def test_force_env_resolves_conflict_and_reports(self, tmp_path):
        result = run_filter(
            '{"a": 9}',
            tmp_path,
            fragments={"core.json": {"a": 1}},
            env_extra={"CLAUDE_MANAGED_FORCE": "a"},
        )
        assert result.returncode == 0
        assert json.loads(result.stdout) == {"a": 1}
        assert "forced `a`: 9 -> 1" in result.stderr

    def test_conflict_report_ends_with_paste_ready_command(self, tmp_path):
        result = run_filter('{"a": 9, "b": 8}', tmp_path, fragments={"core.json": {"a": 1, "b": 2}})
        assert result.returncode == 1
        assert "CLAUDE_MANAGED_FORCE='a,b' chezmoi apply" in result.stderr

    def test_partial_force_fails_and_resuggests_consumed_paths(self, tmp_path):
        result = run_filter(
            '{"a": 9, "b": 8}',
            tmp_path,
            fragments={"core.json": {"a": 1, "b": 2}},
            env_extra={"CLAUDE_MANAGED_FORCE": "a"},
        )
        assert result.returncode == 1
        assert result.stdout == ""
        assert "conflict at `b`" in result.stderr
        assert "conflict at `a`" not in result.stderr
        # nothing applied this run, so the retry command must re-include `a`
        assert "CLAUDE_MANAGED_FORCE='b,a' chezmoi apply" in result.stderr

    def test_unmatched_force_warns_and_passes_through(self, tmp_path):
        quirky = '{"a":1}'  # byte-stable passthrough must survive a stale force var
        result = run_filter(
            quirky,
            tmp_path,
            fragments={"core.json": {"a": 1}},
            env_extra={"CLAUDE_MANAGED_FORCE": "typo.path, ,"},
        )
        assert result.returncode == 0
        assert result.stdout == quirky
        assert "`typo.path` matched no conflict" in result.stderr
