# ruff: noqa: T201
"""Merge the chezmoi-managed fragments into Claude Code's settings.json.

Engine behind two chezmoi wrappers, which import this file from the source
dir and pass it the fragments chezmoi rendered into them:
``dot_claude/modify_settings.json.tmpl`` calls ``main`` and
``run_onchange_after_snapshot-claude-managed.py.tmpl`` calls ``snapshot``.

``main`` is a ``modify_`` filter: it receives the current contents of
``~/.claude/settings.json`` on stdin and writes the merged contents to stdout.
chezmoi runs it on every ``diff``/``status``/``apply``/``--dry-run``, so it
must stay a pure filter — stdout is the only side effect.

Data / state / command:
    data     ``claude-managed.d/*.json[.tmpl]`` in the chezmoi source — one
             fragment per feature, rendered by chezmoi into the wrappers
             (``.chezmoitemplates/claude-managed-fragments``) and composed in
             name order into the settings subset we own. Every chezmoi command
             therefore merges the source's fragments as they render now:
             ``chezmoi diff`` shows what a fragment edit does to settings.json,
             and applying settings.json alone uses the current fragments.
    state    ``~/.claude/.claude-managed.applied.json`` — the *composed*
             fragment as of the last converged apply, advanced by ``snapshot``
             only after verifying settings == merge result, so it can never
             run ahead of the target. The base is what distinguishes "our own value evolved
             -> converge" from "someone else changed it -> conflict", and "left
             the fragment -> retire" from "foreign -> keep".
             Format: the composed data plus a top-level ``"$merge"`` holding
             the composed policy in fragment syntax (``{"dotted.path":
             strategy}``, always written, possibly ``{}``) — so a base is
             itself a valid fragment. Composed data never has ``$`` keys, so
             the reserved key is unambiguous. A base without ``$merge`` (written
             before policies were recorded) is still read: data as-is, no
             recorded policy.
    command  this filter.

Reserved keys (top level of each fragment, never merged; unknown ``$`` keys
are compose errors):
    ``$merge``       ``{"dotted.path": strategy}`` — merge-strategy
                     declarations for the paths a fragment owns; ``*`` matches
                     exactly one segment. Strategies:
                       exact   the value is one atomic leaf (deep equality):
                               current absent / == fragment / == base -> the
                               fragment value; any other current -> conflict.
                               Default for every non-object value; declarable
                               on objects to own a whole subtree.
                       ensure  three-way element-set merge for lists: foreign
                               elements kept, elements in base but gone from
                               the fragment removed, missing fragment elements
                               appended. Never conflicts (a non-list current
                               does).
                       keyed   list entries claimed per claim key (basename of
                               the entry's first hook command): ours replaced,
                               retired via base and ``$legacyHooks``; foreign
                               entries pass through untouched (a non-list
                               current conflicts).
                     Objects without a declaration recurse per key.
    ``$legacyHooks`` retired hook-script basenames to purge (union across
                     fragments).

Ownership has two axes per path: ADDRESSING — what one cell of ownership is
(an object key, a whole ``exact`` value, an ``ensure`` element identified by
its value, a ``keyed`` element identified by its claim key) — and DRIFT
POLICY — what a foreign change to an owned cell does. Only a *modified*
``exact`` cell conflicts; a deleted ``exact`` cell, and any drift on
``ensure``/``keyed`` cells, is reasserted silently: the list strategies
manage mechanism (hooks, permission rules), not preference.

Compose (fragments -> one effective fragment, before the three-way merge):
    objects deep-merge; ``ensure`` lists union in filename order; ``keyed``
    lists concatenate. Anything else colliding with a different value, a
    strategy contradiction (two patterns that can govern the same path —
    including one nested under the other, which would be dead since no
    strategy recurses), an ``ensure``/``keyed`` declaration on a non-list, a
    duplicated ``ensure`` element or keyed claim key, or a keyed entry with no
    derivable claim key is a compose error — all fragments are chezmoi-managed,
    so these are our own authoring bugs: reported together, exit 1, target
    untouched.
    Zero fragments compose to the empty fragment and retire everything we
    ever managed.

Retirement (a path in base but gone from the composed fragment): value still
== base -> removed; objects recurse per key, keeping foreign keys. Anything
modified since last apply is foreign now — kept with a warning, never a
conflict: we no longer claim the path, so blocking the apply over it would be
wrong. Once the base advances past the retired path the warning stops and the
value is permanently foreign.
A retired path's strategy is looked up in the base's recorded policy — the one
its values were written under — so deleting a feature's fragment (which takes
its ``$merge`` declarations with it) still retires its lists correctly. The
recorded strategy only selects per-cell list retirement: a retired list
recorded ``ensure``/``keyed`` retires per cell — exactly our cells (``ensure``
elements in base, ``keyed`` entries whose claim key is in base or
``$legacyHooks``) are removed, foreign cells kept, silently; a list left empty
is removed. Everything else follows the rule above, whatever the recorded
strategy (an object recorded ``exact`` still recurses per key), and a list not
recorded as ``ensure``/``keyed`` stays one leaf even if a fragment now declares
it one. A base with no recorded policy falls back to the current composed
policy; on such a base, deleting a list's sole declaring fragment in the same
apply retires the list as a leaf — the first apply after upgrading should not
also delete fragments.

Equality is JSON-typed throughout: ``true`` never equals ``1`` even though
Python's ``==`` says otherwise, so a bool/number drift conflicts instead of
silently converging.

Conflicts are collected across the whole tree and reported together on stderr;
exit 1 makes chezmoi leave the target untouched. The report ends with a
ready-to-paste ``CLAUDE_MANAGED_FORCE='<path>,<path>' chezmoi apply``: the
one-shot escape hatch resolving exactly the named conflicts in favor of the
fragments. There is no opposite direction — taking the live value is done by
editing the fragment. Force paths are compared verbatim against the printed
conflict path, comma-separated, so a path containing a comma cannot be forced
(fails loudly as "matched no conflict"); the env var lives for one command and
the base advances on the converged apply, so force never persists. A partial
force still exits 1 and applies nothing (the file stays all-or-nothing); the
suggested command then re-includes the already-forced paths. When the merge is
a no-op the original stdin bytes are passed through verbatim, so formatting
differences never show up as drift in ``chezmoi diff``.
"""

