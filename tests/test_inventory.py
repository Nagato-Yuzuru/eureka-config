"""Executable spec for the inventory: .chezmoidata/{platforms,machines,packages}.yaml, its
types (tools/inventory/model.py), resolution (inventory.py) and the artifacts chezmoi
includes from .rendered/ (render.py, `uv run render`).

Table-driven. Each row is one fact about the model:
- RENDER_CASES   synthetic packages -> exact lines each artifact holds on each machine
- REJECTS        malformed entries -> the message naming the entry
- INVARIANTS     facts the real inventory must keep, per machine
Plus: .rendered/ is what the data compiles to today (the check `uv run render --check`
runs before every commit), the chezmoi consumers render to valid shell/toml with the
artifact's lines inside, and the `artifact` template refuses stale data and machines
nobody declared.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from inventory.inventory import DATA_FILES, Inventory, InventoryError, load_yaml
from inventory.render import ARTIFACTS, RENDERED_DIR, render, render_tree, stale

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / ".chezmoidata"
RENDERED = REPO_ROOT / RENDERED_DIR
MISE_TASKS = REPO_ROOT / "dot_config" / "mise" / "tasks"
INSTALL_SCRIPT = REPO_ROOT / "run_onchange_before_install-packages.sh.tmpl"
REFRESH_SCRIPT = REPO_ROOT / "run_onchange_after_refresh-completions.sh.tmpl"

PLATFORMS = load_yaml(DATA / "platforms.yaml")["platforms"]
MACHINES = load_yaml(DATA / "machines.yaml")["machines"]
REAL = Inventory.load(DATA)

# --- harness ------------------------------------------------------------------------------------


def inventory(packages: Mapping[str, Any]) -> Inventory:
    """The real platforms and machines, synthetic packages."""
    return Inventory(PLATFORMS, MACHINES, packages)


def lines(inv: Inventory, artifact: str, machine: str) -> list[str]:
    return render(artifact, inv.resolved(machine)).splitlines()


def chezmoi_render(path: Path, machine: str, source: Path = REPO_ROOT) -> str:
    os_, arch = MACHINES[machine]["platform"], MACHINES[machine]["arch"]
    os_ = PLATFORMS[os_]["os"]
    proc = subprocess.run(
        [
            "chezmoi", "execute-template", "--source", str(source),
            "--override-data", json.dumps({"chezmoi": {"os": os_, "arch": arch}}),
            "--file", str(path),
        ],
        text=True,
        capture_output=True,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def heredoc(text: str, tag: str) -> list[str]:
    """Non-blank lines of the first `<<'TAG' … TAG` block in a rendered script."""
    body = text.split(f"<<'{tag}'\n", 1)[1].split(f"\n{tag}\n", 1)[0]
    return [ln for ln in body.splitlines() if ln.strip()]


def check_shell(script: Path, shell: str = "bash") -> None:
    subprocess.run([shell, "-n", str(script)], check=True)
    if shell == "bash" and shutil.which("shellcheck"):
        subprocess.run(["shellcheck", str(script)], check=True)


# --- render semantics (synthetic data) ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RenderCase:
    id: str
    packages: Mapping[str, Any]
    expect: dict[tuple[str, str], list[str]] = field(default_factory=dict)  # (artifact, machine)


RENDER_CASES = [
    RenderCase(
        "default-backend-is-the-platforms-system-manager",
        {"foo": {"on": ["darwin", "linux"]}},
        {
            ("brewfile", "darwin-arm64"): ['brew "foo"'],
            ("dnf-list", "linux-arm64"): ["foo"],
            ("mise-tools", "darwin-arm64"): [],
            ("uv-tools", "darwin-arm64"): [],
        },
    ),
    RenderCase(
        "via-mise-renders-only-into-tools",
        {"foo": {"on": ["darwin", "linux"], "via": "mise"}},
        {
            ("mise-tools", "linux-arm64"): ['"foo" = "latest"'],
            ("brewfile", "darwin-arm64"): [],
            ("dnf-list", "linux-arm64"): [],
        },
    ),
    RenderCase(
        "via-uv-renders-only-into-uv-tools",
        {"ruff": {"on": ["darwin", "linux"], "via": "uv"}},
        {
            ("uv-tools", "darwin-arm64"): ["ruff"],
            ("uv-tools", "linux-arm64"): ["ruff"],
            ("brewfile", "darwin-arm64"): [],
            ("dnf-list", "linux-arm64"): [],
            ("mise-tools", "darwin-arm64"): [],
        },
    ),
    RenderCase(
        "version-is-emitted-for-mise",
        {"rust": {"on": ["darwin"], "via": "mise", "mise": {"version": "stable"}}},
        {("mise-tools", "darwin-arm64"): ['"rust" = "stable"']},
    ),
    RenderCase(
        "uv-spells-name-and-version-as-name-at-version",
        {"foo": {"on": ["darwin"], "via": "uv", "uv": {"name": "foo-cli", "version": "1.2"}}},
        {("uv-tools", "darwin-arm64"): ["foo-cli@1.2"]},
    ),
    RenderCase(
        "uv-version-override-pins-only-that-machine",
        {
            "foo": {
                "on": ["darwin", "linux"],
                "via": "uv",
                "overrides": [{"$when": "linux", "uv": {"version": "1"}}],
            }
        },
        {("uv-tools", "darwin-arm64"): ["foo"], ("uv-tools", "linux-arm64"): ["foo@1"]},
    ),
    RenderCase(
        "components-render-an-inline-table",
        {
            "rust": {
                "on": ["darwin"],
                "via": "mise",
                "mise": {"version": "stable", "components": ["rust-analyzer", "rust-src"]},
            }
        },
        {
            ("mise-tools", "darwin-arm64"): [
                '"rust" = { version = "stable", components = ["rust-analyzer", "rust-src"] }'
            ]
        },
    ),
    RenderCase(
        "components-without-a-version-still-get-one",
        {"foo": {"on": ["darwin"], "via": "mise", "mise": {"components": ["only"]}}},
        {("mise-tools", "darwin-arm64"): ['"foo" = { version = "latest", components = ["only"] }']},
    ),
    RenderCase(
        "override-replaces-components-wholesale",  # lists replace, maps merge
        {
            "foo": {
                "on": ["darwin", "linux"],
                "via": "mise",
                "mise": {"components": ["a", "b"]},
                "overrides": [{"$when": "linux", "mise": {"components": ["c"]}}],
            }
        },
        {
            ("mise-tools", "darwin-arm64"): [
                '"foo" = { version = "latest", components = ["a", "b"] }'
            ],
            ("mise-tools", "linux-arm64"): ['"foo" = { version = "latest", components = ["c"] }'],
        },
    ),
    RenderCase(
        "override-merges-into-the-block-it-patches",  # version kept, name added
        {
            "foo": {
                "on": ["darwin"],
                "via": "mise",
                "mise": {"version": "2"},
                "overrides": [{"$when": "darwin", "mise": {"name": "npm:foo"}}],
            }
        },
        {("mise-tools", "darwin-arm64"): ['"npm:foo" = "2"']},
    ),
    RenderCase(
        "name-defaults-to-key-and-the-backend-block-renames",
        {
            "fd": {"on": ["darwin", "linux"], "dnf": {"name": "fd-find"}},
            "macism": {"on": ["darwin"], "brew": {"name": "laishulu/homebrew/macism"}},
            "gopls": {
                "on": ["darwin"],
                "via": "mise",
                "mise": {"name": "go:golang.org/x/tools/gopls"},
            },
        },
        {
            ("brewfile", "darwin-arm64"): ['brew "fd"', 'brew "laishulu/homebrew/macism"'],
            ("dnf-list", "linux-arm64"): ["fd-find"],
            ("mise-tools", "darwin-arm64"): ['"go:golang.org/x/tools/gopls" = "latest"'],
        },
    ),
    RenderCase(
        "cask-renders-cask-line-not-brew",
        {"ghostty": {"on": ["darwin"], "brew": {"cask": "ghostty"}}},
        {("brewfile", "darwin-arm64"): ['cask "ghostty"']},
    ),
    RenderCase(
        "both-system-blocks-may-coexist-and-each-platform-reads-its-own",
        {
            "starship": {
                "on": ["darwin", "linux"],
                "brew": {"name": "starship-formula"},
                "dnf": {"copr": "atim/starship"},
            }
        },
        {
            ("brewfile", "darwin-arm64"): ['brew "starship-formula"'],
            ("dnf-list", "linux-arm64"): ["starship"],
            ("dnf-repos", "linux-arm64"): ["copr atim/starship"],
            ("dnf-repos", "darwin-arm64"): [],
        },
    ),
    RenderCase(
        "package-absent-from-on-renders-nowhere",
        {"foo": {"on": ["linux"]}, "bar": {"on": ["darwin"], "via": "mise"}},
        {
            ("brewfile", "darwin-arm64"): [],
            ("mise-tools", "linux-arm64"): [],
            ("dnf-list", "linux-arm64"): ["foo"],
        },
    ),
    RenderCase(
        "os-arch-tag-matches-only-that-arch",
        {"foo": {"on": ["darwin/amd64"]}},
        {("brewfile", "darwin-amd64"): ['brew "foo"'], ("brewfile", "darwin-arm64"): []},
    ),
    RenderCase(
        "override-when-os-changes-backend",
        {
            "uv": {
                "on": ["darwin", "linux"],
                "via": "mise",
                "overrides": [{"$when": "linux", "via": "system"}],
            }
        },
        {
            ("mise-tools", "darwin-arm64"): ['"uv" = "latest"'],
            ("mise-tools", "linux-arm64"): [],
            ("dnf-list", "linux-arm64"): ["uv"],
        },
    ),
    RenderCase(
        "override-when-os-arch-matches-only-that-arch",
        {
            "pnpm": {
                "on": ["darwin", "linux"],
                "via": "mise",
                "overrides": [{"$when": "darwin/amd64", "mise": {"name": "npm:pnpm"}}],
            }
        },
        {
            ("mise-tools", "darwin-amd64"): ['"npm:pnpm" = "latest"'],
            ("mise-tools", "darwin-arm64"): ['"pnpm" = "latest"'],
            ("mise-tools", "linux-arm64"): ['"pnpm" = "latest"'],
        },
    ),
    RenderCase(
        "overrides-merge-in-list-order",
        {
            "foo": {
                "on": ["darwin"],
                "via": "mise",
                "mise": {"version": "1"},
                "overrides": [
                    {"$when": "darwin", "mise": {"version": "2"}},
                    {"$when": "darwin/arm64", "mise": {"version": "3"}},
                ],
            }
        },
        {
            ("mise-tools", "darwin-arm64"): ['"foo" = "3"'],
            ("mise-tools", "darwin-amd64"): ['"foo" = "2"'],
        },
    ),
    RenderCase(
        "override-may-switch-backend-and-bring-that-backends-block",
        {
            "foo": {
                "on": ["darwin", "linux"],
                "via": "mise",
                "mise": {"version": "1"},
                "overrides": [
                    {"$when": "darwin/arm64", "mise": {"version": "2"}},
                    {"$when": "linux", "via": "system", "dnf": {"name": "foo-bin", "copr": "a/b"}},
                ],
            }
        },
        {
            ("mise-tools", "darwin-arm64"): ['"foo" = "2"'],
            ("dnf-list", "linux-arm64"): ["foo-bin"],
            ("dnf-repos", "linux-arm64"): ["copr a/b"],
        },
    ),
    RenderCase(
        "dnf-repos-lists-copr-and-repofile-only-for-packages-installed-here-via-system",
        {
            "starship": {"on": ["darwin", "linux"], "dnf": {"copr": "atim/starship"}},
            "mise": {"on": ["linux"], "dnf": {"repofile": "https://mise.jdx.dev/rpm/mise.repo"}},
            "mac-only": {"on": ["darwin"], "dnf": {"copr": "nope/nope"}},
        },
        {
            ("dnf-repos", "linux-arm64"): [
                "repofile https://mise.jdx.dev/rpm/mise.repo",
                "copr atim/starship",
            ]
        },
    ),
    RenderCase(
        "on-may-mix-os-and-os-arch-tags",
        {"foo": {"on": ["darwin", "linux/arm64"]}},
        {
            ("brewfile", "darwin-arm64"): ['brew "foo"'],
            ("brewfile", "darwin-amd64"): ['brew "foo"'],
            ("dnf-list", "linux-arm64"): ["foo"],
        },
    ),
    RenderCase(
        "no-packages-renders-nothing",
        {},
        {(a, "darwin-arm64"): [] for a in ARTIFACTS} | {(a, "linux-arm64"): [] for a in ARTIFACTS},
    ),
    RenderCase(
        "empty-overrides-is-a-no-op",
        {"foo": {"on": ["darwin"], "via": "mise", "overrides": []}},
        {("mise-tools", "darwin-arm64"): ['"foo" = "latest"']},
    ),
    RenderCase(
        "override-that-matches-no-machine-is-inert",
        {
            "foo": {
                "on": ["darwin", "linux"],
                "via": "mise",
                "overrides": [{"$when": "linux/amd64", "mise": {"version": "9"}}],
            }
        },
        {
            ("mise-tools", "linux-arm64"): ['"foo" = "latest"'],
            ("mise-tools", "darwin-arm64"): ['"foo" = "latest"'],
        },
    ),
    RenderCase(
        "os-level-when-applies-to-arch-limited-package",
        {
            "foo": {
                "on": ["darwin/arm64"],
                "via": "mise",
                "overrides": [{"$when": "darwin", "mise": {"version": "2"}}],
            }
        },
        {("mise-tools", "darwin-arm64"): ['"foo" = "2"'], ("mise-tools", "darwin-amd64"): []},
    ),
    RenderCase(
        "shared-copr-is-emitted-once-per-package",  # the install script's guard makes repeats no-ops
        {
            "a": {"on": ["linux"], "dnf": {"copr": "x/y"}},
            "b": {"on": ["linux"], "dnf": {"copr": "x/y"}},
        },
        {("dnf-repos", "linux-arm64"): ["copr x/y", "copr x/y"]},
    ),
    RenderCase(
        "completions-render-command-then-recipe-only-where-the-package-installs",
        {
            "kubectl": {
                "on": ["darwin"],
                "via": "mise",
                "completions": {"kubectl": "kubectl completion zsh"},
            },
            "plain": {"on": ["darwin", "linux"], "via": "mise"},
        },
        {
            ("completions", "darwin-arm64"): ["kubectl kubectl completion zsh"],
            ("completions", "linux-arm64"): [],
            ("mise-tools", "darwin-arm64"): ['"kubectl" = "latest"', '"plain" = "latest"'],
        },
    ),
    RenderCase(
        "completions-on-a-system-package-render-on-every-backend",  # fpath order arbitrates
        {"mise": {"on": ["darwin", "linux"], "completions": {"mise": "mise completion zsh"}}},
        {
            ("completions", "darwin-arm64"): ["mise mise completion zsh"],
            ("completions", "linux-arm64"): ["mise mise completion zsh"],
            ("brewfile", "darwin-arm64"): ['brew "mise"'],
        },
    ),
    RenderCase(
        "completions-map-renders-one-line-per-command-sorted-and-keeps-shell-syntax",
        {
            "rust": {
                "on": ["darwin"],
                "via": "mise",
                "completions": {
                    "rustup": "rustup completions zsh rustup",
                    "cargo": "x | grep -q '^n' && clap_stub cargo 'C=zsh cargo' || y",
                },
            }
        },
        {
            ("completions", "darwin-arm64"): [
                "cargo x | grep -q '^n' && clap_stub cargo 'C=zsh cargo' || y",
                "rustup rustup completions zsh rustup",
            ]
        },
    ),
    RenderCase(
        "completions-on-a-cask-render-under-the-command-name",
        {
            "1password-cli": {
                "on": ["darwin"],
                "brew": {"cask": "1password-cli"},
                "completions": {"op": "op completion zsh"},
            }
        },
        {
            ("completions", "darwin-arm64"): ["op op completion zsh"],
            ("brewfile", "darwin-arm64"): ['cask "1password-cli"'],
        },
    ),
    RenderCase(
        "completions-on-a-uv-tool-render-where-it-installs",
        {
            "ruff": {
                "on": ["linux"],
                "via": "uv",
                "completions": {"ruff": "ruff generate-shell-completion zsh"},
            }
        },
        {
            ("completions", "linux-arm64"): ["ruff ruff generate-shell-completion zsh"],
            ("completions", "darwin-arm64"): [],
            ("uv-tools", "linux-arm64"): ["ruff"],
        },
    ),
]


@pytest.mark.parametrize(
    ("packages", "artifact", "machine", "want"),
    [
        pytest.param(c.packages, artifact, machine, want, id=f"{c.id}:{artifact}@{machine}")
        for c in RENDER_CASES
        for (artifact, machine), want in c.expect.items()
    ],
)
def test_render(packages: Mapping[str, Any], artifact: str, machine: str, want: list[str]):
    assert lines(inventory(packages), artifact, machine) == want


# --- validation ---------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Reject:
    id: str
    packages: Mapping[str, Any]
    message: str  # what the InventoryError must contain


REJECTS = [
    Reject(
        "unknown-key", {"foo": {"on": ["darwin"], "nope": "1"}}, "packages.foo.nope: Extra inputs"
    ),
    Reject("missing-on", {"foo": {"via": "mise"}}, "packages.foo.on: Field required"),
    Reject(
        "on-not-a-list", {"foo": {"on": "darwin"}}, "packages.foo.on: Input should be a valid list"
    ),
    Reject("on-empty", {"foo": {"on": []}}, "packages.foo.on: List should have at least 1 item"),
    Reject("on-unknown-tag", {"foo": {"on": ["mars"]}}, "packages.foo.on.0: unknown tag 'mars'"),
    Reject(
        "via-invalid",
        {"foo": {"on": ["darwin"], "via": "pip"}},
        "packages.foo: via must be system|mise|uv",
    ),
    Reject(
        "name-field-is-a-map",
        {"foo": {"on": ["darwin"], "via": "mise", "mise": {"name": {"darwin": "x"}}}},
        "packages.foo.mise.name: Input should be a valid string",
    ),
    # the point of the blocks: a backend's parameters cannot land on another backend
    Reject(
        "brew-block-on-a-mise-package",
        {"foo": {"on": ["darwin"], "via": "mise", "brew": {"cask": "x"}}},
        "packages.foo.brew: no brew block on a `via: mise` package",
    ),
    Reject(
        "dnf-block-on-a-mise-package",
        {"foo": {"on": ["linux"], "via": "mise", "dnf": {"copr": "a/b"}}},
        "packages.foo.dnf: no dnf block on a `via: mise` package",
    ),
    Reject(
        "mise-block-on-a-system-package",
        {"foo": {"on": ["darwin"], "mise": {"version": "1"}}},
        "packages.foo.mise: no mise block on a `via: system` package",
    ),
    Reject(
        "uv-block-on-a-mise-package",
        {"foo": {"on": ["darwin"], "via": "mise", "uv": {"version": "1"}}},
        "packages.foo.uv: no uv block on a `via: mise` package",
    ),
    Reject(
        "version-outside-a-block",  # brew and dnf have no version; the field lives where it means something
        {"foo": {"on": ["darwin"], "version": "9"}},
        "packages.foo.version: Extra inputs are not permitted",
    ),
    Reject(
        "brew-is-a-formula-or-a-cask-not-both",
        {"foo": {"on": ["darwin"], "brew": {"name": "a", "cask": "b"}}},
        "packages.foo.brew: a formula ({ name }) or a cask ({ cask }), not both",
    ),
    Reject(
        "dnf-has-one-source",
        {"foo": {"on": ["linux"], "dnf": {"copr": "a/b", "repofile": "https://x/y.repo"}}},
        "packages.foo.dnf: the standard repos ({ name }), a COPR ({ copr }) or a .repo file"
        " ({ repofile }), one",
    ),
    Reject(
        "unknown-key-inside-a-union-block-is-named",
        {"foo": {"on": ["linux"], "dnf": {"nope": "1"}}},
        "packages.foo.dnf.nope: Extra inputs are not permitted",
    ),
    Reject(
        "overrides-not-a-list",
        {"foo": {"on": ["darwin"], "overrides": {"$when": "linux"}}},
        "packages.foo.overrides: Input should be a valid list",
    ),
    Reject(
        "override-missing-when",
        {"foo": {"on": ["darwin"], "overrides": [{"via": "mise"}]}},
        "packages.foo.overrides[?].$when: Field required",
    ),
    Reject(
        "override-unknown-tag",
        {"foo": {"on": ["darwin"], "overrides": [{"$when": "mars"}]}},
        "packages.foo.overrides[mars].$when: unknown tag 'mars'",
    ),
    Reject(
        "override-of-on",
        {"foo": {"on": ["darwin"], "overrides": [{"$when": "linux", "on": ["linux"]}]}},
        "packages.foo.overrides[linux]: cannot override 'on'",
    ),
    Reject(
        "override-via-invalid",
        {"foo": {"on": ["darwin"], "overrides": [{"$when": "linux", "via": "pip"}]}},
        "packages.foo.overrides[linux]: via must be system|mise|uv",
    ),
    Reject(
        "override-value-not-string",
        {
            "foo": {
                "on": ["darwin"],
                "via": "mise",
                "overrides": [{"$when": "linux", "mise": {"version": 1}}],
            }
        },
        "packages.foo.overrides[linux].mise.version: Input should be a valid string",
    ),
    Reject(
        "override-that-matches-nothing-is-still-checked",
        {
            "foo": {
                "on": ["darwin"],
                "overrides": [{"$when": "linux/amd64", "mise": {"version": "1"}}],
            }
        },
        "packages.foo.overrides[linux/amd64].mise: no mise block on a `via: system` package",
    ),
    Reject(
        "components-not-a-list",
        {"foo": {"on": ["darwin"], "via": "mise", "mise": {"components": "rust-src"}}},
        "packages.foo.mise.components: Input should be a valid list",
    ),
    Reject(
        "components-empty",
        {"foo": {"on": ["darwin"], "via": "mise", "mise": {"components": []}}},
        "packages.foo.mise.components: must be a non-empty list",
    ),
    Reject(
        "components-entry-not-a-string",
        {"foo": {"on": ["darwin"], "via": "mise", "mise": {"components": [1]}}},
        "packages.foo.mise.components.0: Input should be a valid string",
    ),
    Reject(
        "completions-not-a-map",
        {"foo": {"on": ["darwin"], "completions": "foo completion zsh"}},
        "packages.foo.completions: Input should be a valid dictionary",
    ),
    Reject(
        "completions-empty",
        {"foo": {"on": ["darwin"], "completions": {}}},
        "packages.foo.completions: must be a non-empty map of command -> recipe",
    ),
    Reject(
        "completions-recipe-not-a-string",
        {"foo": {"on": ["darwin"], "completions": {"foo": ["foo", "completion"]}}},
        "packages.foo.completions.foo: Input should be a valid string",
    ),
    Reject(
        "completions-recipe-empty",
        {"foo": {"on": ["darwin"], "completions": {"foo": ""}}},
        "packages.foo.completions.foo: String should have at least 1 character",
    ),
    Reject(
        "completions-not-overridable",  # fpath order already arbitrates per-backend differences
        {"foo": {"on": ["darwin"], "overrides": [{"$when": "linux", "completions": {"foo": "x"}}]}},
        "packages.foo.overrides[linux]: cannot override 'completions'",
    ),
]


@pytest.mark.parametrize(
    ("packages", "message"), [pytest.param(r.packages, r.message, id=r.id) for r in REJECTS]
)
def test_validation_rejects(packages: Mapping[str, Any], message: str):
    with pytest.raises(InventoryError) as e:
        inventory(packages)
    assert message in str(e.value), str(e.value)


def test_machine_must_name_a_declared_platform():
    with pytest.raises(InventoryError, match="machines.x: unknown platform 'nope'"):
        Inventory(PLATFORMS, {"x": {"platform": "nope", "arch": "arm64"}}, {})


def test_platform_system_manager_is_one_we_render_for():
    with pytest.raises(
        InventoryError, match="platforms.debian.system: Input should be 'brew' or 'dnf'"
    ):
        Inventory({"debian": {"os": "linux", "system": "apt"}}, {}, {})


def test_yaml_on_is_a_key_not_a_boolean(tmp_path: Path):
    """pyyaml is YAML 1.1 (`on` → True); the loader reads it as chezmoi does."""
    f = tmp_path / "x.yaml"
    f.write_text("on: [darwin]\nyes: no\n")
    assert load_yaml(f) == {"on": ["darwin"], "yes": "no"}


# --- real data ----------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Invariant:
    id: str
    artifact: str
    machine: str
    contains: list[str] = field(default_factory=list)
    excludes: list[str] = field(default_factory=list)
    exact: list[str] | None = None


INVARIANTS = [
    Invariant(
        "intel-mac-pnpm-via-npm",
        "mise-tools",
        "darwin-amd64",
        contains=['"npm:pnpm" = "latest"'],
        excludes=['"pnpm" = "latest"'],
    ),
    Invariant(
        "arm-mac-pnpm-via-default",
        "mise-tools",
        "darwin-arm64",
        contains=['"pnpm" = "latest"'],
        excludes=['"npm:pnpm" = "latest"'],
    ),
    Invariant(
        "rust-toolchain-ships-the-editor-components-on-mac",
        "mise-tools",
        "darwin-arm64",
        contains=['"rust" = { version = "stable", components = ["rust-analyzer", "rust-src"] }'],
    ),
    Invariant(
        "rust-toolchain-ships-the-editor-components-on-linux",
        "mise-tools",
        "linux-arm64",
        contains=['"rust" = { version = "stable", components = ["rust-analyzer", "rust-src"] }'],
    ),
    Invariant("uv-not-in-mise-on-mac", "mise-tools", "darwin-arm64", excludes=['"uv" = "latest"']),
    Invariant("uv-not-in-mise-on-linux", "mise-tools", "linux-arm64", excludes=['"uv" = "latest"']),
    Invariant("uv-from-brew", "brewfile", "darwin-arm64", contains=['brew "uv"']),
    Invariant("uv-from-dnf", "dnf-list", "linux-arm64", contains=["uv"]),
    Invariant("mac-installs-nothing-via-dnf", "dnf-list", "darwin-arm64", exact=[]),
    Invariant("linux-has-no-brewfile", "brewfile", "linux-arm64", exact=[]),
    # Python CLIs ride uv on every machine, never a system package or mise.
    Invariant("ruff-and-ty-via-uv-on-mac", "uv-tools", "darwin-arm64", contains=["ruff", "ty"]),
    Invariant("ruff-and-ty-via-uv-on-linux", "uv-tools", "linux-arm64", contains=["ruff", "ty"]),
    Invariant("ruff-not-in-mise", "mise-tools", "darwin-arm64", excludes=['"ruff" = "latest"']),
    Invariant("ruff-not-in-brew", "brewfile", "darwin-arm64", excludes=['brew "ruff"']),
    Invariant(
        "uv-tools-declare-their-completions",
        "completions",
        "linux-arm64",
        contains=["ruff ruff generate-shell-completion zsh", "ty ty generate-shell-completion zsh"],
    ),
    # The two system tools Fedora's repos ship no zsh completion for; brew's copy wins on a Mac.
    Invariant(
        "mise-and-starship-recipes-reach-linux",
        "completions",
        "linux-arm64",
        contains=["mise mise completion zsh", "starship starship completions zsh"],
    ),
    Invariant(
        "workstation-only-tools-generate-no-completion-on-linux",
        "completions",
        "linux-arm64",
        excludes=["kubectl kubectl completion zsh", "op op completion zsh"],
    ),
    # Both ship it on both backends (Fedora rpm and brew formula alike): no recipe.
    Invariant(
        "uv-has-no-recipe",
        "completions",
        "darwin-arm64",
        excludes=["uv uv generate-shell-completion zsh"],
    ),
    Invariant(
        "prek-uses-the-clap-stub",
        "completions",
        "linux-arm64",
        contains=["prek clap_stub prek 'COMPLETE=zsh prek'"],
    ),
]


@pytest.mark.parametrize("inv", [pytest.param(i, id=i.id) for i in INVARIANTS])
def test_real_data_invariant(inv: Invariant):
    got = lines(REAL, inv.artifact, inv.machine)
    assert set(inv.contains) <= set(got), inv.contains
    assert not set(inv.excludes) & set(got), inv.excludes
    if inv.exact is not None:
        assert got == inv.exact


@pytest.mark.parametrize("machine", MACHINES)
@pytest.mark.parametrize(
    "artifact", [a for a in ARTIFACTS if a != "dnf-repos"]
)  # shared repos repeat
def test_real_data_has_no_duplicate_lines(artifact: str, machine: str):
    got = lines(REAL, artifact, machine)
    assert len(got) == len(set(got)), sorted(ln for ln in got if got.count(ln) > 1)


@pytest.mark.parametrize("machine", MACHINES)
def test_completions_commands_are_unique_per_machine(machine: str):
    """Two packages claiming the same command would race for one _<cmd> file."""
    cmds = [ln.split(" ", 1)[0] for ln in lines(REAL, "completions", machine)]
    assert len(cmds) == len(set(cmds)), sorted(c for c in cmds if cmds.count(c) > 1)


# --- .rendered/: the compiled tree chezmoi includes ---------------------------------------------


def test_rendered_tree_is_what_the_data_compiles_to():
    """`uv run render --check`, in-process: every artifact and STAMP on disk, no extras."""
    assert stale(render_tree(REAL, DATA), RENDERED) == []


@pytest.mark.parametrize("machine", MACHINES)
@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_artifact_template_serves_this_machines_file(artifact: str, machine: str, tmp_path: Path):
    """The template's hash of the data files agrees with STAMP, and it picks the machine's dir."""
    tmpl = tmp_path / "t.tmpl"
    tmpl.write_text('{{ includeTemplate "artifact" (merge (dict "name" "' + artifact + '") .) }}')
    assert chezmoi_render(tmpl, machine) == (RENDERED / machine / artifact).read_text()


def _source_copy(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    src.mkdir()
    for d in (".chezmoidata", ".chezmoitemplates", RENDERED_DIR):
        shutil.copytree(REPO_ROOT / d, src / d)
    return src


def test_artifact_template_refuses_a_stale_tree(tmp_path: Path):
    src = _source_copy(tmp_path)
    with (src / ".chezmoidata" / DATA_FILES[1]).open("a") as f:
        f.write("\n# an edit nobody rendered\n")
    tmpl = tmp_path / "t.tmpl"
    tmpl.write_text('{{ includeTemplate "artifact" (merge (dict "name" "brewfile") .) }}')
    with pytest.raises(AssertionError, match=r"\.rendered/ is stale"):
        chezmoi_render(tmpl, "darwin-arm64", source=src)


def test_artifact_template_refuses_an_undeclared_machine(tmp_path: Path):
    tmpl = tmp_path / "t.tmpl"
    tmpl.write_text('{{ includeTemplate "artifact" (merge (dict "name" "brewfile") .) }}')
    proc = subprocess.run(
        [
            "chezmoi", "execute-template", "--source", str(REPO_ROOT),
            "--override-data", json.dumps({"chezmoi": {"os": "linux", "arch": "amd64"}}),
            "--file", str(tmpl),
        ],
        text=True,
        capture_output=True,
        check=False,
    )  # fmt: skip
    assert proc.returncode != 0
    assert "linux-amd64 is not declared" in proc.stderr


# --- the consumers: scripts and configs that embed the artifacts --------------------------------


def _os(machine: str) -> str:
    return PLATFORMS[MACHINES[machine]["platform"]]["os"]


@pytest.mark.parametrize("machine", MACHINES)
def test_install_script_renders_to_valid_shell(machine: str, tmp_path: Path):
    """System packages through this machine's package manager only, then the uv tools."""
    rendered = chezmoi_render(INSTALL_SCRIPT, machine)
    script = tmp_path / "install-packages.sh"
    script.write_text(rendered)
    if _os(machine) == "darwin":
        assert "brew bundle install --file=-" in rendered
        assert "dnf" not in rendered
        assert heredoc(rendered, "EOF") == lines(REAL, "brewfile", machine)
    else:
        assert "xargs -r sudo dnf -y install" in rendered
        assert "brew" not in rendered
        assert heredoc(rendered, "EOF") == lines(REAL, "dnf-repos", machine)
    assert rendered.index("uv tool install") > rendered.index("<<'EOF'"), "uv tools come after uv"
    check_shell(script)
    uv_block = rendered.split("uv tool install", 1)[1]
    assert heredoc(uv_block, "EOF") == lines(REAL, "uv-tools", machine)


