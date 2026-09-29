"""Load the claude-managed settings engine (tools/claude_managed.py) as a module, and run it
the way the chezmoi wrappers do."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_PATH = REPO_ROOT / "tools" / "claude_managed.py"
FILTER_WRAPPER = REPO_ROOT / "dot_claude" / "modify_settings.json.tmpl"
SNAPSHOT_WRAPPER = REPO_ROOT / "run_onchange_after_snapshot-claude-managed.py.tmpl"
FRAGMENT_DIR = REPO_ROOT / "claude-managed.d"


def load_module(name: str, path: Path):
    # Never write __pycache__ into the chezmoi source tree: bytecode under
    # dot_claude/ would be picked up as a source-state entry on the next apply.
    sys.dont_write_bytecode = True
    loader = importlib.machinery.SourceFileLoader(name, str(path))
    spec = importlib.util.spec_from_loader(name, loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    # Must be registered before exec: dataclasses resolves the module through
    # sys.modules when annotations are strings (from __future__ import annotations).
    sys.modules[name] = module
    loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def engine():
    return load_module("modify_settings", ENGINE_PATH)


def rendered(fragments: dict[str, Any]) -> str:
    """Fragments as a wrapper embeds them: ``[[name, text], ...]``. A ``str`` value is the
    text verbatim (for deliberately invalid content); anything else is JSON-serialized."""
    return json.dumps(
        [[n, f if isinstance(f, str) else json.dumps(f)] for n, f in fragments.items()]
    )


def engine_argv(entry: str, fragments: dict[str, Any], *args: str) -> list[str]:
    """argv calling engine ``entry`` (``main`` or ``snapshot``) on ``fragments`` in a fresh
    interpreter, as the wrappers do after chezmoi renders the fragments into them."""
    code = (
        "import sys; sys.dont_write_bytecode = True; sys.path.insert(0, sys.argv[1]); "
        f"import claude_managed; sys.exit(claude_managed.{entry}(*sys.argv[2:]))"
    )
    return [sys.executable, "-c", code, str(ENGINE_PATH.parent), rendered(fragments), *args]
