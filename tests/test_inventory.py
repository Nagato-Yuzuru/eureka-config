"""Executable spec for the inventory: .chezmoidata/{platforms,machines,packages}.toml, its
types (tools/inventory/model.py), resolution (inventory.py) and the artifacts chezmoi
includes from .rendered/ (render.py, `uv run render`).

Table-driven, and every row is about the compiler, not about what .chezmoidata happens to
say today:
- RENDER_CASES   synthetic packages -> exact lines each artifact holds on each machine
- REJECTS        malformed entries -> the message naming the entry

Nothing here asserts what the real data compiles to: a row restating a package's backend has
to be edited in the same commit as the change it guards. The real data is held only to
invariants every valid inventory satisfies.

Plus: the chezmoi consumers render to valid shell/toml with the artifact's lines inside, and
the `artifact` template refuses stale data and machines nobody declared. Those run against a
synthetic chezmoi source (the `source` fixture): tests/fixtures/inventory/chezmoidata, its
compiled `.rendered/`, and copies of the templates under test — chosen so that every artifact
is non-empty and machine-specific, which a real-data consumer test could not rely on.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from inventory.cli import main
from inventory.inventory import (
    DATA_FILES,
    LOCAL_PACKAGES,
    Inventory,
    InventoryError,
    load_data,
)
from inventory.model import DOCS
from inventory.render import (
    ARTIFACTS,
    RENDERED_DIR,
    SCHEMA_DIR,
    STAMP,
    render,
    render_tree,
    schemas,
    stale,
    write_tree,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / ".chezmoidata"  # the real data: invariants only
SCHEMA = REPO_ROOT / SCHEMA_DIR

# The synthetic inventory every other test runs on (see its packages.toml for the design).
# Not named `.chezmoidata`: chezmoi merges every such directory in the source tree into its
# data, .chezmoiignore notwithstanding, and this one sorts after the real one.
FIXTURE_DATA = Path(__file__).resolve().parent / "fixtures" / "inventory" / "chezmoidata"
SYN = Inventory.load(FIXTURE_DATA)
MACHINES = list(SYN.machines)
GENERATED = load_data(FIXTURE_DATA / "zsh.toml")["zsh"]["generatedCompletions"]
CACHE_HOME = load_data(FIXTURE_DATA / "xdg.toml")["xdg"]["cacheHome"]

# The chezmoi templates under test, relative to a source dir.
MISE_TASKS = Path("dot_config") / "mise" / "tasks"
INSTALL_SCRIPT = Path("run_onchange_before_install-packages.sh.tmpl")
REFRESH_SCRIPT = Path("run_onchange_after_refresh-completions.sh.tmpl")
MISE_CONFIG = Path("dot_config") / "mise" / "config.toml.tmpl"
REFRESH_TASK = MISE_TASKS / "executable_refresh-completions.tmpl"
AUDIT_TASK = MISE_TASKS / "executable_audit-completions.tmpl"
UPGRADE_TASK = MISE_TASKS / "executable_upgrade-upstream.tmpl"
CONSUMERS = (INSTALL_SCRIPT, REFRESH_SCRIPT, MISE_CONFIG, REFRESH_TASK, AUDIT_TASK, UPGRADE_TASK)

# --- harness ------------------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _rebuilt_schema() -> None:
    """`.schema/` is committed (the editor reads it before anything could build it); only
    `.rendered/` is gitignored build output. Rebuild the schema from the types up front, the
    way `uv run render` does, so the schema tests below read what the types say now — and a
    committed copy that lags them shows up as a file this run modified."""
    write_tree(schemas(), SCHEMA)


@pytest.fixture(scope="session")
def source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A self-contained chezmoi source dir: the synthetic .chezmoidata, its compiled
    `.rendered/` and `.schema/`, the real `.chezmoitemplates/` and the consumer templates.
    Read-only for the session; a test that edits it works on `_source_copy`."""
    src = tmp_path_factory.mktemp("synthetic") / "source"
    shutil.copytree(FIXTURE_DATA, src / ".chezmoidata")
    shutil.copytree(REPO_ROOT / ".chezmoitemplates", src / ".chezmoitemplates")
    for rel in CONSUMERS:
        (src / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(REPO_ROOT / rel, src / rel)
    write_tree(render_tree(SYN, src / ".chezmoidata"), src / RENDERED_DIR)
    write_tree(schemas(), src / SCHEMA_DIR)
    return src


def _source_copy(source: Path, tmp_path: Path) -> Path:
    return Path(shutil.copytree(source, tmp_path / "source"))


def inventory(packages: Mapping[str, Any], machines: Mapping[str, Any] | None = None) -> Inventory:
    """The synthetic platforms and machines (or the machines given), the packages given."""
    return Inventory(
        load_data(FIXTURE_DATA / "platforms.toml")["platforms"],
        load_data(FIXTURE_DATA / "machines.toml")["machines"] if machines is None else machines,
        packages,
    )


def _toml(v: Any) -> str:
    """`v` as an inline TOML value: as much TOML as a REJECTS row needs to become a data file."""
    match v:
        case Mapping():
            return "{ " + ", ".join(f"{json.dumps(k)} = {_toml(x)}" for k, x in v.items()) + " }"
        case list():
            return "[" + ", ".join(_toml(x) for x in v) + "]"
        case str():
            return json.dumps(v)  # a JSON string is a TOML basic string
        case int():
            return str(v)
    raise TypeError(f"no TOML spelling for {v!r}")


def write_table(path: Path, table: str, entries: Mapping[str, Any]) -> None:
    body = "".join(f"{json.dumps(k)} = {_toml(v)}\n" for k, v in entries.items())
    path.write_text(f"[{table}]\n{body}")


def lines(inv: Inventory, artifact: str, machine: str) -> list[str]:
    return render(artifact, inv.resolved(machine)).splitlines()


def _os(machine: str) -> str:
    return SYN.platforms[SYN.machines[machine].platform].os


def chezmoi_execute(tmpl: Path, source: Path, os_: str, arch: str) -> subprocess.CompletedProcess:
    """`chezmoi execute-template` as the given os/arch. An empty config (not the user's): its
    read-source-state hook would run `make` against a source dir that has no Makefile."""
    return subprocess.run(
        [
            "chezmoi", "execute-template", "--source", str(source),
            "--config-format", "toml", "--config", os.devnull,
            "--override-data", json.dumps({"chezmoi": {"os": os_, "arch": arch}}),
            "--file", str(tmpl),
        ],
        text=True,
        capture_output=True,
        check=False,
    )  # fmt: skip


def chezmoi_render(tmpl: Path, machine: str, source: Path) -> str:
    proc = chezmoi_execute(tmpl, source, _os(machine), SYN.machines[machine].arch)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout


def heredoc(text: str, tag: str) -> list[str]:
    """Non-blank lines of the first `<<'TAG' … TAG` block in a rendered script. The marker
    need not end its line: `cmd <<'EOF' || status=$?` opens a heredoc like any other."""
    rest = text.split(f"<<'{tag}'", 1)[1].split("\n", 1)[1]
    return [ln for ln in rest.split(f"\n{tag}\n", 1)[0].splitlines() if ln.strip()]


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
        # Artifacts are keyed content: declaration order leaking into them would rewrite every
        # file on a reordered packages.toml, and chezmoi re-runs the install script on content.
        "artifact-lines-follow-key-order-not-declaration-order",
        {"zzz": {"on": ["darwin"]}, "aaa": {"on": ["darwin"]}},
        {("brewfile", "darwin-arm64"): ['brew "aaa"', 'brew "zzz"']},
    ),
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
        "minimum-release-age-renders-an-inline-table",
        {"foo": {"on": ["darwin"], "via": "mise", "mise": {"minimum_release_age": "1d"}}},
        {
            ("mise-tools", "darwin-arm64"): [
                '"foo" = { version = "latest", minimum_release_age = "1d" }'
            ]
        },
    ),
    RenderCase(
        "minimum-release-age-sits-beside-components",
        {
            "foo": {
                "on": ["darwin"],
                "via": "mise",
                "mise": {"version": "1", "minimum_release_age": "3d", "components": ["x"]},
            }
        },
        {
            ("mise-tools", "darwin-arm64"): [
                '"foo" = { version = "1", minimum_release_age = "3d", components = ["x"] }'
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
                "overrides": [{"when": "linux", "mise": {"components": ["c"]}}],
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
                "overrides": [{"when": "darwin", "mise": {"name": "npm:foo"}}],
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
                "overrides": [{"when": "linux", "via": "system"}],
            }
        },
        {
            ("mise-tools", "darwin-arm64"): ['"uv" = "latest"'],
            ("mise-tools", "linux-arm64"): [],
            ("dnf-list", "linux-arm64"): ["uv"],
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
                    {"when": "darwin", "mise": {"version": "2"}},
                    {"when": "darwin/arm64", "mise": {"version": "3"}},
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
                    {"when": "darwin/arm64", "mise": {"version": "2"}},
                    {"when": "linux", "via": "system", "dnf": {"name": "foo-bin", "copr": "a/b"}},
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
        "override-switches-brew-flavour",  # a deciding field replaces, it does not pile up
        {
            "foo": {
                "on": ["darwin"],
                "brew": {"name": "foo-formula"},
                "overrides": [{"when": "darwin/amd64", "brew": {"cask": "foo-app"}}],
            }
        },
        {
            ("brewfile", "darwin-arm64"): ['brew "foo-formula"'],
            ("brewfile", "darwin-amd64"): ['cask "foo-app"'],
        },
    ),
    RenderCase(
        "override-switches-dnf-source-but-a-name-patch-keeps-it",
        {
            "a": {
                "on": ["linux"],
                "dnf": {"copr": "x/y"},
                "overrides": [{"when": "linux/arm64", "dnf": {"repofile": "https://x/y.repo"}}],
            },
            "b": {
                "on": ["linux"],
                "dnf": {"copr": "x/y"},
                "overrides": [{"when": "linux", "dnf": {"name": "b2"}}],
            },
        },
        {
            ("dnf-repos", "linux-arm64"): ["repofile https://x/y.repo", "copr x/y"],
            ("dnf-list", "linux-arm64"): ["a", "b2"],
        },
    ),
    RenderCase(
        "overrides-apply-cumulatively-in-order",  # the second only makes sense after the first
        {
            "foo": {
                "on": ["darwin", "linux"],
                "via": "mise",
                "overrides": [
                    {"when": "linux", "via": "system"},
                    {"when": "linux/arm64", "dnf": {"copr": "a/b"}},
                ],
            }
        },
        {
            ("mise-tools", "darwin-arm64"): ['"foo" = "latest"'],
            ("dnf-list", "linux-arm64"): ["foo"],
            ("dnf-repos", "linux-arm64"): ["copr a/b"],
        },
    ),
    RenderCase(
        "dnf-repos-lists-copr-and-repofile-only-for-packages-installed-here-via-system",
        {
            "starship": {"on": ["darwin", "linux"], "dnf": {"copr": "atim/starship"}},
            "vendored": {"on": ["linux"], "dnf": {"repofile": "https://vendor/x.repo"}},
            "mac-only": {"on": ["darwin"], "dnf": {"copr": "nope/nope"}},
        },
        {
            ("dnf-repos", "linux-arm64"): [
                "copr atim/starship",
                "repofile https://vendor/x.repo",
            ]
        },
    ),
    RenderCase(
        "no-packages-renders-nothing",
        {},
        {(a, "darwin-arm64"): [] for a in ARTIFACTS} | {(a, "linux-arm64"): [] for a in ARTIFACTS},
    ),
    RenderCase(
        "override-that-matches-no-machine-is-inert",
        {
            "foo": {
                "on": ["darwin", "linux"],
                "via": "mise",
                "overrides": [{"when": "linux/amd64", "mise": {"version": "9"}}],
            }
        },
        {
            ("mise-tools", "linux-arm64"): ['"foo" = "latest"'],
            ("mise-tools", "darwin-arm64"): ['"foo" = "latest"'],
        },
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
        "mise-completions-map-the-mise-name-to-sorted-commands-where-the-package-installs",
        {
            "hugo": {
                "on": ["darwin", "linux"],
                "via": "mise",
                "mise": {"name": "hugo-extended"},
                "completions": {"hugo": "hugo completion zsh"},
            },
            "rust": {
                "on": ["linux"],
                "via": "mise",
                "completions": {"rustup": "rustup completions zsh rustup", "cargo": "y"},
            },
        },
        {
            ("mise-completions", "linux-arm64"): ["hugo-extended hugo", "rust cargo rustup"],
            ("mise-completions", "darwin-arm64"): ["hugo-extended hugo"],
        },
    ),
    RenderCase(
        # their installs never fire mise's hook; the full refresh still generates theirs
        "mise-completions-skip-other-backends-that-completions-keeps",
        {
            "mise": {"on": ["darwin"], "completions": {"mise": "mise completion zsh"}},
            "ruff": {"on": ["darwin"], "via": "uv", "completions": {"ruff": "x"}},
            "plain": {"on": ["darwin"], "via": "mise"},
        },
        {
            ("mise-completions", "darwin-arm64"): [],
            ("completions", "darwin-arm64"): ["mise mise completion zsh", "ruff x"],
        },
    ),
    RenderCase(
        "uv-without-name-or-version-renders-the-key",
        {"foo": {"on": ["darwin"], "via": "uv"}},
        {("uv-tools", "darwin-arm64"): ["foo"]},
    ),
    RenderCase(
        "a-formula-and-a-cask-may-share-a-name",  # two namespaces in a Brewfile
        {"app": {"on": ["darwin"], "brew": {"cask": "x"}}, "x": {"on": ["darwin"]}},
        {("brewfile", "darwin-arm64"): ['cask "x"', 'brew "x"']},
    ),
    RenderCase(
        "a-name-may-repeat-on-other-machines-and-other-backends",
        {
            "a": {"on": ["darwin/arm64"], "via": "mise", "mise": {"name": "t"}},
            "b": {"on": ["darwin/amd64"], "via": "mise", "mise": {"name": "t"}},
            "t": {"on": ["darwin"], "via": "uv"},
        },
        {
            ("mise-tools", "darwin-arm64"): ['"t" = "latest"'],
            ("mise-tools", "darwin-amd64"): ['"t" = "latest"'],
            ("uv-tools", "darwin-arm64"): ["t"],
        },
    ),
    RenderCase(
        "a-command-may-have-a-different-recipe-on-each-machine",
        {
            "a": {"on": ["darwin/arm64"], "completions": {"c": "a-recipe"}},
            "b": {"on": ["darwin/amd64"], "via": "mise", "completions": {"c": "b-recipe"}},
        },
        {
            ("completions", "darwin-arm64"): ["c a-recipe"],
            ("completions", "darwin-amd64"): ["c b-recipe"],
        },
    ),
    RenderCase(  # a tool that updates itself unasked, like mise with auto_update
        "upstream-without-an-update-installs-but-renders-no-update",
        {
            "boot": {
                "on": ["darwin"],
                "via": "upstream",
                "upstream": {"command": "i", "creates": "$HOME/b"},
            }
        },
        {
            ("upstream-installs", "darwin-arm64"): ['[ -x "$HOME/b" ] || i'],
            ("upstream-updates", "darwin-arm64"): [],
        },
    ),
    RenderCase(
        "upstream-renders-its-command-guarded-by-what-it-creates",
        {
            "boot": {
                "on": ["darwin", "linux"],
                "via": "upstream",
                "upstream": {
                    "command": "curl https://x/install.sh | sh",
                    "creates": "$HOME/b",
                    "update": "b self update",
                },
            }
        },
        {
            ("upstream-installs", "darwin-arm64"): [
                '[ -x "$HOME/b" ] || curl https://x/install.sh | sh'
            ],
            ("upstream-installs", "linux-arm64"): [
                '[ -x "$HOME/b" ] || curl https://x/install.sh | sh'
            ],
            ("upstream-updates", "darwin-arm64"): ['if [ -x "$HOME/b" ]; then b self update; fi'],
            ("upstream-updates", "linux-arm64"): ['if [ -x "$HOME/b" ]; then b self update; fi'],
            ("brewfile", "darwin-arm64"): [],
            ("dnf-list", "linux-arm64"): [],
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
    message: str  # the whole InventoryError text
    machines: Mapping[str, Any] | None = None  # in place of the synthetic machines
    resolve: str | None = None  # a machine to resolve once the inventory is built


REJECTS = [
    Reject("package-not-a-map", {"foo": "brew"}, "packages.foo: must be a map"),
    Reject(
        "on-tag-not-a-string",
        {"foo": {"on": [1]}},
        "packages.foo.on.0: tags are strings: darwin, linux, or os/arch",
    ),
    Reject(
        "machine-arch-invalid",
        {},
        "machines.x.arch: Input should be 'arm64' or 'amd64'",
        machines={"x": {"platform": "brewhost", "arch": "x86"}},
    ),
    Reject("resolved-on-an-undeclared-machine", {}, "machines.nope: not declared", resolve="nope"),
    # --- one thing installed once, one completion generated once, per machine
    Reject(
        "two-packages-install-one-formula",
        {"a": {"on": ["darwin"], "brew": {"name": "x"}}, "x": {"on": ["darwin"]}},
        "packages.x: brew formula 'x' on darwin-arm64 is already installed by packages.a",
    ),
    Reject(  # a clash is per machine: on darwin/arm64 only b installs it
        "two-packages-install-one-cask-on-the-machine-both-reach",
        {
            "a": {"on": ["darwin/amd64"], "brew": {"cask": "y"}},
            "b": {"on": ["darwin"], "brew": {"cask": "y"}},
        },
        "packages.b: brew cask 'y' on darwin-amd64 is already installed by packages.a",
    ),
    Reject(  # the source is where dnf gets it, not what it installs
        "one-rpm-from-two-sources",
        {"a": {"on": ["linux"], "dnf": {"name": "x", "copr": "o/p"}}, "x": {"on": ["linux"]}},
        "packages.x: dnf package 'x' on linux-arm64 is already installed by packages.a",
    ),
    Reject(  # two keys of one [tools] table
        "one-mise-tool-twice",
        {
            "a": {"on": ["linux"], "via": "mise", "mise": {"name": "npm:t"}},
            "b": {"on": ["linux"], "via": "mise", "mise": {"name": "npm:t", "version": "2"}},
        },
        "packages.b: mise tool 'npm:t' on linux-arm64 is already installed by packages.a",
    ),
    Reject(  # two different lines, one tool
        "one-uv-tool-at-two-versions",
        {
            "a": {"on": ["darwin"], "via": "uv", "uv": {"name": "t", "version": "1"}},
            "b": {"on": ["darwin"], "via": "uv", "uv": {"name": "t", "version": "2"}},
        },
        "packages.b: uv tool 't' on darwin-arm64 is already installed by packages.a",
    ),
    Reject(  # two installers, one executable
        "one-upstream-executable-twice",
        {
            "a": {
                "on": ["darwin"],
                "via": "upstream",
                "upstream": {"command": "a", "creates": "x"},
            },
            "b": {
                "on": ["darwin"],
                "via": "upstream",
                "upstream": {"command": "b", "creates": "x"},
            },
        },
        "packages.b: upstream executable 'x' on darwin-arm64 is already installed by packages.a",
    ),
    Reject(  # checked on the resolved package, so an override's rename counts
        "a-clash-an-override-makes",
        {
            "a": {"on": ["darwin"]},
            "b": {"on": ["darwin"], "overrides": [{"when": "darwin/amd64", "brew": {"name": "a"}}]},
        },
        "packages.b: brew formula 'a' on darwin-amd64 is already installed by packages.a",
    ),
    Reject(  # both would write the one _c file
        "two-packages-generate-one-commands-completion",
        {
            "a": {"on": ["darwin"], "completions": {"c": "x"}},
            "b": {"on": ["darwin"], "via": "mise", "completions": {"c": "y"}},
        },
        "packages.b.completions.c: the completion for 'c' on darwin-arm64 is already generated"
        " by packages.a",
    ),
    # --- the shape of one package
    Reject(
        "unknown-key",
        {"foo": {"on": ["darwin"], "nope": "1"}},
        "packages.foo.nope: Extra inputs are not permitted",
    ),
    Reject("missing-on", {"foo": {"via": "mise"}}, "packages.foo.on: Field required"),
    Reject(
        "on-empty",
        {"foo": {"on": []}},
        "packages.foo.on: List should have at least 1 item after validation, not 0",
    ),
    Reject(  # through `load`, the per-entry message, not the whole document's
        "on-unknown-tag", {"oops": {"on": ["mars"]}}, "packages.oops.on.0: unknown tag 'mars'"
    ),
    Reject(  # a real OS with an arch we do not render for: the tag is what is wrong, not a field
        "on-known-os-unknown-arch",
        {"foo": {"on": ["darwin/x86_64"]}},
        "packages.foo.on.0: unknown tag 'darwin/x86_64'",
    ),
    Reject(
        # `name` is discriminating (Cask lacks it) but does not choose a variant, so it may not
        # clear the block: renaming a cask has to fail loudly, not turn it into a formula.
        "a-name-patch-may-not-demote-a-cask-to-a-formula",
        {
            "app": {
                "on": ["darwin"],
                "brew": {"cask": "app"},
                "overrides": [{"when": "darwin/amd64", "brew": {"name": "app"}}],
            }
        },
        "packages.app.overrides[darwin/amd64].brew: a formula ({ name }) or a cask"
        " ({ cask }), not both",
    ),
    Reject(
        "via-invalid",
        {"foo": {"on": ["darwin"], "via": "pip"}},
        "packages.foo: via must be system|mise|uv|upstream",
    ),
    # the point of the blocks: a backend's parameters cannot land on another backend
    Reject(
        "mise-block-on-a-system-package",
        {"foo": {"on": ["darwin"], "mise": {"version": "1"}}},
        "packages.foo.mise: no mise block on a `via: system` package",
    ),
    Reject(  # the command is the whole of the how, so there is no default to fall back on
        "upstream-without-a-command",
        {"foo": {"on": ["darwin"], "via": "upstream"}},
        "packages.foo.upstream: Field required",
    ),
    Reject(  # without it, a re-run would re-install over the tool's own self-update
        "upstream-without-what-it-creates",
        {
            "foo": {
                "on": ["darwin"],
                "via": "upstream",
                "upstream": {"command": "x"},
            }
        },
        "packages.foo.upstream.creates: Field required",
    ),
    Reject(  # rendered inside sh double quotes
        "upstream-creates-that-escapes-its-quotes",
        {
            "foo": {
                "on": ["darwin"],
                "via": "upstream",
                "upstream": {"command": "x", "creates": 'a" b'},
            }
        },
        'packages.foo.upstream.creates: rendered inside sh double quotes, so no ", ` or \\',
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
        "override-missing-when",
        {"foo": {"on": ["darwin"], "overrides": [{"via": "mise"}]}},
        "packages.foo.overrides[?].when: Field required",
    ),
    Reject(
        "override-unknown-tag",
        {"foo": {"on": ["darwin"], "overrides": [{"when": "mars"}]}},
        "packages.foo.overrides[mars].when: unknown tag 'mars'",
    ),
    Reject(
        "override-of-on",
        {"foo": {"on": ["darwin"], "overrides": [{"when": "linux", "on": ["linux"]}]}},
        "packages.foo.overrides[linux]: cannot override 'on'",
    ),
    Reject(
        "override-via-invalid",
        {"foo": {"on": ["darwin"], "overrides": [{"when": "linux", "via": "pip"}]}},
        "packages.foo.overrides[linux].via: Input should be 'system', 'mise', 'uv' or 'upstream'",
    ),
    Reject(
        "override-that-matches-nothing-is-still-checked",
        {
            "foo": {
                "on": ["darwin"],
                "overrides": [{"when": "linux/amd64", "mise": {"version": "1"}}],
            }
        },
        "packages.foo.overrides[linux/amd64].mise: no mise block on a `via: system` package",
    ),
    Reject(
        "components-empty",
        {"foo": {"on": ["darwin"], "via": "mise", "mise": {"components": []}}},
        "packages.foo.mise.components: List should have at least 1 item after validation, not 0",
    ),
    Reject(
        "completions-empty",
        {"foo": {"on": ["darwin"], "completions": {}}},
        "packages.foo.completions: Dictionary should have at least 1 item after validation, not 0",
    ),
    Reject(
        "completions-recipe-empty",
        {"foo": {"on": ["darwin"], "completions": {"foo": ""}}},
        "packages.foo.completions.foo: String should have at least 1 character",
    ),
    Reject(
        "completions-not-overridable",  # fpath order already arbitrates per-backend differences
        {"foo": {"on": ["darwin"], "overrides": [{"when": "linux", "completions": {"foo": "x"}}]}},
        "packages.foo.overrides[linux]: cannot override 'completions'",
    ),
]


def _from_files(r: Reject, tmp_path: Path) -> Inventory:
    """`Inventory.load` on the synthetic data files with the row's tables written in. Load
    checks each document whole first; that check must not preempt the row's message."""
    data = Path(shutil.copytree(FIXTURE_DATA, tmp_path / ".chezmoidata"))
    write_table(data / "packages.toml", "packages", r.packages)
    if r.machines is not None:
        write_table(data / "machines.toml", "machines", r.machines)
    return Inventory.load(data)


@pytest.mark.parametrize("entry", ["constructor", "load"])
@pytest.mark.parametrize("r", [pytest.param(r, id=r.id) for r in REJECTS])
def test_validation_rejects(r: Reject, entry: str, tmp_path: Path):
    with pytest.raises(InventoryError) as e:
        inv = (
            inventory(r.packages, r.machines)
            if entry == "constructor"
            else _from_files(r, tmp_path)
        )
        if r.resolve is not None:
            inv.resolved(r.resolve)
    # Equality, not `in`: a substring cannot see the lines _explain exists to suppress, so a
    # dropped dedupe or noise filter passes under it.
    assert str(e.value) == r.message


def test_machine_must_name_a_declared_platform():
    with pytest.raises(InventoryError, match="machines.x: unknown platform 'nope'"):
        Inventory(
            {"macos": {"os": "darwin", "system": "brew"}},
            {"x": {"platform": "nope", "arch": "arm64"}},
            {},
        )


def test_load_rejects_a_stray_top_level_table(source: Path, tmp_path: Path):
    """A misspelt table would otherwise vanish silently: every document is checked whole."""
    src = _source_copy(source, tmp_path)
    with (src / ".chezmoidata" / "machines.toml").open("a") as f:
        f.write('\n[machine]\nlaptop = { platform = "macos", arch = "arm64" }\n')
    with pytest.raises(InventoryError, match="machine: Extra inputs are not permitted"):
        Inventory.load(src / ".chezmoidata")


def test_platform_system_manager_is_one_we_render_for():
    with pytest.raises(
        InventoryError, match="platforms.debian.system: Input should be 'brew' or 'dnf'"
    ):
        Inventory({"debian": {"os": "linux", "system": "apt"}}, {}, {})


# --- the local layer: packages.local.toml -------------------------------------------------------


def _local_data(tmp_path: Path, local: str) -> Path:
    """The synthetic data files with a packages.local.toml beside them."""
    data = Path(shutil.copytree(FIXTURE_DATA, tmp_path / ".chezmoidata"))
    (data / LOCAL_PACKAGES).write_text(local)
    return data


def test_local_layer_adds_packages(tmp_path: Path):
    inv = Inventory.load(_local_data(tmp_path, '[packages.extra]\non = ["darwin"]\n'))
    for m in MACHINES:
        assert ('brew "extra"' in lines(inv, "brewfile", m)) == (_os(m) == "darwin")


def test_local_entry_replaces_the_tracked_one_whole(tmp_path: Path):
    """Like mise.local.toml: a key the local file declares means what the local file says,
    nothing of the tracked entry carries over — not its `on`, its backend, its completions."""
    inv = Inventory.load(_local_data(tmp_path, '[packages.kit]\non = ["darwin"]\n'))
    for m in MACHINES:
        on_darwin = _os(m) == "darwin"
        assert ('brew "kit"' in lines(inv, "brewfile", m)) == on_darwin
        assert not any(line.startswith('"kit" ') for line in lines(inv, "mise-tools", m))
        assert not any(line.startswith("kitc ") for line in lines(inv, "completions", m))


def test_local_entries_are_checked_like_tracked_ones(tmp_path: Path):
    with pytest.raises(InventoryError, match=r"^packages\.oops\.on"):
        Inventory.load(_local_data(tmp_path, '[packages.oops]\non = ["mars"]\n'))


def test_local_file_rejects_a_stray_top_level_table(tmp_path: Path):
    with pytest.raises(InventoryError, match="package: Extra inputs are not permitted"):
        Inventory.load(_local_data(tmp_path, '[package.extra]\non = ["darwin"]\n'))


# --- real data ----------------------------------------------------------------------------------


def test_real_data_compiles_every_artifact_for_every_declared_machine():
    """All the real data is held to: it passes every check the compiler makes."""
    real = Inventory.load(DATA)
    want = {f"{m}/{a}" for m in real.machines for a in ARTIFACTS} | {STAMP}
    assert set(render_tree(real, DATA)) == want


def test_real_data_is_the_only_chezmoidata_in_the_source():
    """chezmoi reads every `.chezmoidata` directory or `.chezmoidata.<format>` file in the source
    tree, ignored paths included, and merges them over the real data: a test fixture named so
    once pointed every XDG base directory at the fixture's values."""
    tracked = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True, check=True
    ).stdout.split("\0")

    def is_data(path: Path) -> bool:
        return ".chezmoidata" in path.parent.parts or path.name.startswith(".chezmoidata.")

    strays = [
        p
        for p in tracked
        if p and is_data(Path(p)) and Path(p).parent != DATA.relative_to(REPO_ROOT)
    ]
    assert strays == []


# --- the synthetic source: what the consumer tests can rely on ----------------------------------

# Artifacts only one kind of system package manager fills; every other artifact, any machine can.
SYSTEM_ONLY = {"brewfile": "brew", "dnf-list": "dnf", "dnf-repos": "dnf"}


def holds(artifact: str, machine: str) -> bool:
    system = SYSTEM_ONLY.get(artifact)
    return system is None or SYN.platforms[SYN.machines[machine].platform].system == system


@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_fixture_artifact_is_non_empty_and_machine_specific_wherever_it_can_be(artifact: str):
    """The premise of every consumer test below. An empty artifact makes `heredoc == lines`
    compare [] with [], and two machines with the same copy let a template that read the wrong
    machine's directory pass."""
    texts = {m: render(artifact, SYN.resolved(m)) for m in MACHINES}
    held = {m: t for m, t in texts.items() if holds(artifact, m)}
    assert held and all(held.values()), [m for m, t in held.items() if not t]
    assert len(set(held.values())) == len(held), "two machines share one copy"
    assert all(t == "" for m, t in texts.items() if m not in held)


# --- .rendered/: the compiled tree chezmoi includes ---------------------------------------------


def _artifact_tmpl(tmp_path: Path, artifact: str) -> Path:
    tmpl = tmp_path / "t.tmpl"
    tmpl.write_text('{{ includeTemplate "artifact" (merge (dict "name" "' + artifact + '") .) }}')
    return tmpl


@pytest.mark.parametrize("machine", MACHINES)
@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_artifact_template_serves_the_named_file_from_this_machines_dir(
    artifact: str, machine: str, source: Path, tmp_path: Path
):
    """Each artifact differs between machines and from the other artifacts, so a template that
    read another machine's directory, or a fixed name, serves the wrong text. The stamp guard
    is a separate fact — test_artifact_template_refuses_a_stale_tree owns it."""
    got = chezmoi_render(_artifact_tmpl(tmp_path, artifact), machine, source)
    assert got == (source / RENDERED_DIR / machine / artifact).read_text()


def test_artifact_template_refuses_a_stale_tree(source: Path, tmp_path: Path):
    src = _source_copy(source, tmp_path)
    with (src / ".chezmoidata" / DATA_FILES[1]).open("a") as f:
        f.write("\n# an edit nobody rendered\n")
    with pytest.raises(AssertionError, match=r"\.rendered/ is stale"):
        chezmoi_render(_artifact_tmpl(tmp_path, "brewfile"), MACHINES[0], src)


def _rendered_with_local(source: Path, tmp_path: Path, local: str) -> Path:
    src = _source_copy(source, tmp_path)
    (src / ".chezmoidata" / LOCAL_PACKAGES).write_text(local)
    assert main(["all", "--root", str(src)]) == 0
    return src


def test_artifact_template_serves_a_tree_rendered_with_the_local_file(source: Path, tmp_path: Path):
    """The template's hash and STAMP agree on the local file: a tree rendered with it is
    current, and serves what it declares."""
    src = _rendered_with_local(source, tmp_path, '[packages.extra]\non = ["darwin"]\n')
    got = chezmoi_render(_artifact_tmpl(tmp_path, "brewfile"), MACHINES[0], src)
    assert 'brew "extra"' in got.splitlines()


@pytest.mark.parametrize("change", ["edited", "deleted"])
def test_artifact_template_refuses_a_tree_stale_on_the_local_file(
    change: str, source: Path, tmp_path: Path
):
    src = _rendered_with_local(source, tmp_path, '[packages.extra]\non = ["darwin"]\n')
    local = src / ".chezmoidata" / LOCAL_PACKAGES
    if change == "edited":
        local.write_text('[packages.other]\non = ["darwin"]\n')
    else:
        local.unlink()
    with pytest.raises(AssertionError, match=r"\.rendered/ is stale"):
        chezmoi_render(_artifact_tmpl(tmp_path, "brewfile"), MACHINES[0], src)


def test_artifact_template_refuses_an_undeclared_machine(source: Path, tmp_path: Path):
    # 386 is a real GOARCH that model.py's Arch literal cannot spell, so no machines.toml
    # entry can ever make this machine a declared one.
    proc = chezmoi_execute(_artifact_tmpl(tmp_path, "brewfile"), source, "linux", "386")
    assert proc.returncode != 0
    assert "linux-386 is not declared" in proc.stderr


# --- JSON Schemas: the types, for the editor ------------------------------------------------------


def schema_for(name: str) -> Any:  # jsonschema ships no types; a Draft202012Validator
    schema = json.loads((SCHEMA / f"{Path(name).stem}.json").read_text())
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


@pytest.mark.parametrize("name", list(DOCS))
def test_each_data_file_validates_against_its_schema(name: str):
    """The `#:schema` line at the top of each .chezmoidata file names this file."""
    first = (DATA / name).read_text().splitlines()[0]
    assert first == f"#:schema ../{SCHEMA_DIR}/{Path(name).stem}.json"
    schema_for(name).validate(load_data(DATA / name))


def test_no_schema_offers_the_editor_a_null_it_cannot_write():
    """`X | None` fields come out of pydantic as `anyOf: [{…}, {type: null}]`. TOML has no null,
    so leaving that branch in tells the editor `name = null` is a legal line in a file format
    that cannot express it — which is the whole reason _no_null exists."""
    # The rendered text, not a re-dump of it: json.dumps over a dict of strings escapes the
    # inner quotes, and `"null"` would never match.
    assert [name for name, text in schemas().items() if '"null"' in text] == []


SCHEMA_REJECTS = [
    ("mise-block-on-a-system-package", {"on": ["darwin"], "mise": {"version": "1"}}),
    ("brew-block-on-a-mise-package", {"on": ["darwin"], "via": "mise", "brew": {"cask": "x"}}),
    ("version-outside-a-block", {"on": ["darwin"], "version": "9"}),
    ("brew-both-flavours", {"on": ["darwin"], "brew": {"name": "a", "cask": "b"}}),
    ("dnf-two-sources", {"on": ["linux"], "dnf": {"copr": "a/b", "repofile": "u"}}),
    ("unknown-tag", {"on": ["mars"]}),
    ("on-empty", {"on": []}),
    ("unknown-key", {"on": ["darwin"], "nope": 1}),
    ("override-of-on", {"on": ["darwin"], "overrides": [{"when": "linux", "on": ["linux"]}]}),
    ("bad-via", {"on": ["darwin"], "via": "pip"}),
    ("components-empty", {"on": ["darwin"], "via": "mise", "mise": {"components": []}}),
    ("completions-empty", {"on": ["darwin"], "completions": {}}),
]


@pytest.mark.parametrize("packages", [pytest.param(c.packages, id=c.id) for c in RENDER_CASES])
def test_packages_schema_accepts_what_the_types_accept(packages: Mapping[str, Any]):
    schema_for("packages.toml").validate({"packages": packages})


@pytest.mark.parametrize("package", [pytest.param(p, id=i) for i, p in SCHEMA_REJECTS])
def test_packages_schema_rejects_what_the_types_reject(package: Mapping[str, Any]):
    with pytest.raises(InventoryError):
        inventory({"foo": package})
    assert not schema_for("packages.toml").is_valid({"packages": {"foo": package}})


def test_write_tree_only_touches_what_changed_and_removes_what_left(tmp_path: Path):
    out = tmp_path / "out"
    files = {"a/x": "1\n", "b/y": "2\n", "STAMP": "s\n"}
    assert sorted(write_tree(files, out)) == ["STAMP", "a/x", "b/y"]
    before = (out / "a" / "x").stat().st_mtime_ns
    smaller = {"a/x": "1\n", "STAMP": "t\n"}
    assert write_tree(smaller, out) == ["STAMP", "b/y"]
    assert not (out / "b").exists() and (out / "a" / "x").stat().st_mtime_ns == before
    assert stale(smaller, out) == []


def test_all_writes_both_trees(source: Path, tmp_path: Path):
    """One `render` restores whatever was clobbered, in .rendered/ and .schema/ alike."""
    src = _source_copy(source, tmp_path)
    dirty = (f"{RENDERED_DIR}/{MACHINES[-1]}/uv-tools", f"{SCHEMA_DIR}/machines.json")
    for rel in dirty:
        (src / rel).write_text("clobbered\n")
    assert main(["all", "--root", str(src)]) == 0
    for rel in dirty:
        assert (src / rel).read_text() == (source / rel).read_text()


def _trees(src: Path) -> dict[str, str]:
    return {
        str(p.relative_to(src)): p.read_text()
        for tree in (RENDERED_DIR, SCHEMA_DIR)
        for p in (src / tree).rglob("*")
        if p.is_file()
    }


def test_all_on_broken_data_writes_neither_tree(source: Path, tmp_path: Path):
    """The schema tree needs no data and could be written; `all` still writes nothing when
    the data does not parse, so a failed run leaves no half-updated state behind."""
    src = _source_copy(source, tmp_path)
    for rel in (f"{RENDERED_DIR}/{MACHINES[-1]}/uv-tools", f"{SCHEMA_DIR}/machines.json"):
        (src / rel).write_text("clobbered\n")
    (src / SCHEMA_DIR / "zsh.json").unlink()
    with (src / ".chezmoidata" / "packages.toml").open("a") as f:
        f.write('\n[packages.oops]\non = ["mars"]\n')
    before = _trees(src)
    assert main(["all", "--root", str(src)]) == 2
    assert _trees(src) == before


def test_render_schema_needs_no_valid_data(source: Path, tmp_path: Path):
    """`uv run render schema` comes from the types alone: it still works — and `all` does not —
    while a data file is broken, which is when the editor needs the schema most."""
    src = _source_copy(source, tmp_path)
    with (src / ".chezmoidata" / "packages.toml").open("a") as f:
        f.write('\n[packages.oops]\non = ["mars"]\n')
    assert main(["all", "--root", str(src)]) == 2
    assert main(["schema", "--root", str(src)]) == 0
    assert sorted(p.name for p in (src / SCHEMA_DIR).iterdir()) == sorted(
        f"{Path(n).stem}.json" for n in DOCS
    )


# --- the consumers: scripts and configs that embed the artifacts --------------------------------

BASH = shutil.which("bash") or "/bin/bash"


def stub_bin(tmp_path: Path, scripts: Mapping[str, str]) -> Path:
    """A PATH directory holding one `#!/bin/sh` executable per name, with the given body."""
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    for name, body in scripts.items():
        (stub / name).write_text(f"#!/bin/sh\n{body}\n")
        (stub / name).chmod(0o755)
    return stub


def recorder(log: Path) -> str:
    """A stub body appending its argv, one call per line, to `log`."""
    return f'printf "%s\\n" "$*" >> "{log}"'


@pytest.mark.parametrize("machine", MACHINES)
def test_install_script_renders_to_valid_shell(machine: str, source: Path, tmp_path: Path):
    """System packages through this machine's package manager only, then the uv tools."""
    rendered = chezmoi_render(source / INSTALL_SCRIPT, machine, source)
    script = tmp_path / "install-packages.sh"
    script.write_text(rendered)
    check_shell(script)
    if _os(machine) == "darwin":
        assert "dnf" not in rendered
        pkg_block = rendered.split("brew bundle install --no-upgrade --file=-", 1)[1]
        assert heredoc(pkg_block, "EOF") == lines(SYN, "brewfile", machine)
    else:
        assert "brew" not in rendered
        repo_block = rendered.split("copr enable", 1)[1]
        assert heredoc(repo_block, "EOF") == lines(SYN, "dnf-repos", machine)
        pkg_block = rendered.split("xargs -r sudo dnf -y install", 1)[1]
        assert heredoc(pkg_block, "EOF") == lines(SYN, "dnf-list", machine)
    uv_block = rendered.split("uv tool install", 1)[1]
    assert heredoc(uv_block, "EOF") == lines(SYN, "uv-tools", machine)


@pytest.mark.parametrize("machine", MACHINES)
def test_install_script_settles_the_bootstrap_tools_between_the_packages_and_the_uv_tools(
    machine: str, source: Path
):
    """After the system packages, which is where the upstream installers' own dependencies
    come from on a Linux box; before `uv tool install`, which needs the uv they may supply."""
    rendered = chezmoi_render(source / INSTALL_SCRIPT, machine, source)
    want = lines(SYN, "upstream-installs", machine)
    assert heredoc(rendered.split('sh -c "$cmd"', 1)[1], "EOF") == want
    packages = "brew bundle install" if _os(machine) == "darwin" else "sudo dnf -y install"
    assert rendered.index(packages) < rendered.index('sh -c "$cmd"')
    assert rendered.index('sh -c "$cmd"') < rendered.index("uv tool install")


@pytest.mark.parametrize("machine", [m for m in MACHINES if _os(m) == "darwin"])
def test_a_failing_package_manager_does_not_skip_the_rest_of_the_apply(
    machine: str, source: Path, tmp_path: Path
):
    """An unbottled formula is routine on a Tier 3 Mac, and this is a `run_onchange_before_`
    script: aborting on one would skip the bootstrap tools, the uv tools, and every dotfile of
    the apply. The failure still has to reach chezmoi, so the script exits non-zero anyway.

    Run with the package manager stubbed to fail and `sh`/`uv` stubbed to record: what the
    later steps get called with is the behaviour, not the shell spelling that achieves it.
    """
    stub = stub_bin(
        tmp_path,
        {
            "brew": "exit 1",
            "sh": recorder(tmp_path / "sh.log"),
            "uv": recorder(tmp_path / "uv.log"),
        },
    )
    script = tmp_path / "install-packages.sh"
    script.write_text(chezmoi_render(source / INSTALL_SCRIPT, machine, source))
    proc = subprocess.run(
        [BASH, str(script)],
        env={"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode != 0, "a failed package must still reach chezmoi"
    assert (tmp_path / "sh.log").read_text().splitlines() == [
        f"-c {cmd}" for cmd in lines(SYN, "upstream-installs", machine)
    ], "bootstrap installers were skipped"
    assert (tmp_path / "uv.log").read_text().splitlines() == [
        f"tool install {spec}" for spec in lines(SYN, "uv-tools", machine)
    ], "uv tools were skipped"


@pytest.mark.parametrize("machine", MACHINES)
def test_mise_config_renders_the_tools_table(machine: str, source: Path):
    doc = tomllib.loads(chezmoi_render(source / MISE_CONFIG, machine, source))
    assert doc["tools"] == tomllib.loads("\n".join(lines(SYN, "mise-tools", machine)))
    assert "refresh-completions --changed" in doc["hooks"]["postinstall"], (
        "postinstall must stay on the changed-only path — a bare refresh regenerates "
        "every recipe after any mise install session, in any directory"
    )


@pytest.mark.parametrize("machine", MACHINES)
def test_upgrade_upstream_task_renders_to_valid_bash(machine: str, source: Path, tmp_path: Path):
    script = tmp_path / "upgrade-upstream"
    rendered = chezmoi_render(source / UPGRADE_TASK, machine, source)
    script.write_text(rendered)
    check_shell(script)
    assert heredoc(rendered, "EOF") == lines(SYN, "upstream-updates", machine)


def run_upgrade_task(
    source: Path, machine: str, tmp_path: Path, installed: Mapping[str, str]
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Render this machine's upgrade-upstream task and run it with each `installed` tool
    (name -> stub body) present at $HOME/.local/bin/<name>, also on PATH, recording its argv
    to `<name>.log`. The fixture's upstream tools create and update themselves by that name."""
    home = tmp_path / "home"
    bin_dir = home / ".local" / "bin"
    bin_dir.mkdir(parents=True)
    for name, body in installed.items():
        (bin_dir / name).write_text(f"#!/bin/sh\n{recorder(tmp_path / f'{name}.log')}\n{body}\n")
        (bin_dir / name).chmod(0o755)
    script = tmp_path / "upgrade-upstream"
    script.write_text(chezmoi_render(source / UPGRADE_TASK, machine, source))
    proc = subprocess.run(
        [BASH, str(script)],
        env={"PATH": f"{bin_dir}:/usr/bin:/bin", "HOME": str(home)},
        text=True,
        capture_output=True,
        check=False,
    )
    return proc, tmp_path


@pytest.mark.parametrize("machine", MACHINES)
def test_a_failing_update_does_not_skip_the_rest(machine: str, source: Path, tmp_path: Path):
    """boot-<machine> sorts before bootshared, so its failure comes first; the shared tool is
    still updated, and the failure still reaches the caller."""
    proc, logs = run_upgrade_task(source, machine, tmp_path, {machine: "exit 1", "shared": ""})
    assert proc.returncode != 0, "a failed update must reach the exit status"
    assert (logs / f"{machine}.log").read_text().splitlines() == ["self-update"]
    assert (logs / "shared.log").read_text().splitlines() == ["self-update"], (
        "the update after a failing one was skipped"
    )


@pytest.mark.parametrize("machine", MACHINES)
def test_an_upstream_tool_not_installed_here_is_skipped(machine: str, source: Path, tmp_path: Path):
    """Supplying a missing tool is the install script's job; its absence is not a failure."""
    proc, logs = run_upgrade_task(source, machine, tmp_path, {"shared": ""})
    assert proc.returncode == 0, proc.stderr
    assert (logs / "shared.log").read_text().splitlines() == ["self-update"]
    assert not (logs / f"{machine}.log").exists()


@pytest.mark.parametrize("machine", MACHINES)
def test_refresh_completions_task_renders_to_valid_bash(machine: str, source: Path, tmp_path: Path):
    """The recipe heredoc is `read` line by line: one `<cmd> <recipe>` per line, no blank cmd."""
    script = tmp_path / "refresh-completions"
    rendered = chezmoi_render(source / REFRESH_TASK, machine, source)
    script.write_text(rendered)
    check_shell(script)
    assert heredoc(rendered, "RECIPES") == lines(SYN, "completions", machine)
    assert heredoc(rendered, "TOOLMAP") == lines(SYN, "mise-completions", machine)


def run_refresh_task(
    source: Path, machine: str, tmp_path: Path, args: list[str], env: Mapping[str, str]
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Render this machine's refresh-completions task and run it hermetically: a private
    cache dir and $HOME, and a PATH of system utilities only. No synthetic recipe command
    exists there, so every recipe the run selects reports `skip  _<cmd>`."""
    script = tmp_path / "refresh-completions"
    script.write_text(chezmoi_render(source / REFRESH_TASK, machine, source))
    proc = subprocess.run(
        [BASH, str(script), *args],
        env={
            "PATH": "/usr/bin:/bin",
            "HOME": str(tmp_path),
            "ZDOTDIR": str(tmp_path),
            "XDG_CACHE_HOME": str(tmp_path / "cache"),
            **env,
        },
        text=True,
        capture_output=True,
        check=False,
    )
    return proc, tmp_path / "cache" / GENERATED


@pytest.mark.parametrize("machine", MACHINES)
def test_refresh_changed_ignores_tools_without_recipes(machine: str, source: Path, tmp_path: Path):
    """The postinstall hook's hot path: a session that installed only tools owning no
    recipe — any project-local `mise use` — writes nothing and exits 0 immediately."""
    proc, out = run_refresh_task(
        source,
        machine,
        tmp_path,
        ["--changed"],
        {"MISE_INSTALLED_TOOLS": '[{"name":"no-recipe-tool","version":"1.0.0"}]'},
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert not out.exists()


@pytest.mark.parametrize("machine", MACHINES)
def test_refresh_changed_overlays_only_the_installed_tools_commands(
    machine: str, source: Path, tmp_path: Path
):
    """`--changed` processes the installed tools' commands and nothing else; files other
    tools own stay in place (removal is the no-arg rebuild's job)."""
    tool, *cmds = lines(SYN, "mise-completions", machine)[0].split()
    (tmp_path / "cache" / GENERATED).mkdir(parents=True)
    (tmp_path / "cache" / GENERATED / "_zzz").write_text("#compdef zzz\n")
    proc, out = run_refresh_task(
        source,
        machine,
        tmp_path,
        ["--changed"],
        {"MISE_INSTALLED_TOOLS": json.dumps([{"name": tool, "version": "1.0.0"}])},
    )
    assert proc.returncode == 0, proc.stderr
    processed = {ln.split()[1] for ln in proc.stdout.splitlines()}
    assert processed == {f"_{c}" for c in cmds}
    assert (out / "_zzz").exists()
    assert {p.name for p in out.iterdir()} <= {"_zzz", *(f"_{c}" for c in cmds)}


@pytest.mark.parametrize("machine", MACHINES)
def test_refresh_full_run_rebuilds_from_scratch(machine: str, source: Path, tmp_path: Path):
    """No args: a file whose tool left packages.toml (or never had a recipe) is gone
    afterwards — the only mode that removes."""
    (tmp_path / "cache" / GENERATED).mkdir(parents=True)
    (tmp_path / "cache" / GENERATED / "_zzz").write_text("#compdef zzz\n")
    proc, out = run_refresh_task(source, machine, tmp_path, [], {})
    assert proc.returncode == 0, proc.stderr
    assert not (out / "_zzz").exists()


def test_refresh_changed_outside_the_hook_fails_loudly(source: Path, tmp_path: Path):
    """`--changed` without the hook's $MISE_INSTALLED_TOOLS is a misuse, not a full run."""
    proc, out = run_refresh_task(source, MACHINES[0], tmp_path, ["--changed"], {})
    assert proc.returncode != 0
    assert "postinstall" in proc.stderr
    assert not out.exists()


@pytest.mark.parametrize("machine", MACHINES)
def test_onchange_refresh_script_keys_on_this_machines_recipes(
    machine: str, source: Path, tmp_path: Path
):
    """chezmoi re-runs it when its rendered content changes, so the recipe list is rendered
    into it verbatim."""
    script = tmp_path / "refresh-completions.sh"
    rendered = chezmoi_render(source / REFRESH_SCRIPT, machine, source)
    script.write_text(rendered)
    check_shell(script)
    assert heredoc(rendered, "RECIPES") == lines(SYN, "completions", machine)


def run_onchange_refresh(
    source: Path, tmp_path: Path, mise: str | None, env: Mapping[str, str] = {}
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run the onchange refresh script with nothing on PATH but, when `mise` is a stub body
    (a `case "$1"` arm list), a `mise` that runs it after logging `<args>|$XDG_CACHE_HOME`.
    Returns the process and the logged calls."""
    log = tmp_path / "mise.log"
    stubs = {}
    if mise is not None:
        stubs["mise"] = (
            f'printf "%s|%s\\n" "$*" "${{XDG_CACHE_HOME-}}" >> "{log}"\ncase "$1" in\n{mise}\nesac'
        )
    stub = stub_bin(tmp_path, stubs)
    script = tmp_path / "refresh-completions.sh"
    script.write_text(chezmoi_render(source / REFRESH_SCRIPT, MACHINES[0], source))
    proc = subprocess.run(
        [BASH, str(script)],
        env={"PATH": str(stub), "HOME": str(tmp_path), **env},
        text=True,
        capture_output=True,
        check=False,
    )
    return proc, log.read_text().splitlines() if log.exists() else []


def test_onchange_refresh_without_mise_defers_without_failing_the_apply(
    source: Path, tmp_path: Path
):
    proc, _ = run_onchange_refresh(source, tmp_path, mise=None)
    assert proc.returncode == 0, proc.stderr


def test_onchange_refresh_defers_while_the_toolset_is_incomplete(source: Path, tmp_path: Path):
    """`mise ls --missing` listing anything means `mise install` has not run: its postinstall
    hook owns the first generation, so no `mise run` here."""
    proc, calls = run_onchange_refresh(source, tmp_path, mise='ls) echo "kit stable" ;;')
    assert proc.returncode == 0, proc.stderr
    assert calls == ["ls --missing|"]


@pytest.mark.parametrize(
    ("preset", "want"),
    [(None, f"$HOME/{CACHE_HOME}"), ("/elsewhere/cache", "/elsewhere/cache")],
    ids=["unset-derives-from-home", "set-is-kept"],
)
def test_onchange_refresh_runs_the_task_with_the_cache_dir(
    preset: str | None, want: str, source: Path, tmp_path: Path
):
    """An apply from a shell that never read ~/.zshenv still hands the task its cache dir."""
    env = {} if preset is None else {"XDG_CACHE_HOME": preset}
    proc, calls = run_onchange_refresh(source, tmp_path, mise="*) ;;", env=env)
    assert proc.returncode == 0, proc.stderr
    want = want.replace("$HOME", str(tmp_path))
    assert calls == [f"ls --missing|{preset or ''}", f"run refresh-completions|{want}"]


def test_onchange_refresh_survives_a_failing_task(source: Path, tmp_path: Path):
    """A failed recipe leaves one completion missing; it must not fail the apply."""
    proc, calls = run_onchange_refresh(source, tmp_path, mise="run) exit 3 ;;")
    assert proc.returncode == 0, proc.stderr
    assert calls[-1].startswith("run refresh-completions|")


@pytest.mark.parametrize("machine", MACHINES)
def test_audit_completions_task_renders_to_valid_zsh(machine: str, source: Path, tmp_path: Path):
    script = tmp_path / "audit-completions"
    rendered = chezmoi_render(source / AUDIT_TASK, machine, source)
    script.write_text(rendered)
    check_shell(script, "zsh")
    system = "brewfile" if _os(machine) == "darwin" else "dnf-list"
    assert heredoc(rendered, "PACKAGES") == lines(SYN, system, machine)
    assert heredoc(rendered, "UVTOOLS") == lines(SYN, "uv-tools", machine)
    assert heredoc(rendered, "RECIPES") == lines(SYN, "completions", machine)