from __future__ import annotations

import copy
import enum
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

type Json = None | bool | int | float | str | list[Json] | dict[str, Json]
type Strategy = Literal["exact", "ensure", "keyed"]

# Identity map so a raw JSON string resolves to a *typed* Strategy (or None).
_STRATEGIES: dict[str, Strategy] = {"exact": "exact", "ensure": "ensure", "keyed": "keyed"}
_RESERVED = ("$merge", "$legacyHooks")


class _Absent(enum.Enum):
    """Sentinel distinguishing "key missing" from an explicit null in the target.

    An enum (not a bare ``object()``) so ``x is _ABSENT`` narrows
    ``Json | _Absent`` back to ``Json`` under static type checking.
    """

    ABSENT = enum.auto()


_ABSENT = _Absent.ABSENT


@dataclass(slots=True)
class Conflict:
    path: str
    current: Json
    desired: Json


def _hook_key(entry: Json) -> str:
    """Claim key of a keyed-list entry: basename of its first hook command.

    Malformed or non-command entries key to "" which never matches one of our
    basenames, so they are treated as foreign and preserved. rsplit rather than
    PurePath: the command may be a whole shell line, not a path.
    """
    match entry:
        case {"hooks": [{"command": str(command)}, *_]}:
            return command.rsplit("/", 1)[-1]
        case _:
            return ""


def strip_reserved(fragment: dict[str, Json]) -> dict[str, Json]:
    """Fragment minus reserved $-prefixed keys — what actually gets composed."""
    return {k: v for k, v in fragment.items() if not k.startswith("$")}


