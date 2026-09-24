"""Compose layer: many feature fragments -> one effective fragment.

Every fragment is chezmoi-managed, so cross-fragment contradictions are our
own authoring bugs — each must fail loudly at compose time, and all errors
are raised together as one ExceptionGroup.
"""

from __future__ import annotations

import pytest


def compose(engine, **named):
    """compose(engine, a={...}, b={...}) composes fragments named a.json, b.json, ..."""
    return engine.compose([(f"{name}.json", fragment) for name, fragment in named.items()])


def compose_errors(engine, **named) -> list[str]:
    """Error messages from a compose that must fail, in collection order."""
    with pytest.raises(ExceptionGroup) as excinfo:
        compose(engine, **named)
    return [str(exc) for exc in excinfo.value.exceptions]


def entry(command: str) -> dict:
    return {"matcher": "Bash", "hooks": [{"type": "command", "command": command}]}


class TestDataComposition:
    def test_disjoint_top_level_keys_union(self, engine):
        composed = compose(engine, a={"x": 1}, b={"y": 2})
        assert composed.data == {"x": 1, "y": 2}

    def test_objects_deep_merge(self, engine):
        composed = compose(engine, a={"o": {"x": 1}}, b={"o": {"y": 2}})
        assert composed.data == {"o": {"x": 1, "y": 2}}

    def test_equal_duplicate_scalar_tolerated(self, engine):
        composed = compose(engine, a={"x": 1}, b={"x": 1})
        assert composed.data == {"x": 1}

    def test_scalar_collision_reports_both_files(self, engine):
        errors = compose_errors(engine, a={"x": 1}, b={"x": 2})
        assert len(errors) == 1
        assert "compose conflict at `x`" in errors[0]
        assert "a.json" in errors[0]
        assert "b.json" in errors[0]

    def test_nested_collision_names_the_file_that_set_the_subtree(self, engine):
        errors = compose_errors(engine, a={"o": {"x": 1}}, b={"o": {"x": 2}})
        assert len(errors) == 1
        assert "`o.x`" in errors[0]
        assert "a.json" in errors[0]
        assert "b.json" in errors[0]

    def test_dict_vs_list_collision_errors(self, engine):
        errors = compose_errors(engine, a={"x": {"k": 1}}, b={"x": [1]})
        assert len(errors) == 1
        assert "compose conflict at `x`" in errors[0]

    def test_undeclared_lists_are_atomic_and_collide(self, engine):
        errors = compose_errors(engine, a={"x": [1]}, b={"x": [2]})
        assert len(errors) == 1
        assert "compose conflict at `x`" in errors[0]

    def test_bool_and_number_collide_instead_of_unifying(self, engine):
        # Python's True == 1 must not make {"x": 1} and {"x": true} "equal"
        errors = compose_errors(engine, a={"x": 1}, b={"x": True})
        assert len(errors) == 1
        assert "compose conflict at `x`" in errors[0]

    def test_source_fragments_never_mutated(self, engine):
        fragment_a = {"$merge": {"s.ask": "ensure"}, "s": {"ask": ["A"]}}
        fragment_b = {"$merge": {"s.ask": "ensure"}, "s": {"ask": ["B"]}}
        composed = compose(engine, a=fragment_a, b=fragment_b)
        assert composed.data["s"]["ask"] == ["A", "B"]
        assert fragment_a["s"]["ask"] == ["A"]
        assert fragment_b["s"]["ask"] == ["B"]

    def test_composed_data_never_aliases_fragment_objects(self, engine):
        # regression: the ensure branch used to extend with un-copied references
        fragment_a = {"$merge": {"s.pool": "ensure"}, "s": {"pool": [{"host": "a"}]}}
        fragment_b = {"$merge": {"s.pool": "ensure"}, "s": {"pool": [{"host": "b"}]}}
        composed = compose(engine, a=fragment_a, b=fragment_b)
        composed.data["s"]["pool"][1]["host"] = "mutated"
        assert fragment_b["s"]["pool"][0]["host"] == "b"


