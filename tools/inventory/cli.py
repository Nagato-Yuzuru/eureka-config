"""`uv run render [all|schema] [--root DIR]`: compile .chezmoidata/{platforms,
machines,packages}.toml into .rendered/<machine>/<artifact> for every declared machine plus
the JSON Schemas under .schema/ (`all`, the default), or only the schemas
(`schema` — from the types alone, so it works while a data file is still broken). Bad data
prints the message naming the entry and exits 2."""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Sequence
from pathlib import Path

from inventory.inventory import Inventory, InventoryError
from inventory.render import (
    RENDERED_DIR,
    SCHEMA_DIR,
    render_tree,
    schemas,
    write_tree,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
log = logging.getLogger("inventory")


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="render", description=__doc__)
    ap.add_argument("what", nargs="?", choices=("all", "schema"), default="all")
    ap.add_argument("--root", type=Path, default=REPO_ROOT, help="chezmoi source dir")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s", stream=sys.stderr)
    data_dir = args.root / ".chezmoidata"
    # `schema` comes from the types alone, so it works while a data file is broken;
    # `all` writes neither tree when the data does not parse.
    trees = [(schemas(), args.root / SCHEMA_DIR, SCHEMA_DIR)]
    if args.what != "schema":
        try:
            inv = Inventory.load(data_dir)
        except InventoryError as e:
            log.error("%s", e)
            return 2
        trees.append((render_tree(inv, data_dir), args.root / RENDERED_DIR, RENDERED_DIR))
    for files, out_dir, shown in trees:
        for rel in write_tree(files, out_dir):
            log.info("%s %s/%s", "wrote" if rel in files else "removed", shown, rel)
    return 0


if __name__ == "__main__":
    sys.exit(main())
