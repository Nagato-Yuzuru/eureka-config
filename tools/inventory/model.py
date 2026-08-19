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

from collections.abc import Mapping
from typing import Annotated, Any, Literal, get_args

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Discriminator,
    Field,
    PlainSerializer,
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
Via = Literal["system", "mise", "uv"]
BACKENDS: tuple[Backend, ...] = get_args(Backend)
VIAS: tuple[Via, ...] = get_args(Via)

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

    @property
    def tag(self) -> str:
        return self.os


class OsArchSel(Strict):
    os: Os
    arch: Arch

    @property
    def tag(self) -> str:
        return f"{self.os}/{self.arch}"


# `on:` tags. An open sum: a platform- or device-level selector is one more class
# and one more arm in `matches` (inventory.py); existing data does not change.
Selector = OsSel | OsArchSel


def parse_selector(v: Any) -> Selector:
    """`darwin` | `darwin/amd64` (or an already-parsed selector) -> the selector."""
    if isinstance(v, OsSel | OsArchSel):
        return v
    if not isinstance(v, str):
        raise ValueError("tags are strings: darwin, linux, or os/arch")
    os_, sep, arch = v.partition("/")
    if os_ not in get_args(Os) or (sep and arch not in get_args(Arch)):
        raise ValueError(f"unknown tag {v!r}")
    return (
        OsArchSel.model_validate({"os": os_, "arch": arch})
        if sep
        else OsSel.model_validate({"os": os_})
    )


# Round-trips as its tag, so a dumped package re-parses and error paths read like the file.
SelectorField = Annotated[
    Selector, BeforeValidator(parse_selector), PlainSerializer(lambda s: s.tag, return_type=str)
]


# --- install: how -------------------------------------------------------------------------------


# Every block's `name` is the package's name on that backend when it differs from the
# key; absent means the key. A block with nothing set is the plain install and the
# default (`git: { on: [...] }` is a Formula() on macos, an Rpm() on fedora).


class Formula(Strict):
    name: NonEmpty | None = None


class Cask(Strict):
    cask: NonEmpty


Brew = Formula | Cask  # brew's two flavours are two variants, not two optional fields


class Rpm(Strict):
    """From Fedora's standard repos."""

    name: NonEmpty | None = None


class CoprRpm(Strict):
    """From a COPR repo (owner/project), enabled first."""

    name: NonEmpty | None = None
    copr: NonEmpty


class RepofileRpm(Strict):
    """From a third-party repo whose .repo file (URL) is added first."""

    name: NonEmpty | None = None
    repofile: NonEmpty


Dnf = Rpm | CoprRpm | RepofileRpm  # where dnf gets it: exactly one source


class Mise(Strict):
    name: NonEmpty | None = None  # mise's name, incl. backend prefix ("npm:pnpm", "go:…")
    version: NonEmpty = "latest"
    components: list[NonEmpty] = []  # extra components installed with the tool

    @field_validator("components")
    @classmethod
    def _non_empty(cls, v: list[str]) -> list[str]:
        if not v:
            raise ValueError("must be a non-empty list")
        return v


class Uv(Strict):
    name: NonEmpty | None = None  # name on PyPI
    version: NonEmpty | None = None  # spelled `name@version` for `uv tool install`


Spec = Brew | Dnf | Mise | Uv


class Override(BaseModel):
    """`{ $when: <tag>, <field>: <value>, … }`: a patch on the how, deep-merged over the
    package on machines the tag matches, in list order. Any of via/brew/dnf/mise/uv;
    never on/completions/overrides. Switching `via` drops the previous backend's block."""

    model_config = ConfigDict(extra="allow", frozen=True)

    when: Annotated[SelectorField, Field(alias="$when")]

    @property
    def patch(self) -> Mapping[str, Any]:
        """The fields other than `$when`, unvalidated until merged into a package."""
        return self.model_extra or {}  # pydantic types the extras Optional; extra="allow" fills it

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
    dnf: Dnf = Rpm()


class MisePackage(_Package):
    via: Literal["mise"]
    mise: Mise = Mise()


class UvPackage(_Package):
    via: Literal["uv"]
    uv: Uv = Uv()


def _via(v: Any) -> Any:
    """The discriminator: `via` of a raw line (absent means system) or of a package."""
    if isinstance(v, Mapping):
        return v.get("via", "system")
    if isinstance(v, _Package):
        return v.via
    return "system"  # anything else then fails SystemPackage's validation as "not a map"


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
