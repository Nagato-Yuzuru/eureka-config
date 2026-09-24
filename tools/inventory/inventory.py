"""The inventory: the three data files (.chezmoidata/*.toml) loaded, validated, and resolved per machine.

Resolution is the one place the domain rules live: a selector matches a machine
through its platform's OS; matching overrides deep-merge over the package (maps
merge, lists and scalars replace) and the result is parsed as a whole again, so
an override never produces a shape the types would reject; `via: system` picks
the platform's package manager. On each machine, no two packages may install the same
thing or generate the same command's completion.
"""

from __future__ import annotations

import tomllib
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any, assert_never, get_args

from pydantic import TypeAdapter, ValidationError

from inventory.model import (
    BACKENDS,
    DOCS,
    PACKAGE,
    VIAS,
    Arch,
    Brew,
    Cask,
    CoprRpm,
    Dnf,
    Formula,
    Machine,
    Mise,
    MisePackage,
    OsArchSel,
    OsSel,
    Override,
    Package,
    Platform,
    RepofileRpm,
    Resolved,
    Rpm,
    Selector,
    Spec,
    SystemPackage,
    Upstream,
    UpstreamPackage,
    Uv,
    UvPackage,
)

DATA_FILES = ("machines.toml", "packages.toml", "platforms.toml")

_PLATFORMS = TypeAdapter(dict[str, Platform])
_MACHINES = TypeAdapter(dict[str, Machine])
_PACKAGES = TypeAdapter(dict[str, Any])  # shape only; each line is parsed on its own below


# The blocks that are unions: the variants' names as pydantic spells them in an error
# location, the fields any variant knows, and the one-line statement of the choice.
_UNIONS = {"brew": get_args(Brew), "dnf": get_args(Dnf)}
_VARIANTS = {b: {v.__name__ for v in vs} for b, vs in _UNIONS.items()}
_FIELDS = {b: {f for v in vs for f in v.model_fields} for b, vs in _UNIONS.items()}
_CHOICES = {
    "brew": "a formula ({ name }) or a cask ({ cask }), not both",
    "dnf": "the standard repos ({ name }), a COPR ({ copr }) or a .repo file ({ repofile }), one",
}


class InventoryError(ValueError):
    """Bad data, with the message naming the entry (`packages.foo.mise.version: …`)."""


def load_data(path: Path) -> dict[str, Any]:
    return tomllib.loads(path.read_text())


def _explain(prefix: str, err: ValidationError, tags: Sequence[str] = ()) -> str:
    """One `<path>: <why>` line per error, the path spelled the way packages.toml reads:
    `packages.foo.mise.components`, `packages.foo.overrides[linux].via` (`tags` are the
    overrides' `when`, by position)."""
    lines: list[str] = []
    for e in err.errors():
        loc = [str(x) for x in e["loc"]]
        tag = loc.pop(0) if loc and loc[0] in VIAS else None  # the discriminator's segment
        msg = e["msg"].removeprefix("Value error, ")
        match e["type"]:
            case "union_tag_invalid":
                msg = f"via must be {'|'.join(VIAS)}"
            case "model_type":
                msg = "must be a map"
            case "extra_forbidden" if tag and len(loc) == 1 and loc[0] in BACKENDS:
                msg = f"no {loc[0]} block on a `via: {tag}` package"
        if len(loc) >= 2 and loc[0] == "overrides" and loc[1].isdigit():
            i = int(loc[1])
            loc[0:2] = [f"overrides[{tags[i] if i < len(tags) else '?'}]"]
            if e["type"] == "extra_forbidden" and len(loc) == 2:  # only the how may be patched
                loc, msg = loc[:1], f"cannot override {loc[1]!r}"
        # inside a union block (`brew`, `dnf`), wherever it sits: one choice, not per variant
        for i in range(len(loc) - 1):
            if loc[i] in _UNIONS and loc[i + 1] in _VARIANTS[loc[i]]:
                if e["type"] == "extra_forbidden" and loc[-1] not in _FIELDS[loc[i]]:
                    del loc[i + 1]  # a key no variant has: name it, not the choice
                else:
                    loc, msg = loc[: i + 1], _CHOICES[loc[i]]
                break
        path = ".".join(filter(None, [prefix, *loc]))
        line = f"{path}: {msg}"
        if line not in lines:
            lines.append(line)
    # a key no variant has explains a block by itself; the choice line would only add noise
    for block, choice in _CHOICES.items():
        named = {ln.rsplit(f".{block}.", 1)[0] for ln in lines if f".{block}." in ln}
        lines = [ln for ln in lines if not any(ln == f"{n}.{block}: {choice}" for n in named)]
    return "\n".join(lines)


