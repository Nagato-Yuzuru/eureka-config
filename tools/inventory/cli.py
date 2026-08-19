"""`uv run render [--check] [--root DIR]`: compile .chezmoidata/{platforms,machines,packages}.yaml
into .rendered/<machine>/<artifact> for every declared machine, or with --check report
what would change (exit 1) — the pre-commit hook, so a data edit never lands without
its render. Bad data prints the message naming the entry and exits 2."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from inventory.inventory import Inventory, InventoryError
from inventory.render import RENDERED_DIR, render_tree, stale, write_tree

REPO_ROOT = Path(__file__).resolve().parents[2]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="render", description=__doc__)
    ap.add_argument("--check", action="store_true", help="report stale artifacts, write nothing")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="chezmoi source dir")
    args = ap.parse_args(argv)
    data_dir, out_dir = args.root / ".chezmoidata", args.root / RENDERED_DIR
    try:
        files = render_tree(Inventory.load(data_dir), data_dir)
    except InventoryError as e:
        print(e, file=sys.stderr)  # noqa: T201 — the CLI's one job is to report
        return 2
    changed = stale(files, out_dir)
    if args.check:
        for rel in changed:
            print(f"stale: {RENDERED_DIR}/{rel}", file=sys.stderr)  # noqa: T201
        if changed:
            print("run `uv run render` and commit the result", file=sys.stderr)  # noqa: T201
        return 1 if changed else 0
    write_tree(files, out_dir)
    for rel in changed:
        print(f"wrote {RENDERED_DIR}/{rel}")  # noqa: T201
    return 0


if __name__ == "__main__":
    sys.exit(main())