class TestEnsureComposition:
    def test_union_in_filename_order_with_dedup(self, engine):
        composed = compose(
            engine,
            a={"$merge": {"s.ask": "ensure"}, "s": {"ask": ["A", "SHARED"]}},
            b={"s": {"ask": ["SHARED", "B"]}},
        )
        assert composed.data["s"]["ask"] == ["A", "SHARED", "B"]

    def test_declared_on_non_list_errors(self, engine):
        errors = compose_errors(engine, a={"$merge": {"x": "ensure"}, "x": 1})
        assert any("strategy ensure requires a list" in e for e in errors)

    def test_duplicate_elements_within_one_fragment_error(self, engine):
        # cross-fragment union dedups; within-fragment duplicates must not
        # slip past validation into settings.json
        errors = compose_errors(engine, a={"$merge": {"s.ask": "ensure"}, "s": {"ask": ["A", "A"]}})
        assert any('ensure element "A" declared more than once' in e for e in errors)

    def test_union_distinguishes_bool_from_number(self, engine):
        composed = compose(
            engine,
            a={"$merge": {"s.ask": "ensure"}, "s": {"ask": [1]}},
            b={"s": {"ask": [True]}},
        )
        assert composed.data["s"]["ask"] == [1, True]


class TestKeyedComposition:
    def test_lists_concatenate_in_filename_order(self, engine):
        composed = compose(
            engine,
            a={"$merge": {"hooks.*": "keyed"}, "hooks": {"PreToolUse": [entry("/h/a.sh")]}},
            b={"hooks": {"PreToolUse": [entry("/h/b.sh")]}},
        )
        commands = [e["hooks"][0]["command"] for e in composed.data["hooks"]["PreToolUse"]]
        assert commands == ["/h/a.sh", "/h/b.sh"]

    def test_duplicate_claim_key_across_files_errors(self, engine):
        errors = compose_errors(
            engine,
            a={"$merge": {"hooks.*": "keyed"}, "hooks": {"PreToolUse": [entry("/x/dup.sh")]}},
            b={"hooks": {"PreToolUse": [entry("/y/dup.sh")]}},
        )
        assert any("claim key `dup.sh` declared more than once" in e for e in errors)

    def test_entry_without_claim_key_errors(self, engine):
        broken = {"matcher": "Bash", "hooks": [{"type": "prompt", "prompt": "hi"}]}
        errors = compose_errors(
            engine, a={"$merge": {"hooks.*": "keyed"}, "hooks": {"PreToolUse": [broken]}}
        )
        assert any("no derivable claim key" in e for e in errors)


