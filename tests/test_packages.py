"""Executable spec for .chezmoidata/packages.yaml and its renderers in .chezmoitemplates/.

Table-driven. Each table row is one fact about the model:
- RENDER_CASES   synthetic packages -> exact lines each renderer emits on each machine
- REJECTS        malformed entries -> the validation message naming the entry
- INVARIANTS     facts the real packages.yaml must keep (per machine)
Plus golden renders of the real data per machine — the checked-in "what does each
machine get" view (regenerate: `UPDATE_GOLDEN=1 uv run pytest tests/test_packages.py`).
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES = REPO_ROOT / ".chezmoitemplates"
INSTALL_SCRIPT = REPO_ROOT / "run_onchange_before_install-packages.sh.tmpl"
GOLDEN = Path(__file__).parent / "golden"

MACHINES = {
    "darwin-arm64": ("darwin", "arm64"),
    "darwin-amd64": ("darwin", "amd64"),
    "linux-arm64": ("linux", "arm64"),
}
RENDERERS = ("brewfile", "mise-tools", "dnf-list", "dnf-repos")

# --- harness ------------------------------------------------------------------------------------


def chezmoi_template(
    expr: str, *, source: Path, machine: str, data: dict | None = None
) -> subprocess.CompletedProcess[str]:
    os_, arch = MACHINES[machine]
    override = {"chezmoi": {"os": os_, "arch": arch}, **(data or {})}
    return subprocess.run(
        [
            "chezmoi",
            "execute-template",
            "--source",
            str(source),
            "--override-data",
            json.dumps(override),
        ],
        input=expr,
        text=True,
        capture_output=True,
        check=False,
    )


def render_lines(
    template: str, *, source: Path, machine: str, data: dict | None = None
) -> list[str]:
    """Non-blank lines of a renderer's output; the constant `[tools]` header is dropped."""
    proc = chezmoi_template(
        f'{{{{ includeTemplate "{template}" . }}}}', source=source, machine=machine, data=data
    )
    assert proc.returncode == 0, proc.stderr
    return [ln for ln in proc.stdout.splitlines() if ln.strip() and ln != "[tools]"]


