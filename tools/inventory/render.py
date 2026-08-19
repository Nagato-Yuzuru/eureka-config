"""The per-machine artifacts, as text, from a machine's `Resolved` list — and the
`.rendered/` tree that holds them for chezmoi to include (`uv run render`).

Every artifact is one line per package in key order, in the exact syntax the
consumer reads: Brewfile lines, dnf names and repos, mise `[tools]` entries,
`uv tool install` specs, `<command> <recipe>` for refresh-completions. STAMP is
the hash of the data files the tree was compiled from; the `artifact` template
recomputes it and refuses to serve a stale tree.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

from inventory.inventory import DATA_FILES, Inventory
from inventory.model import Cask, CoprRpm, Mise, RepofileRpm, Resolved, Uv

RENDERED_DIR = ".rendered"
STAMP = "STAMP"


def brewfile(rs: Iterable[Resolved]) -> list[str]:
    return [
        f'cask "{r.name}"' if isinstance(r.spec, Cask) else f'brew "{r.name}"'
        for r in rs
        if r.backend == "brew"
    ]


def dnf_list(rs: Iterable[Resolved]) -> list[str]:
    return [r.name for r in rs if r.backend == "dnf"]


def dnf_repos(rs: Iterable[Resolved]) -> list[str]:
    """`copr owner/project` / `repofile <url>`, one per line; a shared repo repeats per
    package (the install script's commands are idempotent)."""
    out = []
    for r in rs:
        match r.spec:
            case CoprRpm(copr=copr):
                out.append(f"copr {copr}")
            case RepofileRpm(repofile=url):
                out.append(f"repofile {url}")
    return out


def mise_tools(rs: Iterable[Resolved]) -> list[str]:
    """Entries of mise's `[tools]` table. Arrays are spelled the way mise (toml_edit)
    writes them back, so its rewrites do not drift from the render."""
    out = []
    for r in rs:
        if r.backend == "mise" and isinstance(r.spec, Mise):
            name, version = json.dumps(r.name), json.dumps(r.spec.version)
            if not r.spec.components:
                out.append(f"{name} = {version}")
            else:
                comps = ", ".join(json.dumps(c) for c in r.spec.components)
                out.append(f"{name} = {{ version = {version}, components = [{comps}] }}")
    return out


def uv_tools(rs: Iterable[Resolved]) -> list[str]:
    """`uv tool install` arguments: the PyPI name, `@<version>` when one is declared."""
    return [
        f"{r.name}@{r.spec.version}" if r.spec.version else r.name
        for r in rs
        if r.backend == "uv" and isinstance(r.spec, Uv)
    ]


def completions(rs: Iterable[Resolved]) -> list[str]:
    """`<command> <recipe>` for every package installing here that declares one."""
    return [f"{cmd} {recipe}" for r in rs for cmd, recipe in sorted(r.completions.items())]


ARTIFACTS: dict[str, Callable[[Iterable[Resolved]], list[str]]] = {
    "brewfile": brewfile,
    "dnf-list": dnf_list,
    "dnf-repos": dnf_repos,
    "mise-tools": mise_tools,
    "uv-tools": uv_tools,
    "completions": completions,
}


def render(name: str, rs: Iterable[Resolved]) -> str:
    return "".join(f"{line}\n" for line in ARTIFACTS[name](rs))


def render_machine(inv: Inventory, machine_id: str) -> dict[str, str]:
    rs = inv.resolved(machine_id)
    return {name: render(name, rs) for name in ARTIFACTS}


def stamp(data_dir: Path) -> str:
    """sha256 of the data files, concatenated in name order — the same bytes the
    `artifact` template hashes."""
    h = hashlib.sha256()
    for f in DATA_FILES:
        h.update((data_dir / f).read_bytes())
    return h.hexdigest()


def render_tree(inv: Inventory, data_dir: Path) -> dict[str, str]:
    """Relative path -> content for the whole `.rendered/` tree."""
    files = {
        f"{mid}/{name}": text
        for mid in inv.machines
        for name, text in render_machine(inv, mid).items()
    }
    files[STAMP] = stamp(data_dir) + "\n"
    return files


def write_tree(files: Mapping[str, str], out_dir: Path) -> None:
    """Rebuilt from scratch: a machine that leaves machines.toml leaves no directory behind."""
    if out_dir.exists():
        shutil.rmtree(out_dir)
    for rel, text in files.items():
        path = out_dir / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)


def stale(files: Mapping[str, str], out_dir: Path) -> list[str]:
    """Paths whose on-disk content differs from `files` — missing, changed, or extra."""
    on_disk = (
        {str(p.relative_to(out_dir)): p.read_text() for p in out_dir.rglob("*") if p.is_file()}
        if out_dir.exists()
        else {}
    )
    return sorted(
        rel for rel in files.keys() | on_disk.keys() if files.get(rel) != on_disk.get(rel)
    )
