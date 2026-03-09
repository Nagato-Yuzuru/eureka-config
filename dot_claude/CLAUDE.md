# Global Preferences

## JavaScript / TypeScript — Package Manager

- Always use `pnpm` instead of `npm` for all package operations.
- Never run `npm install`, `npm ci`, `npm run`, or `npx`.

| Intent               | Command                                  |
| -------------------- | ---------------------------------------- |
| Install dependencies | `pnpm install`                           |
| Add a package        | `pnpm add <pkg>`                         |
| Run a script         | `pnpm run <script>` or `pnpm <script>`   |
| One-off execution    | `pnpm dlx <pkg>` or `deno run npm:<pkg>` |

Prefer deno run npm:<pkg> over pnpm dlx when the tool is non-interactive and has minimal system access (e.g. formatters, type checkers, code generators with no file I/O). For interactive CLI tools or tools that require filesystem/network access, use pnpm dlx.

## Python — Toolchain

- **Never** invoke the system Python interpreter or modify system-level packages:
  - Forbidden: `python`, `python3`, `pip`, `pip3`, `python -m pip`, `sudo pip`, `virtualenv`, `poetry`.
  - This applies even when a venv is active — always go through `uv`.
- Always use `uv` for environment and dependency management.

| Intent                         | Command              |
| ------------------------------ | -------------------- |
| Create / sync environment      | `uv sync`            |
| Add a package                  | `uv add <pkg>`       |
| Add a dev dependency           | `uv add --dev <pkg>` |
| Run a script                   | `uv run <script>`    |
| One-off tool execution         | `uvx <tool>`         |
| Run Python directly            | `uv run python`      |
| Run a PEP 723 inline-meta file | `uv run <file.py>`   |

### PEP 723 — Inline Script Metadata

For single-file scripts with dependencies, prefer PEP 723 inline metadata over creating a full project. `uv` resolves and installs dependencies automatically on first run.

```python
# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx", "rich"]
# ///

import httpx
...
```

Run with: `uv run file.py` — no `uv sync`, no `pyproject.toml` required.

Use a full `uv` project (`uv sync`) when the script grows into a multi-file module, requires editable installs, or is intended as a reusable library.

- Always use `ty` for static type checking instead of `mypy` or `pyright`.

| Intent                     | Command           |
| -------------------------- | ----------------- |
| Type-check current project | `ty check`        |
| Type-check a specific file | `ty check <path>` |
