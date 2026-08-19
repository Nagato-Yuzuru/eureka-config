"""The inventory: the three data files loaded, validated, and resolved per machine.

Resolution is the one place the domain rules live: a selector matches a machine
through its platform's OS; matching overrides deep-merge over the package (maps
merge, lists and scalars replace) and the result is parsed as a whole again, so
an override never produces a shape the types would reject; `via: system` picks
the platform's package manager.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

import yaml
from pydantic import TypeAdapter, ValidationError

from inventory.model import (
    BACKENDS,
    PACKAGE,
    VIAS,
    Cask,
    Machine,
    MisePackage,
    OsArchSel,
    OsSel,
    Override,
    Package,
    Platform,
    Resolved,
    Selector,
    SystemPackage,
    UvPackage,
)

DATA_FILES = ("machines.yaml", "packages.yaml", "platforms.yaml")

_PLATFORMS = TypeAdapter(dict[str, Platform])
_MACHINES = TypeAdapter(dict[str, Machine])
_PACKAGES = TypeAdapter(dict[str, Any])  # shape only; each line is parsed on its own below


class InventoryError(ValueError):
    """Bad data, with the message naming the entry (`packages.foo.mise.version: …`)."""


class _Yaml12(yaml.SafeLoader):
    """pyyaml speaks YAML 1.1, where `on`/`off`/`yes`/`no` are booleans — and `on:` is
    our most common key. chezmoi's parser (YAML 1.2) reads them as strings; so do we."""


_Yaml12.yaml_implicit_resolvers = {
    ch: [(tag, rx) for tag, rx in rs if tag != "tag:yaml.org,2002:bool"]
    for ch, rs in yaml.SafeLoader.yaml_implicit_resolvers.items()
}
_Yaml12.add_implicit_resolver(
    "tag:yaml.org,2002:bool", re.compile(r"^(?:true|True|TRUE|false|False|FALSE)$"), list("tTfF")
)


def load_yaml(path: Path) -> Any:
    return yaml.load(path.read_text(), Loader=_Yaml12)  # noqa: S506 — a SafeLoader subclass


def _explain(prefix: str, err: ValidationError, tags: Sequence[str] = ()) -> str:
    """One `<path>: <why>` line per error, the path spelled the way packages.yaml reads:
    `packages.foo.mise.components`, `packages.foo.overrides[linux].via` (`tags` are the
    overrides' `$when`, by position)."""
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
            case "extra_forbidden" if tag and loc and loc[-1] in BACKENDS:
                msg = f"no {loc[-1]} block on a `via: {tag}` package"
        if "Formula" in loc or "Cask" in loc:  # the Brew union: report it once, as one choice
            loc = loc[: loc.index("brew") + 1] if "brew" in loc else loc
            msg = "a formula ({ name }) or a cask ({ cask }), not both"
        if len(loc) >= 2 and loc[0] == "overrides" and loc[1].isdigit():
            i = int(loc[1])
            loc[0:2] = [f"overrides[{tags[i] if i < len(tags) else '?'}]"]
        path = ".".join([prefix, *loc]) if loc else prefix
        line = f"{path}: {msg}"
        if line not in lines:
            lines.append(line)
    return "\n".join(lines)


def _when_tags(raw: Any) -> list[str]:
    """`$when` of each override of a not-yet-validated line, for error paths only."""
    try:
        return [str(o["$when"]) for o in raw["overrides"]]
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


def matches(sel: Selector, machine: Machine, platforms: Mapping[str, Platform]) -> bool:
    os_ = platforms[machine.platform].os
    match sel:
        case OsSel(os=o):
            return o == os_
        case OsArchSel(os=o, arch=a):
            return o == os_ and a == machine.arch


def parse_package(prefix: str, raw: Any) -> Package:
    """One line of packages.yaml -> its typed form, or InventoryError naming what is wrong."""
    try:
        return PACKAGE.validate_python(raw)
    except ValidationError as e:
        raise InventoryError(_explain(prefix, e, _when_tags(raw))) from None


def patched(pkg: Package, overrides: Iterable[Override]) -> dict[str, Any]:
    """The package's own fields with the overrides merged over them, in order — the raw
    form of what `parse_package` turns back into a package. A patch that switches `via`
    starts the how afresh: the previous backend's block goes, only what it brings applies."""
    eff = pkg.model_dump(exclude_unset=True, by_alias=True, exclude={"overrides"})
    via = pkg.via
    for ov in overrides:
        if ov.patch.get("via", via) != via:
            eff = {k: v for k, v in eff.items() if k not in BACKENDS}
            via = ov.patch["via"]
        eff = deep_merge(eff, ov.patch)
    return eff


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
            for ov in pkg.overrides:  # a bad patch fails here, even one whose tag matches nothing
                parse_package(f"packages.{key}.overrides[{ov.when.tag}]", patched(pkg, [ov]))
            self.packages[key] = pkg

    @classmethod
    def load(cls, data_dir: Path) -> Inventory:
        docs = {f: load_yaml(data_dir / f) for f in DATA_FILES}
        try:
            return cls(
                docs["platforms.yaml"]["platforms"],
                docs["machines.yaml"]["machines"],
                docs["packages.yaml"]["packages"],
            )
        except (KeyError, TypeError) as e:
            raise InventoryError(f"a data file lacks its top-level key: {e}") from None

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
                backend = self.platforms[m.platform].system
                if backend == "brew":
                    spec = pkg.brew
                    name = spec.cask if isinstance(spec, Cask) else spec.name
                else:
                    spec = pkg.dnf
                    name = spec.name
            case MisePackage():
                backend, spec, name = "mise", pkg.mise, pkg.mise.name
            case UvPackage():
                backend, spec, name = "uv", pkg.uv, pkg.uv.name
        return Resolved(
            key=key, backend=backend, name=name or key, spec=spec, completions=pkg.completions
        )

    def resolved(self, machine_id: str) -> list[Resolved]:
        """Every package that installs on `machine_id`, in key order (what the renderers take)."""
        if machine_id not in self.machines:
            raise InventoryError(f"machines.{machine_id}: not declared")
        return [r for key in sorted(self.packages) if (r := self.resolve(key, machine_id))]
