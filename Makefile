# Run by chezmoi's read-source-state hook, so editing .chezmoidata/ or the compiler needs
# no separate render step. `render` rewrites STAMP only when its content changes, so the
# touch is what advances the mtime make compares. The directory itself is a prerequisite
# for the gitignored packages.local.toml: deleting it leaves no newer file behind, only a
# newer directory.
.rendered/STAMP: .chezmoidata $(wildcard .chezmoidata/*.toml) $(wildcard tools/inventory/*.py) uv.lock
	uv run render
	@touch $@

# The repo's checks, one entry point for a person and for CI alike. Commits run only the
# prek hooks; the suite runs here on demand and in CI (.github/workflows/ci.yml).
#   make check                       lint, then test: everything
#   make test PYTEST_ARGS='-k gate'  pytest across all cores, after bringing .rendered/ up to date
#   make lint                        every prek hook, over all tracked files
.PHONY: check test lint
check: lint test

test: .rendered/STAMP
	uv run pytest -q -n auto $(PYTEST_ARGS)

lint:
	prek run --all-files