class TestPolicyComposition:
    def test_same_pattern_same_strategy_from_two_files_ok(self, engine):
        # redeclaring the same pattern with the same strategy from a second
        # file is not a contradiction, and composes to exactly one entry
        composed = compose(
            engine,
            a={"$merge": {"s.ask": "ensure"}, "s": {"ask": ["A"]}},
            b={"$merge": {"s.ask": "ensure"}, "s": {"ask": ["B"]}},
        )
        assert composed.policy == {("s", "ask"): "ensure"}

    def test_same_pattern_different_strategy_errors(self, engine):
        errors = compose_errors(
            engine,
            a={"$merge": {"s.ask": "ensure"}, "s": {"ask": ["A"]}},
            b={"$merge": {"s.ask": "exact"}},
        )
        assert any("merge-strategy conflict on `s.ask`" in e for e in errors)
        assert any("a.json" in e and "b.json" in e for e in errors)

    def test_overlapping_wildcard_patterns_conflict(self, engine):
        errors = compose_errors(
            engine,
            a={"$merge": {"hooks.*": "keyed"}},
            b={"$merge": {"hooks.PreToolUse": "ensure"}},
        )
        assert errors == [
            "merge-strategy conflict on `hooks.PreToolUse`: a.json says keyed, b.json says ensure"
        ]

    def test_pattern_nested_under_declared_ancestor_errors(self, engine):
        # no strategy recurses, so `env.PATH` under `env: exact` is dead config
        errors = compose_errors(
            engine,
            a={"$merge": {"env": "exact"}},
            b={"$merge": {"env.PATH": "ensure"}},
        )
        assert any(
            "`env.PATH` (b.json) is nested under declared `env` (a.json)" in e for e in errors
        )

    def test_pattern_nested_under_declared_ancestor_errors_regardless_of_order(self, engine):
        # attribution follows pattern hierarchy (which is the ancestor, which
        # is nested), not which file declared its pattern first: here the
        # nested pattern is declared first and the ancestor second.
        errors = compose_errors(
            engine,
            a={"$merge": {"env.PATH": "ensure"}},
            b={"$merge": {"env": "exact"}},
        )
        assert any(
            "`env.PATH` (a.json) is nested under declared `env` (b.json)" in e for e in errors
        )

    def test_nested_pattern_errors_even_with_same_strategy(self, engine):
        errors = compose_errors(engine, a={"$merge": {"env": "exact", "env.PATH": "exact"}})
        assert any("nested under declared `env`" in e for e in errors)

    def test_wildcard_ancestor_also_shadows_descendants(self, engine):
        errors = compose_errors(
            engine,
            a={"$merge": {"hooks.*": "keyed"}},
            b={"$merge": {"hooks.PreToolUse.extra": "ensure"}},
        )
        assert any("nested under declared `hooks.*`" in e for e in errors)

    def test_unknown_strategy_errors(self, engine):
        errors = compose_errors(engine, a={"$merge": {"x": "clobber"}})
        assert any("unknown merge strategy 'clobber'" in e for e in errors)

    def test_merge_must_be_object(self, engine):
        errors = compose_errors(engine, a={"$merge": ["nope"]})
        assert any("$merge must be an object" in e for e in errors)


class TestReservedKeys:
    def test_unknown_reserved_key_errors(self, engine):
        errors = compose_errors(engine, a={"$mrege": {}})
        assert any("unknown reserved key `$mrege`" in e for e in errors)

    def test_reserved_keys_stripped_from_data(self, engine):
        composed = compose(engine, a={"$legacyHooks": ["x.sh"], "a": 1})
        assert composed.data == {"a": 1}
        assert composed.legacy == ("x.sh",)

    def test_legacy_hooks_union_dedup_in_filename_order(self, engine):
        composed = compose(
            engine, a={"$legacyHooks": ["x.sh", "y.sh"]}, b={"$legacyHooks": ["y.sh", "z.sh"]}
        )
        assert composed.legacy == ("x.sh", "y.sh", "z.sh")

    def test_legacy_hooks_non_list_errors(self, engine):
        errors = compose_errors(engine, a={"$legacyHooks": "x.sh"})
        assert any("$legacyHooks must be a list of strings" in e for e in errors)

    def test_legacy_hooks_non_string_entry_errors(self, engine):
        errors = compose_errors(engine, a={"$legacyHooks": ["ok.sh", 3]})
        assert any("$legacyHooks entries must all be strings" in e for e in errors)


class TestErrorCollection:
    """All compose errors are collected across the whole set of fragments."""

    def test_independent_errors_from_different_files_all_reported(self, engine):
        # two unrelated authoring bugs, in two unrelated files, in one compose
        errors = compose_errors(
            engine,
            a={"$mrege": {}},
            b={"$legacyHooks": "not-a-list"},
        )
        assert len(errors) == 2
        assert any("a.json" in e and "unknown reserved key `$mrege`" in e for e in errors)
        assert any("b.json" in e and "$legacyHooks must be a list of strings" in e for e in errors)


class TestEmpty:
    def test_no_fragments_compose_to_empty(self, engine):
        composed = engine.compose([])
        assert composed.data == {}
        assert composed.policy == {}
        assert composed.legacy == ()