def _same(a: Json, b: Json) -> bool:
    """Deep JSON equality; unlike Python's ``==``, bool never equals a number.

    Python unifies bool with int (``True == 1``), which would let a bool/number
    drift silently "converge". Every semantic equality check in this file must
    go through here (or ``_contains``), never bare ``==``.
    """
    if isinstance(a, bool) is not isinstance(b, bool):
        return False
    match a, b:
        case dict(), dict():
            return a.keys() == b.keys() and all(_same(value, b[key]) for key, value in a.items())
        case list(), list():
            return len(a) == len(b) and all(_same(x, y) for x, y in zip(a, b, strict=True))
        case _:
            return a == b


def _contains(items: list[Json], value: Json) -> bool:
    """List membership under ``_same`` instead of Python's ``==``."""
    return any(_same(item, value) for item in items)


# --- merge-strategy policy ----------------------------------------------------


def _matches(pattern: tuple[str, ...], path: tuple[str, ...]) -> bool:
    return len(pattern) == len(path) and all(
        p in ("*", s) for p, s in zip(pattern, path, strict=True)
    )


def _overlaps(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Whether two patterns can govern the same data.

    Equal length: they can match the same concrete path. Different length: the
    shorter can match an ancestor of paths the longer matches — and since no
    strategy recurses, a declaration nested under a declared path is dead.
    """
    return all(x == y or "*" in (x, y) for x, y in zip(a, b, strict=False))


def strategy_for(policy: dict[tuple[str, ...], Strategy], path: tuple[str, ...]) -> Strategy | None:
    """First declared strategy matching path; contradictions are compose errors."""
    for pattern, strategy in policy.items():
        if _matches(pattern, path):
            return strategy
    return None


# --- compose: many feature fragments -> one effective fragment ----------------


@dataclass(slots=True)
class Composed:
    """The effective fragment: every feature fragment composed into one."""

    data: dict[str, Json]
    policy: dict[tuple[str, ...], Strategy]
    legacy: tuple[str, ...]


def compose(fragments: list[tuple[str, dict[str, Json]]]) -> Composed:
    """Compose (name, fragment) pairs, in order, into one effective fragment.

    Raises:
        ExceptionGroup: of ValueError (semantic contradictions) and TypeError
        (shape violations); on any error nothing must be merged. All errors
        across all fragments are collected, not just the first.
    """
    errors: list[Exception] = []
    policy: dict[tuple[str, ...], Strategy] = {}
    policy_src: dict[tuple[str, ...], str] = {}
    legacy: list[str] = []

    for name, fragment in fragments:
        for key in fragment:
            if key.startswith("$") and key not in _RESERVED:
                errors.append(ValueError(f"{name}: unknown reserved key `{key}`"))
        _collect_policy(name, fragment.get("$merge"), policy, policy_src, errors)
        match fragment.get("$legacyHooks"):
            case None:
                pass
            case list(entries):
                strings = [x for x in entries if isinstance(x, str)]
                if len(strings) != len(entries):
                    errors.append(TypeError(f"{name}: $legacyHooks entries must all be strings"))
                legacy += [x for x in strings if x not in legacy]
            case other:
                errors.append(
                    TypeError(f"{name}: $legacyHooks must be a list of strings, got {other!r}")
                )

    data: dict[str, Json] = {}
    origin: dict[tuple[str, ...], str] = {}
    for name, fragment in fragments:
        _compose_into(data, strip_reserved(fragment), (), name, origin, policy, errors)

    for key, value in data.items():
        _validate(value, (key,), policy, errors)
    if errors:
        raise ExceptionGroup("fragments do not compose", errors)
    return Composed(data, policy, tuple(legacy))


def _collect_policy(
    name: str,
    raw: Json,
    policy: dict[tuple[str, ...], Strategy],
    policy_src: dict[tuple[str, ...], str],
    errors: list[Exception],
) -> None:
    if raw is None:
        return
    if not isinstance(raw, dict):
        errors.append(TypeError(f"{name}: $merge must be an object"))
        return
    for dotted, value in raw.items():
        strategy = _STRATEGIES.get(value) if isinstance(value, str) else None
        if strategy is None:
            errors.append(ValueError(f"{name}: unknown merge strategy {value!r} for `{dotted}`"))
            continue
        pattern = tuple(dotted.split("."))
        for existing, existing_strategy in policy.items():
            if not _overlaps(existing, pattern):
                continue
            if len(existing) == len(pattern):
                if existing_strategy != strategy:
                    errors.append(
                        ValueError(
                            f"merge-strategy conflict on `{dotted}`: {policy_src[existing]} says"
                            f" {existing_strategy}, {name} says {strategy}"
                        )
                    )
            else:
                if len(existing) < len(pattern):
                    ancestor, ancestor_src = existing, policy_src[existing]
                    nested, nested_src = pattern, name
                else:
                    ancestor, ancestor_src = pattern, name
                    nested, nested_src = existing, policy_src[existing]
                errors.append(
                    ValueError(
                        f"merge-strategy conflict: `{'.'.join(nested)}` ({nested_src}) is nested"
                        f" under declared `{'.'.join(ancestor)}` ({ancestor_src}) and can never"
                        " apply — no strategy recurses"
                    )
                )
        policy.setdefault(pattern, strategy)
        policy_src.setdefault(pattern, name)


def _compose_into(
    acc: dict[str, Json],
    incoming: dict[str, Json],
    path: tuple[str, ...],
    name: str,
    origin: dict[tuple[str, ...], str],
    policy: dict[tuple[str, ...], Strategy],
    errors: list[Exception],
) -> None:
    for key, value in incoming.items():
        sub = (*path, key)
        if key not in acc:
            # Deep copy so later fragments extending a shared list in place can
            # never mutate the caller's parsed fragment objects.
            acc[key] = copy.deepcopy(value)
            origin[sub] = name
            continue
        existing = acc[key]
        strategy = strategy_for(policy, sub)
        if strategy is None and isinstance(existing, dict) and isinstance(value, dict):
            _compose_into(existing, value, sub, name, origin, policy, errors)
        elif strategy == "ensure" and isinstance(existing, list) and isinstance(value, list):
            existing.extend(copy.deepcopy(x) for x in value if not _contains(existing, x))
        elif strategy == "keyed" and isinstance(existing, list) and isinstance(value, list):
            existing.extend(copy.deepcopy(value))
        elif not _same(existing, value):
            errors.append(
                ValueError(
                    f"compose conflict at `{'.'.join(sub)}`: {_origin_of(origin, sub)} vs {name}"
                )
            )


def _origin_of(origin: dict[tuple[str, ...], str], path: tuple[str, ...]) -> str:
    """Fragment that first set path — the nearest recorded ancestor."""
    for i in range(len(path), 0, -1):
        if (name := origin.get(path[:i])) is not None:
            return name
    return "<unknown>"


def _validate(
    value: Json,
    path: tuple[str, ...],
    policy: dict[tuple[str, ...], Strategy],
    errors: list[Exception],
) -> None:
    """Composed data must satisfy its own declarations — violations are bugs."""
    dotted = ".".join(path)
    match strategy_for(policy, path):
        case ("ensure" | "keyed") as strategy if not isinstance(value, list):
            errors.append(
                TypeError(
                    f"`{dotted}`: strategy {strategy} requires a list, got {type(value).__name__}"
                )
            )
        case "keyed":
            assert isinstance(value, list)
            keys = [_hook_key(entry) for entry in value]
            for i, key in enumerate(keys):
                if not key:
                    errors.append(
                        ValueError(f"`{dotted}[{i}]`: keyed entry has no derivable claim key")
                    )
            for key in sorted({k for k in keys if k and keys.count(k) > 1}):
                errors.append(ValueError(f"`{dotted}`: claim key `{key}` declared more than once"))
        case "ensure":
            assert isinstance(value, list)
            reported: list[Json] = []
            for item in value:
                if _contains(reported, item):
                    continue
                if sum(1 for x in value if _same(x, item)) > 1:
                    reported.append(item)
                    errors.append(
                        ValueError(
                            f"`{dotted}`: ensure element {json.dumps(item, ensure_ascii=False)}"
                            " declared more than once"
                        )
                    )
        case None if isinstance(value, dict):
            for key, item in value.items():
                _validate(item, (*path, key), policy, errors)
        case _:
            pass


# --- base snapshot: composed fragment as of the last converged apply ----------


@dataclass(slots=True)
class Base:
    """The parsed base: composed data plus the policy it was written under.

    ``policy`` is None for a base written before policies were recorded.
    """

    data: dict[str, Json]
    policy: dict[tuple[str, ...], Strategy] | None


def parse_base(raw: dict[str, Json]) -> Base:
    """Parse a base snapshot (see the module docstring for its format).

    Raises:
        ExceptionGroup: of ValueError / TypeError when a recorded ``$merge`` is
        malformed.
    """
    data = strip_reserved(raw)
    if "$merge" not in raw:
        return Base(data=data, policy=None)
    policy: dict[tuple[str, ...], Strategy] = {}
    errors: list[Exception] = []
    _collect_policy("base", raw["$merge"], policy, {}, errors)
    if errors:
        raise ExceptionGroup("malformed recorded policy", errors)
    return Base(data=data, policy=policy)


def base_snapshot(composed: Composed) -> dict[str, Json]:
    """The base to record for a converged composed fragment; inverse of ``parse_base``."""
    policy: dict[str, Json] = {".".join(p): s for p, s in composed.policy.items()}
    return {"$merge": policy, **composed.data}


# --- three-way merge ----------------------------------------------------------


@dataclass(slots=True, kw_only=True)
class Merger:
    """One merge pass of the composed fragment against current settings.

    Inputs ``policy``, ``retire_policy``, ``legacy`` and ``force`` are the
    cross-cutting context the per-node strategies read without threading it
    through every recursive call. ``retire_policy`` governs retired paths: the
    base's recorded policy, or the current composed policy for a base without
    one. Outputs accumulate on the instance: ``merged`` is the result tree
    (populated by ``merge_composed``; discard it when ``conflicts`` is
    non-empty — a conflict leaves the current value in place), ``warnings``
    report retired-but-modified paths left as-is, and ``forced`` records the
    would-be conflicts resolved as desired because their path was in ``force``.
    """

    policy: dict[tuple[str, ...], Strategy]
    retire_policy: dict[tuple[str, ...], Strategy]
    legacy: tuple[str, ...]
    force: frozenset[str] = frozenset()
    merged: dict[str, Json] = field(default_factory=dict)
    conflicts: list[Conflict] = field(default_factory=list)
    forced: list[Conflict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def merge(
        self,
        current: Json | _Absent,
        desired: Json,
        base: Json | _Absent,
        path: tuple[str, ...],
    ) -> Json:
        match strategy_for(self.policy, path):
            case "ensure":
                assert isinstance(desired, list)  # compose validation guarantees it
                return self._ensure_list(current, desired, base, path)
            case "keyed":
                assert isinstance(desired, list)
                return self._keyed_list(current, desired, base, path)
            case "exact":
                return self._leaf(current, desired, base, path)
            case None if isinstance(desired, dict):
                return self._object(current, desired, base, path)
            case _:
                return self._leaf(current, desired, base, path)

    def _conflict(self, path: tuple[str, ...], current: Json, desired: Json) -> Json:
        record = Conflict(".".join(path), current, desired)
        if record.path in self.force:
            self.forced.append(record)
            return desired
        self.conflicts.append(record)
        return current

    def _object(
        self,
        current: Json | _Absent,
        desired: dict[str, Json],
        base: Json | _Absent,
        path: tuple[str, ...],
    ) -> Json:
        if not isinstance(current, dict) and current is not _ABSENT:
            return self._conflict(path, current, desired)
        cur = current if isinstance(current, dict) else {}
        sub = base if isinstance(base, dict) else {}
        merged = cur | {
            key: self.merge(cur.get(key, _ABSENT), want, sub.get(key, _ABSENT), (*path, key))
            for key, want in desired.items()
        }
        for key, base_value in sub.items():
            if key in desired or key not in merged:
                continue
            retired = self._retire(merged[key], base_value, (*path, key))
            if retired is _ABSENT:
                del merged[key]
            else:
                merged[key] = retired
        return merged

    def _retire(self, current: Json, base: Json, path: tuple[str, ...]) -> Json | _Absent:
        """Withdraw ownership of a path that left the composed fragment.

        Still == base -> remove; objects recurse per key, keeping foreign keys
        (an object left empty was wholly ours and is dropped). Anything
        modified since last apply is foreign now — kept with a warning, never
        a conflict: we no longer claim the path, so blocking over it would be
        wrong. ``ensure``/``keyed`` lists (per ``retire_policy``) retire per
        cell instead: see ``_retire_list``.
        """
        match strategy_for(self.retire_policy, path), current, base:
            case ("ensure" | "keyed") as strategy, list(), list():
                return self._retire_list(strategy, current, base)
        if _same(current, base):
            return _ABSENT
        if isinstance(current, dict) and isinstance(base, dict):
            kept: dict[str, Json] = {}
            for key, value in current.items():
                if key in base:
                    retired = self._retire(value, base[key], (*path, key))
                    if retired is not _ABSENT:
                        kept[key] = retired
                else:
                    kept[key] = value
            return kept if kept else _ABSENT
        self.warnings.append(
            f"retired `{'.'.join(path)}` was modified since last apply; left in place"
        )
        return current

    def _retire_list(
        self, strategy: Literal["ensure", "keyed"], current: list[Json], base: list[Json]
    ) -> list[Json] | _Absent:
        """Retire a whole ``ensure``/``keyed`` list: drop exactly our cells.

        Our cells are addressed as while the list was live — ``ensure``
        elements by value in base, ``keyed`` entries by the claim keys in base
        plus ``$legacyHooks`` — so foreign cells survive and a drifted own
        ``keyed`` entry is still ours (removed), while a drifted ``ensure``
        element is a different value and so foreign (kept). Silent, like list
        drift: no warning. A list left empty was wholly ours and is dropped.
        """
        if strategy == "ensure":
            kept = [x for x in current if not _contains(base, x)]
        else:
            ours = {_hook_key(entry) for entry in base} | set(self.legacy)
            ours.discard("")
            kept = [entry for entry in current if _hook_key(entry) not in ours]
        return kept if kept else _ABSENT

    def _keyed_list(
        self,
        current: Json | _Absent,
        desired: list[Json],
        base: Json | _Absent,
        path: tuple[str, ...],
    ) -> Json:
        if not isinstance(current, list) and current is not _ABSENT:
            return self._conflict(path, current, desired)
        base_entries = base if isinstance(base, list) else []
        ours = {_hook_key(entry) for entry in (*desired, *base_entries)} | set(self.legacy)
        ours.discard("")
        existing = current if isinstance(current, list) else []
        return [entry for entry in existing if _hook_key(entry) not in ours] + desired

    def _ensure_list(
        self,
        current: Json | _Absent,
        desired: list[Json],
        base: Json | _Absent,
        path: tuple[str, ...],
    ) -> Json:
        if not isinstance(current, list) and current is not _ABSENT:
            return self._conflict(path, current, desired)
        retired = [x for x in base if not _contains(desired, x)] if isinstance(base, list) else []
        existing = current if isinstance(current, list) else []
        kept = [x for x in existing if not _contains(retired, x)]
        return kept + [x for x in desired if not _contains(kept, x)]

    def _leaf(
        self,
        current: Json | _Absent,
        desired: Json,
        base: Json | _Absent,
        path: tuple[str, ...],
    ) -> Json:
        if current is _ABSENT:
            return desired
        if _same(current, desired) or (base is not _ABSENT and _same(current, base)):
            return desired
        return self._conflict(path, current, desired)


def merge_composed(
    current: dict[str, Json],
    composed: Composed,
    base: Base,
    *,
    force: frozenset[str] = frozenset(),
) -> Merger:
    """Merge the composed fragment into current settings.

    Retired paths are governed by the base's recorded policy, or by the
    composed one when the base recorded none (written before policies were).

    ``force``: conflict paths (their rendered dotted form) to resolve as the
    fragment value instead of conflicting — the caller answers for them.

    Returns the completed Merger: read ``.merged`` (discard it when
    ``.conflicts`` is non-empty), ``.forced`` and ``.warnings``.
    """
    merger = Merger(
        policy=composed.policy,
        retire_policy=composed.policy if base.policy is None else base.policy,
        legacy=composed.legacy,
        force=force,
    )
    merged = merger.merge(current, composed.data, base.data, ())
    assert isinstance(merged, dict)  # invariant: merging two objects yields an object
    merger.merged = merged
    return merger


# Path resolution — env overrides exist for tests and manual runs.


def _env_path(var: str, default: Path) -> Path:
    return Path(os.environ.get(var, str(default)))


def settings_file() -> Path:
    return _env_path("CLAUDE_SETTINGS_FILE", Path.home() / ".claude/settings.json")


def base_file() -> Path:
    return _env_path("CLAUDE_BASE_FILE", Path.home() / ".claude/.claude-managed.applied.json")


def forced_paths() -> frozenset[str]:
    """CLAUDE_MANAGED_FORCE: comma-separated conflict paths to resolve as desired."""
    raw = os.environ.get("CLAUDE_MANAGED_FORCE", "")
    return frozenset(part for p in raw.split(",") if (part := p.strip()))


def load_base(path: Path) -> Base:
    """Last-applied composed-fragment snapshot; empty (fail-safe) when missing or malformed.

    Fail-safe direction: without a base, our own updates surface as conflicts
    instead of silently overwriting foreign changes. A malformed recorded
    policy alone drops just the policy (retirement falls back to the composed
    one), keeping the data.
    """
    empty = Base(data={}, policy=None)
    try:
        raw = json.loads(path.read_text())
    except FileNotFoundError:
        return empty
    except json.JSONDecodeError as exc:
        print(f"claude-managed: ignoring corrupt base {path}: {exc}", file=sys.stderr)
        return empty
    if not isinstance(raw, dict):
        print(f"claude-managed: ignoring non-object base {path}", file=sys.stderr)
        return empty
    try:
        return parse_base(raw)
    except ExceptionGroup as eg:
        for exc in eg.exceptions:
            print(f"claude-managed: ignoring base policy in {path}: {exc}", file=sys.stderr)
        return Base(data=strip_reserved(raw), policy=None)


def parse_fragments(rendered: str) -> list[tuple[str, dict[str, Json]]]:
    """Parse the fragments a wrapper embeds — ``[[name, text], ...]`` — in name order.

    A corrupt fragment is a hard error (they are chezmoi-managed; fail loudly).

    Raises:
        ExceptionGroup: of json.JSONDecodeError (fragment context via ``add_note``)
        and TypeError, collected across every fragment, not just the first.
    """
    fragments: list[tuple[str, dict[str, Json]]] = []
    errors: list[Exception] = []
    for name, text in sorted(json.loads(rendered)):
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError as exc:
            exc.add_note(f"{name}: invalid JSON")
            errors.append(exc)
            continue
        if not isinstance(parsed, dict):
            errors.append(TypeError(f"{name}: fragment must be a JSON object"))
            continue
        fragments.append((name, parsed))
    if errors:
        raise ExceptionGroup("invalid fragments", errors)
    return fragments


def _describe(exc: BaseException) -> str:
    """One-line rendering, ``add_note`` context first: `name.json: invalid JSON: …`."""
    return ": ".join((*getattr(exc, "__notes__", ()), str(exc)))


def main(rendered: str, source: str) -> int:
    """The modify_ filter over the ``rendered`` fragments; ``source`` names where they
    live, for the messages."""
    raw = sys.stdin.read()
    has_input = bool(raw.strip())
    try:
        current: Json = json.loads(raw) if has_input else {}
    except json.JSONDecodeError as exc:
        print(f"claude-managed: settings.json is not valid JSON: {exc}", file=sys.stderr)
        return 1
    if not isinstance(current, dict):
        print(
            f"claude-managed: settings.json is not a JSON object: {current!r}",
            file=sys.stderr,
        )
        return 1

    try:
        composed = compose(parse_fragments(rendered))
    except ExceptionGroup as eg:
        for exc in eg.exceptions:
            print(f"⚠ claude-managed: {_describe(exc)}", file=sys.stderr)
        print(
            f"  settings.json left untouched. Fix the fragments in {source}, then re-run.",
            file=sys.stderr,
        )
        return 1

    force = forced_paths()
    result = merge_composed(current, composed, load_base(base_file()), force=force)

    for path in sorted(force - {c.path for c in result.forced}):
        print(
            f"claude-managed: CLAUDE_MANAGED_FORCE `{path}` matched no conflict; ignored",
            file=sys.stderr,
        )

    if result.conflicts:
        for c in result.conflicts:
            print(
                f"⚠ claude-managed: conflict at `{c.path}`\n"
                f"    current: {json.dumps(c.current, ensure_ascii=False)}\n"
                f"    desired: {json.dumps(c.desired, ensure_ascii=False)}",
                file=sys.stderr,
            )
        # Nothing applied this run, so the retry command re-includes paths
        # already consumed by force — they conflict again next run without it.
        retry = ",".join(c.path for c in (*result.conflicts, *result.forced))
        print(
            "  settings.json left untouched. Take a live value by aligning its fragment"
            f" in {source}; take the desired values with:\n"
            f"    CLAUDE_MANAGED_FORCE='{retry}' chezmoi apply",
            file=sys.stderr,
        )
        return 1

    for c in result.forced:
        print(
            f"claude-managed: forced `{c.path}`:"
            f" {json.dumps(c.current, ensure_ascii=False)}"
            f" -> {json.dumps(c.desired, ensure_ascii=False)}",
            file=sys.stderr,
        )
    for message in result.warnings:
        print(f"claude-managed: {message}", file=sys.stderr)

    if has_input and _same(result.merged, current):
        # Byte-stable no-op: never let formatting differences show up as drift.
        sys.stdout.write(raw)
    else:
        json.dump(result.merged, sys.stdout, indent=2, ensure_ascii=False)
        sys.stdout.write("\n")
    return 0


def snapshot(rendered: str) -> int:
    """Advance the base once the settings merge has converged.

    Runs after all targets, including the settings merge, have been written. The
    base only advances after VERIFYING that live settings == merge result with
    zero conflicts. If the fragments don't compose, or the merge target failed or
    was skipped, the base stays put — a stale base can only produce fail-safe
    conflicts, never silent overwrites, and it can never run ahead of settings and
    manufacture false ones. Each refusal is a quiet no-op: the filter already
    reported the real problem this apply.
    """
    try:
        composed = compose(parse_fragments(rendered))
    except ExceptionGroup:
        print(
            "claude-managed: fragments not composable; base snapshot left untouched",
            file=sys.stderr,
        )
        return 0

    try:
        current = json.loads(settings_file().read_text())
    except FileNotFoundError:
        current = {}
    except json.JSONDecodeError:
        print(
            "claude-managed: settings.json is not valid JSON; base snapshot left untouched",
            file=sys.stderr,
        )
        return 0
    if not isinstance(current, dict):
        print(
            "claude-managed: settings.json is not a JSON object; base snapshot left untouched",
            file=sys.stderr,
        )
        return 0

    path = base_file()
    result = merge_composed(current, composed, load_base(path))
    if result.conflicts or not _same(result.merged, current):
        print(
            "claude-managed: settings not converged; base snapshot left untouched", file=sys.stderr
        )
        return 0

    text = json.dumps(base_snapshot(composed), indent=2, ensure_ascii=False) + "\n"
    try:
        if path.read_text() == text:
            return 0
    except FileNotFoundError:
        pass
    path.write_text(text)
    print("✓ claude-managed: base snapshot advanced")
    return 0