def _when_tags(raw: Any) -> list[str]:
    """`when` of each override of a not-yet-validated line, for error paths only."""
    try:
        return [str(o["when"]) for o in raw["overrides"]]
    except (KeyError, TypeError):
        return []


def deep_merge(dst: Mapping[str, Any], src: Mapping[str, Any]) -> dict[str, Any]:
    """`src` over `dst`, recursively for maps; lists and scalars replace. Neither is mutated."""
    out = dict(dst)
    for k, v in src.items():
        if isinstance(v, Mapping) and isinstance(out.get(k), Mapping):
            out[k] = deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def matches_os_arch(sel: Selector, os_: str, arch: str) -> bool:
    match sel:
        case OsSel(os=o):
            return o == os_
        case OsArchSel(os=o, arch=a):
            return o == os_ and a == arch
        # `Selector` is an open sum (model.py). Without this a new variant falls off the end
        # returning None, `any(...)` reads that as False, and the package installs nowhere —
        # silently, on every machine. The arm makes the type checker demand the missing case.
        case _:
            assert_never(sel)


def matches(sel: Selector, machine: Machine, platforms: Mapping[str, Platform]) -> bool:
    return matches_os_arch(sel, platforms[machine.platform].os, machine.arch)


def parse_package(prefix: str, raw: Any) -> Package:
    """One line of packages.toml -> its typed form, or InventoryError naming what is wrong."""
    try:
        return PACKAGE.validate_python(raw)
    except ValidationError as e:
        raise InventoryError(_explain(prefix, e, _when_tags(raw))) from None


# In a union block, the fields that tell the variants apart (`cask` for brew, `copr` and
# `repofile` for dnf — and `name`, which Cask lacks); the rest is common to all variants.
_DISCRIMINATING = {
    b: {f for v in vs for f in v.model_fields}
    - set.intersection(*(set(v.model_fields) for v in vs))
    for b, vs in _UNIONS.items()
}

# The subset that *chooses* a variant: required by one of them (`cask`, `copr`, `repofile`).
# Only these may clear the block. `name` is discriminating but optional — it is absent from
# Cask by accident of shape, not because writing it means "make this a formula", and a rename
# patch that silently demoted a cask would be indistinguishable from one that renamed it.
_SELECTS_VARIANT = {
    b: {f for v in vs for f, info in v.model_fields.items() if info.is_required()}
    for b, vs in _UNIONS.items()
}


def patched(pkg: Package, overrides: Iterable[Override]) -> dict[str, Any]:
    """The package's own fields with the overrides merged over them, in order — the raw
    form of what `parse_package` turns back into a package. Two things replace instead of
    merging, because merging could only produce a shape the types reject: a patch that
    switches `via` starts the how afresh (the previous backend's block goes), and a patch
    that chooses a different variant of a union block (formula -> cask, copr -> repofile)
    drops the block's previous discriminating fields. A patch that only sets a shared or
    optional field is a merge into the variant already chosen — patching `name` onto a cask
    is a rename the types then reject, not a demotion to a formula."""
    eff = pkg.model_dump(exclude_unset=True, by_alias=True, exclude={"overrides"})
    via = pkg.via
    for ov in overrides:
        patch = ov.patch
        if patch.get("via", via) != via:
            eff = {k: v for k, v in eff.items() if k not in BACKENDS}
            via = patch["via"]
        for block, disc in _DISCRIMINATING.items():
            if block in eff and _SELECTS_VARIANT[block] & set(patch.get(block, {})):
                eff[block] = {k: v for k, v in eff[block].items() if k not in disc}
        eff = deep_merge(eff, patch)
    return eff


def _tag_machines(sel: Selector) -> list[tuple[str, str]]:
    """The (os, arch) pairs a tag stands for — every arch for a bare OS tag."""
    match sel:
        case OsSel(os=o):
            return [(o, a) for a in get_args(Arch)]
        case OsArchSel(os=o, arch=a):
            return [(o, a)]
        case _:
            assert_never(sel)


def _installs(spec: Spec, name: str) -> tuple[str, str]:
    """What a resolved package occupies on its machine, as (kind, identity): two packages
    with the same pair would install one thing twice. Formula and cask are separate
    namespaces in a Brewfile; an upstream package is named by its key, so its identity is
    the command it runs."""
    match spec:
        case Formula():
            return "brew formula", name
        case Cask():
            return "brew cask", name
        case Rpm() | CoprRpm() | RepofileRpm():
            return "dnf package", name
        case Mise():
            return "mise tool", name
        case Uv():
            return "uv tool", name
        case Upstream(command=command):
            return "upstream command", command
        case _:
            assert_never(spec)


