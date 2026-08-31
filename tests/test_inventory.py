"""Executable spec for the inventory: .chezmoidata/{platforms,machines,packages}.toml, its
types (tools/inventory/model.py), resolution (inventory.py) and the artifacts chezmoi
includes from .rendered/ (render.py, `uv run render`).

Table-driven, and every row is about the compiler, not about what .chezmoidata happens to
say today:
- RENDER_CASES   synthetic packages -> exact lines each artifact holds on each machine
- REJECTS        malformed entries -> the message naming the entry

What the real data compiles to belongs in tests/golden/, never in an assertion here: a row
restating a package's backend has to be edited in the same commit as the change it guards.
`uv run render golden` accepts a change.

Plus: the chezmoi consumers render to valid shell/toml with the artifact's lines inside, and
the `artifact` template refuses stale data and machines nobody declared.
"""

from __future__ import annotations

import json
import logging
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
from inventory.inventory import DATA_FILES, Inventory, InventoryError, load_data
from inventory.model import DOCS
from inventory.render import (
    ARTIFACTS,
    GOLDEN_DIR,
    RENDERED_DIR,
    SCHEMA_DIR,
    artifact_tree,
    render,
    render_tree,
    schemas,
    stale,
    write_tree,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA = REPO_ROOT / ".chezmoidata"
RENDERED = REPO_ROOT / RENDERED_DIR
SCHEMA = REPO_ROOT / SCHEMA_DIR
GOLDEN = REPO_ROOT / GOLDEN_DIR
MISE_TASKS = REPO_ROOT / "dot_config" / "mise" / "tasks"
INSTALL_SCRIPT = REPO_ROOT / "run_onchange_before_install-packages.sh.tmpl"
REFRESH_SCRIPT = REPO_ROOT / "run_onchange_after_refresh-completions.sh.tmpl"

PLATFORMS = load_data(DATA / "platforms.toml")["platforms"]
MACHINES = load_data(DATA / "machines.toml")["machines"]
GENERATED = load_data(DATA / "zsh.toml")["zsh"]["generatedCompletions"]
REAL = Inventory.load(DATA)

# --- harness ------------------------------------------------------------------------------------


@pytest.fixture(scope="session", autouse=True)
def _built_trees() -> None:
    """`.rendered/` and `.schema/` are gitignored build output, so a fresh clone has neither and
    every test that reads them would fail before the suite ever compiled anything. Build them
    up front, the way `uv run render` does.

    chezmoi rebuilds `.rendered/` too: `.chezmoi.toml.tmpl` runs the compiler from a
    read-source-state hook, so every `chezmoi_render` below refreshes it as a side effect. No
    test may therefore assert that the tree on disk is stale — tests/golden/ holds snapshots.
    """
    write_tree(render_tree(REAL, DATA), RENDERED)
    write_tree(schemas(), SCHEMA)


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
        "mise-completions-skip-other-backends",  # their installs never fire mise's hook
        {
            "mise": {"on": ["darwin"], "completions": {"mise": "mise completion zsh"}},
            "ruff": {"on": ["darwin"], "via": "uv", "completions": {"ruff": "x"}},
            "plain": {"on": ["darwin"], "via": "mise"},
        },
        {("mise-completions", "darwin-arm64"): []},
    ),
    RenderCase(
        "upstream-renders-its-command-and-reaches-no-package-manager",
        {
            "boot": {
                "on": ["darwin", "linux"],
                "via": "upstream",
                "upstream": {"command": "curl https://x/install.sh | sh"},
            }
        },
        {
            ("upstream-installs", "darwin-arm64"): ["curl https://x/install.sh | sh"],
            ("upstream-installs", "linux-arm64"): ["curl https://x/install.sh | sh"],
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
    message: str  # what the InventoryError must contain


REJECTS = [
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
    Reject("on-unknown-tag", {"foo": {"on": ["mars"]}}, "packages.foo.on.0: unknown tag 'mars'"),
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


@pytest.mark.parametrize(
    ("packages", "message"), [pytest.param(r.packages, r.message, id=r.id) for r in REJECTS]
)
def test_validation_rejects(packages: Mapping[str, Any], message: str):
    with pytest.raises(InventoryError) as e:
        inventory(packages)
    # Equality, not `in`: a substring cannot see the lines _explain exists to suppress, so a
    # dropped dedupe or noise filter passes under it.
    assert str(e.value) == message


def test_machine_must_name_a_declared_platform():
    with pytest.raises(InventoryError, match="machines.x: unknown platform 'nope'"):
        Inventory(PLATFORMS, {"x": {"platform": "nope", "arch": "arm64"}}, {})


def test_load_rejects_a_stray_top_level_table(tmp_path: Path):
    """A misspelt table would otherwise vanish silently: every document is checked whole."""
    src = _source_copy(tmp_path)
    with (src / ".chezmoidata" / "machines.toml").open("a") as f:
        f.write('\n[machine]\nlaptop = { platform = "macos", arch = "arm64" }\n')
    with pytest.raises(InventoryError, match="machine: Extra inputs are not permitted"):
        Inventory.load(src / ".chezmoidata")


def test_platform_system_manager_is_one_we_render_for():
    with pytest.raises(
        InventoryError, match="platforms.debian.system: Input should be 'brew' or 'dnf'"
    ):
        Inventory({"debian": {"os": "linux", "system": "apt"}}, {}, {})


# --- real data ----------------------------------------------------------------------------------


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


def _machine_showing(artifact: str) -> str:
    """A machine whose copy of `artifact` has content. Every artifact is empty on some machine,
    and an empty file equals every other empty file — asking for one would pass a template that
    ignored the name it was given."""
    for machine in MACHINES:
        if (RENDERED / machine / artifact).read_text().strip():
            return machine
    raise AssertionError(f"{artifact} is empty on every machine, so it can pin nothing")


@pytest.mark.parametrize("artifact", ARTIFACTS)
def test_artifact_template_serves_the_named_file_from_this_machines_dir(
    artifact: str, tmp_path: Path
):
    """One machine per artifact is enough: swapping the machine-id halves breaks every one of
    these, and serving a fixed name breaks all but the one that name belongs to. The stamp
    guard is a separate fact — test_artifact_template_refuses_a_stale_tree owns it."""
    machine = _machine_showing(artifact)
    tmpl = tmp_path / "t.tmpl"
    tmpl.write_text('{{ includeTemplate "artifact" (merge (dict "name" "' + artifact + '") .) }}')
    assert chezmoi_render(tmpl, machine) == (RENDERED / machine / artifact).read_text()


def _source_copy(tmp_path: Path) -> Path:
    src = tmp_path / "source"
    src.mkdir()
    for d in (".chezmoidata", ".chezmoitemplates", RENDERED_DIR, SCHEMA_DIR):
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
            # 386 is a real GOARCH that model.py's Arch literal cannot spell, so no
            # machines.toml entry can ever make this machine a declared one.
            "--override-data", json.dumps({"chezmoi": {"os": "linux", "arch": "386"}}),
            "--file", str(tmpl),
        ],
        text=True,
        capture_output=True,
        check=False,
    )  # fmt: skip
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


def test_artifacts_match_the_golden():
    """The committed snapshot of what the real data compiles to. It fails on any change to an
    artifact — intended or not — and `uv run render golden` is how an intended one is accepted,
    in a commit where the diff shows what else moved with it."""
    assert stale(artifact_tree(REAL), GOLDEN) == []


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


DIRTY = [
    ("all", f"{RENDERED_DIR}/linux-amd64/dnf-list", 1),
    ("all", f"{SCHEMA_DIR}/machines.json", 1),
    ("schema", f"{SCHEMA_DIR}/machines.json", 1),
    ("schema", f"{RENDERED_DIR}/linux-amd64/dnf-list", 0),  # `schema` owns only its own tree
]


@pytest.mark.parametrize(("what", "victim", "rc"), DIRTY, ids=lambda v: str(v))
def test_check_exits_1_and_names_every_stale_file(
    what: str, victim: str, rc: int, tmp_path: Path, caplog: pytest.LogCaptureFixture
):
    """What prek runs before every commit. `all` covers both trees, `schema` only its own."""
    src = _source_copy(tmp_path)
    assert main([what, "--check", "--root", str(src)]) == 0
    (src / victim).write_text("not what the types compile to\n")
    with caplog.at_level(logging.ERROR, logger="inventory"):
        assert main([what, "--check", "--root", str(src)]) == rc
    assert (f"stale: {victim}" in caplog.text) is (rc == 1)


def test_all_writes_both_trees(tmp_path: Path):
    """One `render` restores whatever was clobbered, in .rendered/ and .schema/ alike."""
    src = _source_copy(tmp_path)
    dirty = (f"{RENDERED_DIR}/linux-amd64/dnf-list", f"{SCHEMA_DIR}/machines.json")
    for rel in dirty:
        (src / rel).write_text("clobbered\n")
    assert main(["all", "--root", str(src)]) == 0
    for rel in dirty:
        assert (src / rel).read_text() == (REPO_ROOT / rel).read_text()


def test_render_schema_needs_no_valid_data(tmp_path: Path):
    """`uv run render schema` comes from the types alone: it still works — and `all` does not —
    while a data file is broken, which is when the editor needs the schema most."""
    src = _source_copy(tmp_path)
    with (src / ".chezmoidata" / "packages.toml").open("a") as f:
        f.write('\n[packages.oops]\non = ["mars"]\n')
    assert main(["all", "--root", str(src)]) == 2
    assert main(["schema", "--root", str(src)]) == 0
    assert main(["schema", "--check", "--root", str(src)]) == 0
    assert sorted(p.name for p in (src / SCHEMA_DIR).iterdir()) == sorted(
        f"{Path(n).stem}.json" for n in DOCS
    )


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
        pkg_block = rendered.split("brew bundle install --file=-", 1)[1]
        assert heredoc(pkg_block, "EOF") == lines(REAL, "brewfile", machine)
    else:
        assert "xargs -r sudo dnf -y install" in rendered
        assert "brew" not in rendered
        pkg_block = rendered.split("copr enable", 1)[1]
        assert heredoc(pkg_block, "EOF") == lines(REAL, "dnf-repos", machine)
    assert rendered.index("uv tool install") > rendered.index("<<'EOF'"), "uv tools come after uv"
    check_shell(script)
    uv_block = rendered.split("uv tool install", 1)[1]
    assert heredoc(uv_block, "EOF") == lines(REAL, "uv-tools", machine)


@pytest.mark.parametrize("machine", MACHINES)
def test_install_script_settles_the_bootstrap_tools_between_the_packages_and_the_uv_tools(
    machine: str,
):
    """After the system packages, which is where the upstream installers' own dependencies
    come from on a Linux box; before `uv tool install`, which needs the uv they may supply."""
    rendered = chezmoi_render(INSTALL_SCRIPT, machine)
    want = lines(REAL, "upstream-installs", machine)
    assert want, "mise comes from upstream on every machine"
    assert heredoc(rendered.split('sh -c "$cmd"', 1)[1], "EOF") == want
    packages = "brew bundle install" if _os(machine) == "darwin" else "sudo dnf -y install"
    assert rendered.index(packages) < rendered.index('sh -c "$cmd"')
    assert rendered.index('sh -c "$cmd"') < rendered.index("uv tool install")


@pytest.mark.parametrize("machine", [m for m in MACHINES if _os(m) == "darwin"])
def test_a_failing_package_manager_does_not_skip_the_rest_of_the_apply(
    machine: str, tmp_path: Path
):
    """An unbottled formula is routine on a Tier 3 Mac, and this is a `run_onchange_before_`
    script: aborting on one would skip the bootstrap tools, the uv tools, and every dotfile of
    the apply. The failure still has to reach chezmoi, so the script exits non-zero anyway.

    Run with the package manager stubbed to fail and `sh`/`uv` stubbed to record: what the
    later steps get called with is the behaviour, not the shell spelling that achieves it.
    """
    stub = tmp_path / "bin"
    stub.mkdir()
    (stub / "brew").write_text("#!/bin/sh\nexit 1\n")
    for name in ("sh", "uv"):
        (stub / name).write_text(f'#!/bin/sh\nprintf "%s\\n" "$*" >> "{tmp_path}/{name}.log"\n')
    for f in stub.iterdir():
        f.chmod(0o755)
    script = tmp_path / "install-packages.sh"
    script.write_text(chezmoi_render(INSTALL_SCRIPT, machine))
    proc = subprocess.run(
        ["bash", str(script)],
        env={"PATH": f"{stub}:/usr/bin:/bin", "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
        check=False,
    )
    assert proc.returncode != 0, "a failed package must still reach chezmoi"
    assert "mise.run" in (tmp_path / "sh.log").read_text(), "bootstrap installers were skipped"
    assert "tool install" in (tmp_path / "uv.log").read_text(), "uv tools were skipped"


@pytest.mark.parametrize("machine", MACHINES)
def test_mise_config_renders_the_tools_table(machine: str):
    rendered = chezmoi_render(REPO_ROOT / "dot_config" / "mise" / "config.toml.tmpl", machine)
    doc = tomllib.loads(rendered)
    want = [ln.split(" = ", 1)[0].strip('"') for ln in lines(REAL, "mise-tools", machine)]
    assert sorted(doc["tools"]) == sorted(want)
    assert "refresh-completions --changed" in doc["hooks"]["postinstall"], (
        "postinstall must stay on the changed-only path — a bare refresh regenerates "
        "every recipe after any mise install session, in any directory"
    )


@pytest.mark.parametrize("machine", MACHINES)
def test_refresh_completions_task_renders_to_valid_bash(machine: str, tmp_path: Path):
    """The recipe heredoc is `read` line by line: one `<cmd> <recipe>` per line, no blank cmd."""
    script = tmp_path / "refresh-completions"
    rendered = chezmoi_render(MISE_TASKS / "executable_refresh-completions.tmpl", machine)
    script.write_text(rendered)
    check_shell(script)
    assert heredoc(rendered, "RECIPES") == lines(REAL, "completions", machine)
    assert heredoc(rendered, "TOOLMAP") == lines(REAL, "mise-completions", machine)


def run_refresh_task(
    machine: str, tmp_path: Path, args: list[str], env: Mapping[str, str]
) -> tuple[subprocess.CompletedProcess[str], Path]:
    """Render this machine's refresh-completions task and run it hermetically: a private
    cache dir and $HOME, and a PATH of system utilities only, so recipe commands are
    absent (skipped) unless the host system happens to ship one — assertions below
    stay agnostic to skip vs wrote."""
    script = tmp_path / "refresh-completions"
    script.write_text(chezmoi_render(MISE_TASKS / "executable_refresh-completions.tmpl", machine))
    proc = subprocess.run(
        ["bash", str(script), *args],
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
def test_refresh_changed_ignores_tools_without_recipes(machine: str, tmp_path: Path):
    """The postinstall hook's hot path: a session that installed only tools owning no
    recipe — any project-local `mise use` — writes nothing and exits 0 immediately."""
    proc, out = run_refresh_task(
        machine,
        tmp_path,
        ["--changed"],
        {"MISE_INSTALLED_TOOLS": '[{"name":"no-recipe-tool","version":"1.0.0"}]'},
    )
    assert (proc.returncode, proc.stdout, proc.stderr) == (0, "", "")
    assert not out.exists()


@pytest.mark.parametrize("machine", MACHINES)
def test_refresh_changed_overlays_only_the_installed_tools_commands(machine: str, tmp_path: Path):
    """`--changed` processes the installed tools' commands and nothing else; files other
    tools own stay in place (removal is the no-arg rebuild's job)."""
    toolmap = lines(REAL, "mise-completions", machine)
    if not toolmap:
        pytest.skip("no mise-backed completion recipes on this machine")
    tool, *cmds = toolmap[0].split()
    (tmp_path / "cache" / GENERATED).mkdir(parents=True)
    (tmp_path / "cache" / GENERATED / "_zzz").write_text("#compdef zzz\n")
    proc, out = run_refresh_task(
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
def test_refresh_full_run_rebuilds_from_scratch(machine: str, tmp_path: Path):
    """No args: a file whose tool left packages.toml (or never had a recipe) is gone
    afterwards — the only mode that removes."""
    (tmp_path / "cache" / GENERATED).mkdir(parents=True)
    (tmp_path / "cache" / GENERATED / "_zzz").write_text("#compdef zzz\n")
    proc, out = run_refresh_task(machine, tmp_path, [], {})
    assert proc.returncode == 0, proc.stderr
    assert not (out / "_zzz").exists()


def test_refresh_changed_outside_the_hook_fails_loudly(tmp_path: Path):
    """`--changed` without the hook's $MISE_INSTALLED_TOOLS is a misuse, not a full run."""
    proc, out = run_refresh_task(next(iter(MACHINES)), tmp_path, ["--changed"], {})
    assert proc.returncode != 0
    assert "postinstall" in proc.stderr
    assert not out.exists()


@pytest.mark.parametrize("machine", MACHINES)
def test_onchange_refresh_script_keys_on_this_machines_recipes(machine: str, tmp_path: Path):
    """chezmoi re-runs it when its rendered content changes, so the recipe list is rendered
    into it verbatim; the toolset gate defers to mise's postinstall hook on a new machine."""
    script = tmp_path / "refresh-completions.sh"
    rendered = chezmoi_render(REFRESH_SCRIPT, machine)
    script.write_text(rendered)
    check_shell(script)
    guards = ("command -v mise", "mise ls --missing", "export XDG_CACHE_HOME=")
    assert all(g in rendered for g in guards)
    assert all(rendered.index(g) < rendered.index("mise run refresh-completions") for g in guards)
    assert "mise run refresh-completions ||" in rendered, "a failed recipe must not fail the apply"
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
