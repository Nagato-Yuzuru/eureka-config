"""`uv run render [--check] [--root DIR]`: compile .chezmoidata/{platforms,machines,packages}.toml
into .rendered/<machine>/<artifact> for every declared machine, or with --check report
what would change (exit 1) — the pre-commit hook, so a data edit never lands without
its render. Bad data prints the message naming the entry and exits 2."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from inventory.inventory import Inventory, InventoryError
from inventory.render import RENDERED_DIR, render_tree, stale, write_tree

REPO_ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("inventory")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="render", description=__doc__)
    ap.add_argument("--check", action="store_true", help="report stale artifacts, write nothing")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="chezmoi source dir")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    data_dir, out_dir = args.root / ".chezmoidata", args.root / RENDERED_DIR
    try:
        files = render_tree(Inventory.load(data_dir), data_dir)
    except InventoryError as e:
        log.error("%s", e)
        return 2
    changed = stale(files, out_dir)
    if args.check:
        for rel in changed:
            log.error("stale: %s/%s", RENDERED_DIR, rel)
        if changed:
            log.error("run `uv run render` and commit the result")
        return 1 if changed else 0
    write_tree(files, out_dir)
    for rel in changed:
        log.info("wrote %s/%s", RENDERED_DIR, rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
