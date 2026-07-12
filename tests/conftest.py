"""Load dot_claude/modify_settings.json (a Python file despite the extension) as a module."""

from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
ENGINE_PATH = REPO_ROOT / "dot_claude" / "modify_settings.json"
SNAPSHOT_PATH = REPO_ROOT / "run_onchange_after_snapshot-claude-managed.py.tmpl"


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