@pytest.fixture(scope="session")
def bare_source(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """A source dir holding only the templates, so `packages` comes solely from --override-data."""
    src = tmp_path_factory.mktemp("chezmoi-src")
    (src / ".chezmoitemplates").symlink_to(TEMPLATES)
    return src


# --- render semantics (synthetic data) ----------------------------------------------------------


@dataclass(frozen=True)
class RenderCase:
    id: str
    packages: dict
    expect: dict[tuple[str, str], list[str]] = field(
        default_factory=dict
    )  # (renderer, machine) -> lines


RENDER_CASES = [
    RenderCase(
        "default-backend-is-system",
        {"foo": {"on": ["darwin", "linux"]}},
        {
            ("brewfile", "darwin-arm64"): ['brew "foo"'],
            ("dnf-list", "linux-arm64"): ["foo"],
            ("mise-tools", "darwin-arm64"): [],
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
        {"rust": {"on": ["darwin"], "via": "mise", "version": "stable"}},
        {("mise-tools", "darwin-arm64"): ['"rust" = "stable"']},
    ),
    RenderCase(
        "name-defaults-to-key-and-per-backend-override-wins",
        {
            "fd": {"on": ["darwin", "linux"], "dnf": "fd-find"},
            "macism": {"on": ["darwin"], "brew": "laishulu/homebrew/macism"},
            "gopls": {"on": ["darwin"], "via": "mise", "mise": "go:golang.org/x/tools/gopls"},
        },
        {
            ("brewfile", "darwin-arm64"): ['brew "fd"', 'brew "laishulu/homebrew/macism"'],
            ("dnf-list", "linux-arm64"): ["fd-find"],
            ("mise-tools", "darwin-arm64"): ['"go:golang.org/x/tools/gopls" = "latest"'],
        },
    ),
    RenderCase(
        "cask-renders-cask-line-not-brew",
        {"ghostty": {"on": ["darwin"], "cask": "ghostty"}},
        {("brewfile", "darwin-arm64"): ['cask "ghostty"']},
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
        {
            ("brewfile", "darwin-amd64"): ['brew "foo"'],
            ("brewfile", "darwin-arm64"): [],
        },
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
                "overrides": [{"$when": "darwin/amd64", "mise": "npm:pnpm"}],
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
                "version": "1",
                "overrides": [
                    {"$when": "darwin", "version": "2"},
                    {"$when": "darwin/arm64", "version": "3"},
                ],
            }
        },
        {
            ("mise-tools", "darwin-arm64"): ['"foo" = "3"'],
            ("mise-tools", "darwin-amd64"): ['"foo" = "2"'],
        },
    ),
    RenderCase(
        "override-reaches-every-field",
        {
            "foo": {
                "on": ["darwin", "linux"],
                "via": "mise",
                "version": "1",
                "overrides": [
                    {"$when": "darwin/arm64", "version": "2"},
                    {"$when": "linux", "via": "system", "dnf": "foo-bin", "copr": "a/b"},
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
            "starship": {"on": ["darwin", "linux"], "copr": "atim/starship"},
            "mise": {"on": ["linux"], "repofile": "https://mise.jdx.dev/rpm/mise.repo"},
            "mac-only": {"on": ["darwin"], "copr": "nope/nope"},
            "via-mise": {"on": ["linux"], "via": "mise", "copr": "nope/nope"},
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
        {
            ("brewfile", "darwin-arm64"): [],
            ("mise-tools", "darwin-arm64"): [],
            ("dnf-list", "linux-arm64"): [],
            ("dnf-repos", "linux-arm64"): [],
        },
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
                "overrides": [{"$when": "linux/amd64", "version": "9"}],
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
                "overrides": [{"$when": "darwin", "version": "2"}],
            }
        },
        {
            ("mise-tools", "darwin-arm64"): ['"foo" = "2"'],
            ("mise-tools", "darwin-amd64"): [],
        },
    ),
    RenderCase(
        "shared-copr-is-emitted-once-per-package",  # the install script's guard makes repeats no-ops
        {"a": {"on": ["linux"], "copr": "x/y"}, "b": {"on": ["linux"], "copr": "x/y"}},
        {("dnf-repos", "linux-arm64"): ["copr x/y", "copr x/y"]},
    ),
]


@pytest.mark.parametrize(
    ("packages", "renderer", "machine", "want"),
    [
        pytest.param(c.packages, renderer, machine, want, id=f"{c.id}:{renderer}@{machine}")
        for c in RENDER_CASES
        for (renderer, machine), want in c.expect.items()
    ],
)
def test_render(bare_source: Path, packages: dict, renderer: str, machine: str, want: list[str]):
    assert (
        render_lines(renderer, source=bare_source, machine=machine, data={"packages": packages})
        == want
    )


# --- validation ---------------------------------------------------------------------------------

REJECTS = [
    ("unknown-key", {"foo": {"on": ["darwin"], "nope": "1"}}, 'packages.foo: unknown key "nope"'),
    ("missing-on", {"foo": {"via": "mise"}}, "packages.foo: missing 'on'"),
    ("on-not-a-list", {"foo": {"on": "darwin"}}, "packages.foo: 'on' must be a non-empty list"),
    ("on-empty", {"foo": {"on": []}}, "packages.foo: 'on' must be a non-empty list"),
    ("on-unknown-tag", {"foo": {"on": ["mars"]}}, 'packages.foo.on: unknown tag "mars"'),
    (
        "via-invalid",
        {"foo": {"on": ["darwin"], "via": "pip"}},
        "packages.foo: via must be system|mise",
    ),
    (
        "name-field-is-a-map",
        {"foo": {"on": ["darwin"], "mise": {"darwin": "x"}}},
        "packages.foo.mise: must be a string",
    ),
    (
        "overrides-not-a-list",
        {"foo": {"on": ["darwin"], "overrides": {"$when": "linux"}}},
        "packages.foo: 'overrides' must be a list",
    ),
    (
        "override-missing-when",
        {"foo": {"on": ["darwin"], "overrides": [{"via": "mise"}]}},
        "each entry needs $when",
    ),
    (
        "override-unknown-tag",
        {"foo": {"on": ["darwin"], "overrides": [{"$when": "mars"}]}},
        "each entry needs $when",
    ),
    (
        "override-of-on",
        {"foo": {"on": ["darwin"], "overrides": [{"$when": "linux", "on": ["linux"]}]}},
        'packages.foo.overrides[linux]: cannot override "on"',
    ),
    (
        "override-via-invalid",
        {"foo": {"on": ["darwin"], "overrides": [{"$when": "linux", "via": "pip"}]}},
        "packages.foo.overrides[linux]: via must be system|mise",
    ),
    (
        "override-value-not-string",
        {"foo": {"on": ["darwin"], "overrides": [{"$when": "linux", "version": 1}]}},
        "packages.foo.overrides[linux].version: must be a string",
    ),
]


def validation_error(source: Path, packages: dict) -> str | None:
    proc = chezmoi_template(
        '{{ includeTemplate "packages-validate" . }}',
        source=source,
        machine="darwin-arm64",
        data={"packages": packages},
    )
    return None if proc.returncode == 0 else proc.stderr


@pytest.mark.parametrize(("packages", "message"), [pytest.param(p, m, id=i) for i, p, m in REJECTS])
def test_validation_rejects(bare_source: Path, packages: dict, message: str):
    err = validation_error(bare_source, packages)
    assert err is not None and message in err, err


# --- real data ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class Invariant:
    id: str
    renderer: str
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
    Invariant("uv-not-in-mise-on-mac", "mise-tools", "darwin-arm64", excludes=['"uv" = "latest"']),
    Invariant("uv-not-in-mise-on-linux", "mise-tools", "linux-arm64", excludes=['"uv" = "latest"']),
    Invariant("uv-from-brew", "brewfile", "darwin-arm64", contains=['brew "uv"']),
    Invariant("uv-from-dnf", "dnf-list", "linux-arm64", contains=["uv"]),
    Invariant("mac-installs-nothing-via-dnf", "dnf-list", "darwin-arm64", exact=[]),
    Invariant("linux-has-no-brewfile", "brewfile", "linux-arm64", exact=[]),
]