class Inventory:
    def __init__(self, platforms: Any, machines: Any, packages: Any) -> None:
        try:
            self.platforms: dict[str, Platform] = _PLATFORMS.validate_python(platforms)
        except ValidationError as e:
            raise InventoryError(_explain("platforms", e)) from None
        try:
            self.machines: dict[str, Machine] = _MACHINES.validate_python(machines)
        except ValidationError as e:
            raise InventoryError(_explain("machines", e)) from None
        for mid, m in self.machines.items():
            if m.platform not in self.platforms:
                raise InventoryError(f"machines.{mid}: unknown platform {m.platform!r}")
        try:
            raw_packages = _PACKAGES.validate_python(packages)
        except ValidationError as e:
            raise InventoryError(_explain("packages", e)) from None
        self.packages: dict[str, Package] = {}
        for key, raw in raw_packages.items():
            pkg = parse_package(f"packages.{key}", raw)
            # Every override checked as it would apply: cumulatively, in list order, on each
            # machine its tag stands for — declared or not, so a patch whose tag matches no
            # machine fails here too, and one that only works after an earlier entry passes.
            for i, ov in enumerate(pkg.overrides):
                for os_, arch in _tag_machines(ov.when):
                    upto = [o for o in pkg.overrides[: i + 1] if matches_os_arch(o.when, os_, arch)]
                    parse_package(f"packages.{key}.overrides[{ov.when.tag}]", patched(pkg, upto))
            self.packages[key] = pkg
        for mid in self.machines:
            self._check_unique(mid)

    def _check_unique(self, machine_id: str) -> None:
        """Two packages installing one thing on a machine would render it twice (a mise
        `[tools]` key twice is not even valid TOML); two claiming one command's completion
        would race for its one `_<cmd>` file."""
        installs: dict[tuple[str, str], str] = {}
        commands: dict[str, str] = {}
        for r in self.resolved(machine_id):
            kind, ident = _installs(r.spec, r.name)
            if (other := installs.setdefault((kind, ident), r.key)) != r.key:
                raise InventoryError(
                    f"packages.{r.key}: {kind} {ident!r} on {machine_id} is already"
                    f" installed by packages.{other}"
                )
            for cmd in r.completions:
                if (other := commands.setdefault(cmd, r.key)) != r.key:
                    raise InventoryError(
                        f"packages.{r.key}.completions.{cmd}: the completion for {cmd!r} on"
                        f" {machine_id} is already generated by packages.{other}"
                    )

    @classmethod
    def load(cls, data_dir: Path) -> Inventory:
        """Every .chezmoidata file checked whole against its document model — a stray or
        misspelt top-level table, an empty key, xdg/zsh typos all fail here. An error
        inside a packages.toml entry is left to the per-entry parse below, whose message
        names the override and the choice."""
        docs = {f: load_data(data_dir / f) for f in DOCS}
        for name, doc in DOCS.items():
            try:
                doc.model_validate(docs[name])
            except ValidationError as e:
                if name == "packages.toml" and all(len(x["loc"]) > 2 for x in e.errors()):
                    continue
                raise InventoryError(_explain("", e)) from None
        return cls(
            docs["platforms.toml"]["platforms"],
            docs["machines.toml"]["machines"],
            docs["packages.toml"]["packages"],
        )

    # --- resolution ---------------------------------------------------------------------------

    def effective(self, key: str, machine_id: str) -> Package:
        """The package with this machine's overrides merged in — what it means here."""
        m, pkg = self.machines[machine_id], self.packages[key]
        matching = [ov for ov in pkg.overrides if matches(ov.when, m, self.platforms)]
        if not matching:
            return pkg
        return parse_package(f"packages.{key}[{machine_id}]", patched(pkg, matching))

    def resolve(self, key: str, machine_id: str) -> Resolved | None:
        """How `key` installs on `machine_id`; None when it does not."""
        m = self.machines[machine_id]
        pkg = self.effective(key, machine_id)
        if not any(matches(sel, m, self.platforms) for sel in pkg.on):
            return None
        match pkg:
            case SystemPackage():
                if self.platforms[m.platform].system == "brew":
                    spec = pkg.brew
                    name = spec.cask if isinstance(spec, Cask) else spec.name
                else:
                    spec = pkg.dnf
                    name = spec.name
            case MisePackage():
                spec, name = pkg.mise, pkg.mise.name
            case UvPackage():
                spec, name = pkg.uv, pkg.uv.name
            case UpstreamPackage():
                spec, name = pkg.upstream, None  # named by its key
        return Resolved(key=key, name=name or key, spec=spec, completions=pkg.completions)

    def resolved(self, machine_id: str) -> list[Resolved]:
        """Every package that installs on `machine_id`, in key order (what the renderers take)."""
        if machine_id not in self.machines:
            raise InventoryError(f"machines.{machine_id}: not declared")
        return [r for key in sorted(self.packages) if (r := self.resolve(key, machine_id))]
