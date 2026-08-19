"""The inventory: the three data files loaded, validated, and resolved per machine.

Resolution is the one place the domain rules live: a selector matches a machine
through its platform's OS; matching overrides deep-merge over the package (maps
merge, lists and scalars replace) and the result is parsed as a whole again, so
an override never produces a shape the types would reject; `via: system` picks
the platform's package manager.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
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


def _explain(prefix: str, err: ValidationError, raw: Mapping[str, Any] | None = None) -> str:
    """One `<path>: <why>` line per error, the path spelled the way packages.yaml reads:
    `packages.foo.mise.components`, `packages.foo.overrides[linux].via`."""
    overrides = raw.get("overrides", []) if isinstance(raw, Mapping) else []
    lines: list[str] = []
    for e in err.errors():
        loc = [str(x) for x in e["loc"]]
        tag = loc.pop(0) if loc and loc[0] in VIAS else None  # the discriminator's segment
        msg = e["msg"].removeprefix("Value error, ")
        if e["type"] == "extra_forbidden" and loc and loc[-1] in BACKENDS and tag:
            msg = f"no {loc[-1]} block on a `via: {tag}` package"
        if "Formula" in loc or "Cask" in loc:  # the Brew union: report it once, as one choice
            loc = loc[: loc.index("brew") + 1] if "brew" in loc else loc
            msg = "a formula ({ name }) or a cask ({ cask }), not both"
        if len(loc) >= 2 and loc[0] == "overrides" and loc[1].isdigit():
            i = int(loc[1])
            when = overrides[i].get("$when", "?") if i < len(overrides) else "?"
            loc[0:2] = [f"overrides[{when}]"]
        path = ".".join([prefix, *loc]) if loc else prefix
        line = f"{path}: {msg}"
        if line not in lines:
            lines.append(line)
    return "\n".join(lines)


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


class Inventory:
    def __init__(
        self,
        platforms: Mapping[str, Any],
        machines: Mapping[str, Any],
        packages: Mapping[str, Any],
    ) -> None:
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
        if not isinstance(packages, Mapping):
            raise InventoryError("packages: must be a map")
        self._raw: dict[str, dict[str, Any]] = {}
        for key, item in packages.items():
            self._raw[key] = self._check_package(key, item)

    @classmethod
    def load(cls, data_dir: Path) -> Inventory:
        docs = {f: load_yaml(data_dir / f) or {} for f in DATA_FILES}
        return cls(
            docs["platforms.yaml"].get("platforms", {}),
            docs["machines.yaml"].get("machines", {}),
            docs["packages.yaml"].get("packages", {}),
        )

    # --- validation ---------------------------------------------------------------------------

    @staticmethod
    def _parse(prefix: str, raw: Mapping[str, Any]) -> Package:
        if not isinstance(raw, Mapping):
            raise InventoryError(f"{prefix}: must be a map")
        via = raw.get("via", "system")
        if via not in VIAS:
            raise InventoryError(f"{prefix}: via must be {'|'.join(VIAS)}")
        try:
            return PACKAGE.validate_python(raw)
        except ValidationError as e:
            raise InventoryError(_explain(prefix, e, raw)) from None

    def _check_package(self, key: str, item: Any) -> dict[str, Any]:
        """The package as declared, and each override applied on its own — so a bad patch
        fails here, even one whose tag matches no machine."""
        pkg = self._parse(f"packages.{key}", item)
        for i, ov in enumerate(pkg.overrides):
            when = item["overrides"][i]["$when"]
            self._parse(f"packages.{key}.overrides[{when}]", self._patched(item, [ov.patch]))
        return dict(item)

    @staticmethod
    def _patched(item: Mapping[str, Any], patches: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        """The patches over the item, in order. A patch that switches `via` starts the how
        afresh: the previous backend's block goes, only what the patch brings applies."""
        eff = {k: v for k, v in item.items() if k != "overrides"}
        for p in patches:
            if "via" in p and p["via"] != eff.get("via", "system"):
                eff = {k: v for k, v in eff.items() if k not in BACKENDS}
            eff = deep_merge(eff, p)
        return eff

    # --- resolution ---------------------------------------------------------------------------

    def effective(self, key: str, machine_id: str) -> Package:
        """The package with this machine's overrides merged in — what it means here."""
        m = self.machines[machine_id]
        raw = self._raw[key]
        base = self._parse(f"packages.{key}", raw)
        patches = [ov.patch for ov in base.overrides if matches(ov.when, m, self.platforms)]
        if not patches:
            return base
        return self._parse(f"packages.{key}[{machine_id}]", self._patched(raw, patches))

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
        return [r for key in sorted(self._raw) if (r := self.resolve(key, machine_id))]