@pytest.mark.parametrize("inv", [pytest.param(i, id=i.id) for i in INVARIANTS])
def test_real_data_invariant(inv: Invariant):
    lines = render_lines(inv.renderer, source=REPO_ROOT, machine=inv.machine)
    assert set(inv.contains) <= set(lines), inv.contains
    assert not set(inv.excludes) & set(lines), inv.excludes
    if inv.exact is not None:
        assert lines == inv.exact


@pytest.mark.parametrize("machine", MACHINES)
@pytest.mark.parametrize(
    "renderer", [r for r in RENDERERS if r != "dnf-repos"]
)  # shared repos repeat by design
def test_real_data_has_no_duplicate_lines(renderer: str, machine: str):
    lines = render_lines(renderer, source=REPO_ROOT, machine=machine)
    assert len(lines) == len(set(lines)), sorted(ln for ln in lines if lines.count(ln) > 1)


@pytest.mark.parametrize("machine", MACHINES)
def test_install_script_renders_to_valid_shell(machine: str, tmp_path: Path):
    os_, arch = MACHINES[machine]
    proc = subprocess.run(
        [
            "chezmoi", "execute-template", "--source", str(REPO_ROOT),
            "--override-data", json.dumps({"chezmoi": {"os": os_, "arch": arch}}),
            "--file", str(INSTALL_SCRIPT),
        ],
        text=True,
        capture_output=True,
        check=False,
    )  # fmt: skip
    assert proc.returncode == 0, proc.stderr
    script = tmp_path / "install-packages.sh"
    script.write_text(proc.stdout)
    if os_ == "darwin":
        assert "brew bundle install --file=-" in proc.stdout
        assert "dnf" not in proc.stdout
    else:
        assert "xargs -r sudo dnf -y install" in proc.stdout
        assert "brew" not in proc.stdout
    subprocess.run(["bash", "-n", str(script)], check=True)
    if shutil.which("shellcheck"):
        subprocess.run(["shellcheck", str(script)], check=True)


@pytest.mark.parametrize("machine", MACHINES)
@pytest.mark.parametrize("renderer", RENDERERS)
def test_golden_render(renderer: str, machine: str):
    """Regenerate after an intentional packages.yaml change: UPDATE_GOLDEN=1 uv run pytest tests/test_packages.py"""
    got = "".join(f"{ln}\n" for ln in render_lines(renderer, source=REPO_ROOT, machine=machine))
    path = GOLDEN / machine / f"{renderer}.txt"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(got)
    assert path.exists(), f"missing golden {path}; run with UPDATE_GOLDEN=1"
    assert got == path.read_text()