@pytest.mark.parametrize("machine", MACHINES)
def test_mise_config_renders_the_tools_table(machine: str):
    rendered = chezmoi_render(REPO_ROOT / "dot_config" / "mise" / "config.toml.tmpl", machine)
    doc = tomllib.loads(rendered)
    want = [ln.split(" = ", 1)[0].strip('"') for ln in lines(REAL, "mise-tools", machine)]
    assert sorted(doc["tools"]) == sorted(want)


@pytest.mark.parametrize("machine", MACHINES)
def test_refresh_completions_task_renders_to_valid_bash(machine: str, tmp_path: Path):
    """The recipe heredoc is `read` line by line: one `<cmd> <recipe>` per line, no blank cmd."""
    script = tmp_path / "refresh-completions"
    rendered = chezmoi_render(MISE_TASKS / "executable_refresh-completions.tmpl", machine)
    script.write_text(rendered)
    check_shell(script)
    assert heredoc(rendered, "RECIPES") == lines(REAL, "completions", machine)


@pytest.mark.parametrize("machine", MACHINES)
def test_onchange_refresh_script_keys_on_this_machines_recipes(machine: str, tmp_path: Path):
    """chezmoi re-runs it when its rendered content changes, so the recipe list is rendered
    into it verbatim; the toolset gate defers to mise's postinstall hook on a new machine."""
    script = tmp_path / "refresh-completions.sh"
    rendered = chezmoi_render(REFRESH_SCRIPT, machine)
    script.write_text(rendered)
    check_shell(script)
    assert "mise ls --missing" in rendered
    assert rendered.index("mise ls --missing") < rendered.index("mise run refresh-completions")
    assert heredoc(rendered, "RECIPES") == lines(REAL, "completions", machine)


@pytest.mark.parametrize("machine", MACHINES)
def test_audit_completions_task_renders_to_valid_zsh(machine: str, tmp_path: Path):
    script = tmp_path / "audit-completions"
    rendered = chezmoi_render(MISE_TASKS / "executable_audit-completions.tmpl", machine)
    script.write_text(rendered)
    check_shell(script, "zsh")
    assert ("brew packages" in rendered) == (_os(machine) == "darwin")
    assert ("rpm -ql" in rendered) == (_os(machine) == "linux")
    assert heredoc(rendered, "UVTOOLS") == lines(REAL, "uv-tools", machine)
    assert heredoc(rendered, "RECIPES") == lines(REAL, "completions", machine)
