"""What .chezmoidata/{platforms,machines,packages}.toml may say — the types are the schema.

A package line answers three questions: where it goes (`on`), how it gets there
(`via` plus that backend's block), and which zsh completions it generates
(`completions`); `overrides` patches the how per machine. `via` is the
discriminator: `system` (the machine's platform picks brew or dnf, so both
blocks may appear) or exactly one tool backend with exactly its block. Which
system package manager a platform has is a platform fact (`Platform.system`),
declared once, never assumed from the OS.

`upstream` is the backend for what no package manager can install here: the
bootstrap tools (mise, uv) are the other backends, so they cannot come through
one, and their projects ship the installer that is their recommended source.
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
    WithJsonSchema,
)

Os = Literal["darwin", "linux"]
Arch = Literal["arm64", "amd64"]
SystemBackend = Literal["brew", "dnf"]
Backend = Literal["brew", "dnf", "mise", "uv", "upstream"]
Via = Literal["system", "mise", "uv", "upstream"]
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


TAGS = [*get_args(Os), *(f"{o}/{a}" for o in get_args(Os) for a in get_args(Arch))]

# Round-trips as its tag, so a dumped package re-parses and error paths read like the file;
# to JSON Schema it is the tag too (an enum), not the parsed object.
SelectorField = Annotated[
    Selector,
    BeforeValidator(parse_selector),
    PlainSerializer(lambda s: s.tag, return_type=str),
    WithJsonSchema(
        {"type": "string", "enum": TAGS, "description": "a machine tag: an OS, or os/arch"}
    ),
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
    # extra components installed with the tool; absent means none, an empty list is a mistake
    components: Annotated[list[NonEmpty], Field(min_length=1)] = []


class Uv(Strict):
    name: NonEmpty | None = None  # name on PyPI
    version: NonEmpty | None = None  # spelled `name@version` for `uv tool install`


class Upstream(Strict):
    """Installed by the project's own installer, because no package manager here can.

    `command` runs again whenever this machine's rendered set changes, so it must
    converge rather than assume a clean machine: keep the installer's own skip or
    overwrite behaviour (`MISE_INSTALL_SKIP_IF_EXISTS`), never add uninstall steps.
    """

    command: NonEmpty  # sh, run as written; no default — an empty install is not one


Spec = Brew | Dnf | Mise | Uv | Upstream


class Override(Strict):
    """`{ when = <tag>, <field> = <value>, … }`: a patch on the how, deep-merged over the
    package on machines the tag matches, in list order. Any of via/brew/dnf/mise/uv;
    never on/completions/overrides. Switching `via` drops the previous backend's block."""

    when: SelectorField
    via: Via | None = None
    brew: Brew | None = None
    dnf: Dnf | None = None
    mise: Mise | None = None
    uv: Uv | None = None
    upstream: Upstream | None = None

    @property
    def patch(self) -> dict[str, Any]:
        """What the entry sets, as written — the raw form `patched` merges over a package."""
        return self.model_dump(exclude_unset=True, exclude={"when"})


class _Package(Strict):
    on: Annotated[list[SelectorField], Field(min_length=1)]
    # command -> sh command printing its zsh completion; absent means none, {} is a mistake
    completions: Annotated[dict[NonEmpty, NonEmpty], Field(min_length=1)] = {}
    overrides: list[Override] = []


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


class UpstreamPackage(_Package):
    via: Literal["upstream"]
    upstream: Upstream  # required: the command is the whole of the how


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
    | Annotated[UvPackage, Tag("uv")]
    | Annotated[UpstreamPackage, Tag("upstream")],
    Discriminator(_via),
]
PACKAGE = TypeAdapter(Package)


# --- the documents: one model per .chezmoidata file, for the JSON Schemas ---------------------


class PackagesDoc(Strict):
    packages: dict[NonEmpty, Package]


class PlatformsDoc(Strict):
    platforms: dict[NonEmpty, Platform]


class MachinesDoc(Strict):
    machines: dict[NonEmpty, Machine]


class Xdg(Strict):
    """XDG base dirs relative to $HOME; .zshenv exports them, templates derive paths."""

    dataHome: NonEmpty
    stateHome: NonEmpty
    cacheHome: NonEmpty


class XdgDoc(Strict):
    xdg: Xdg


class Zsh(Strict):
    generatedCompletions: NonEmpty  # where refresh-completions writes, under $XDG_CACHE_HOME


class ZshDoc(Strict):
    zsh: Zsh


DOCS: dict[str, type[Strict]] = {
    "packages.toml": PackagesDoc,
    "platforms.toml": PlatformsDoc,
    "machines.toml": MachinesDoc,
    "xdg.toml": XdgDoc,
    "zsh.toml": ZshDoc,
}


# --- resolved: what a renderer sees ------------------------------------------------------------


# Which backend a spec belongs to. The brew and dnf halves come from their unions, so a new
# variant of either lands here by being added there.
_BACKEND_OF: dict[type, Backend] = {
    **dict.fromkeys(get_args(Brew), "brew"),
    **dict.fromkeys(get_args(Dnf), "dnf"),
    Mise: "mise",
    Uv: "uv",
    Upstream: "upstream",
}


class Resolved(Strict):
    """One package as it installs on one machine. The only input the renderers take."""

    key: NonEmpty
    name: NonEmpty  # name on that backend (the cask's, for a cask)
    spec: Spec
    completions: dict[NonEmpty, NonEmpty]

    @property
    def backend(self) -> Backend:
        """Read off the spec, not stored beside it: the two cannot disagree, so a `uv` label
        over a Mise spec cannot validate and then vanish from every artifact unremarked."""
        return _BACKEND_OF[type(self.spec)]
