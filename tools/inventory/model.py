"""What .chezmoidata/{platforms,machines,packages}.yaml may say — the types are the schema.

A package line answers three questions: where it goes (`on`), how it gets there
(`via` plus that backend's block), and which zsh completions it generates
(`completions`); `overrides` patches the how per machine. `via` is the
discriminator: `system` (the machine's platform picks brew or dnf, so both
blocks may appear) or exactly one tool backend with exactly its block. Which
system package manager a platform has is a platform fact (`Platform.system`),
declared once, never assumed from the OS.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    StringConstraints,
    Tag,
    TypeAdapter,
    field_validator,
    model_validator,
)

Os = Literal["darwin", "linux"]
Arch = Literal["arm64", "amd64"]
SystemBackend = Literal["brew", "dnf"]
Backend = Literal["brew", "dnf", "mise", "uv"]
BACKENDS: tuple[Backend, ...] = ("brew", "dnf", "mise", "uv")
VIAS = ("system", "mise", "uv")

NonEmpty = Annotated[str, StringConstraints(min_length=1)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- machines: who ------------------------------------------------------------------------------


class Platform(Strict):
    """A kind of machine: its OS and the package manager that comes with it."""

    os: Os
    system: SystemBackend


class Machine(Strict):
    """One render target. Keyed `<os>-<arch>` today; a specific device is one more entry."""

    platform: NonEmpty
    arch: Arch


class OsSel(Strict):
    os: Os


class OsArchSel(Strict):
    os: Os
    arch: Arch


# `on:` tags. An open sum: a platform- or device-level selector is one more class
# and one more arm in `matches` (inventory.py); existing data does not change.
Selector = OsSel | OsArchSel

_OS: tuple[str, ...] = ("darwin", "linux")
_ARCH: tuple[str, ...] = ("arm64", "amd64")


def parse_selector(v: Any) -> Any:
    if isinstance(v, OsSel | OsArchSel):
        return v
    if not isinstance(v, str):
        raise ValueError("tags are strings: darwin, linux, or os/arch")
    os_, sep, arch = v.partition("/")
    if os_ not in _OS or (sep and arch not in _ARCH):
        raise ValueError(f"unknown tag {v!r}")
    return OsArchSel(os=os_, arch=arch) if sep else OsSel(os=os_)  # type: ignore[arg-type]


SelectorField = Annotated[Selector, BeforeValidator(parse_selector)]


# --- install: how -------------------------------------------------------------------------------


class Formula(Strict):
    name: NonEmpty | None = None


class Cask(Strict):
    cask: NonEmpty


Brew = Formula | Cask  # brew's two flavours are two variants, not two optional fields


class Dnf(Strict):
    name: NonEmpty | None = None
    copr: NonEmpty | None = None  # Fedora COPR repo (owner/project) that ships the package
    repofile: NonEmpty | None = None  # URL of a third-party .repo file to add first


class Mise(Strict):
    name: NonEmpty | None = None  # mise's name, incl. backend prefix ("npm:pnpm", "go:…")
    version: NonEmpty = "latest"
    components: list[NonEmpty] | None = None  # extra components installed with the tool

    @field_validator("components")
    @classmethod
    def _non_empty(cls, v: list[str] | None) -> list[str] | None:
        if v is not None and not v:
            raise ValueError("must be a non-empty list")
        return v


class Uv(Strict):
    name: NonEmpty | None = None  # name on PyPI
    version: NonEmpty | None = None  # spelled `name@version` for `uv tool install`


Spec = Formula | Cask | Dnf | Mise | Uv


class Override(BaseModel):
    """`{ $when: <tag>, <field>: <value>, … }`: a patch on the how, deep-merged over the
    package on machines the tag matches, in list order. Any of via/brew/dnf/mise/uv;
    never on/completions/overrides. Switching `via` drops the previous backend's block."""

    model_config = ConfigDict(extra="allow", frozen=True)

    when: Annotated[SelectorField, Field(alias="$when")]

    @property
    def patch(self) -> dict[str, Any]:
        return dict(self.model_extra or {})

    @model_validator(mode="after")
    def _only_the_how(self) -> Override:
        bad = sorted(set(self.patch) - {"via", *BACKENDS})
        if bad:
            raise ValueError(f"cannot override {', '.join(repr(k) for k in bad)}")
        return self


class _Package(Strict):
    on: Annotated[list[SelectorField], Field(min_length=1)]
    completions: dict[NonEmpty, NonEmpty] = {}  # command -> sh command printing its completion
    overrides: list[Override] = []

    @field_validator("completions")
    @classmethod
    def _non_empty(cls, v: dict[str, str]) -> dict[str, str]:
        if not v:
            raise ValueError("must be a non-empty map of command -> recipe")
        return v


class SystemPackage(_Package):
    via: Literal["system"] = "system"
    brew: Brew = Formula()
    dnf: Dnf = Dnf()


class MisePackage(_Package):
    via: Literal["mise"]
    mise: Mise = Mise()


class UvPackage(_Package):
    via: Literal["uv"]
    uv: Uv = Uv()


def _via(v: Any) -> str:
    return v.get("via", "system") if isinstance(v, dict) else getattr(v, "via", "system")


Package = Annotated[
    Annotated[SystemPackage, Tag("system")]
    | Annotated[MisePackage, Tag("mise")]
    | Annotated[UvPackage, Tag("uv")],
    Discriminator(_via),
]
PACKAGE = TypeAdapter(Package)


# --- resolved: what a renderer sees ------------------------------------------------------------


class Resolved(Strict):
    """One package as it installs on one machine. The only input the renderers take."""

    key: NonEmpty
    backend: Backend
    name: NonEmpty  # name on that backend (the cask's, for a cask)
    spec: Spec
    completions: dict[NonEmpty, NonEmpty]
