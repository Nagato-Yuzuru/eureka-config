# Run by chezmoi's read-source-state hook, so editing .chezmoidata/ or the compiler needs
# no separate render step. `render` rewrites STAMP only when its content changes, so the
# touch is what advances the mtime make compares.
.rendered/STAMP: $(wildcard .chezmoidata/*.toml) $(wildcard tools/inventory/*.py) uv.lock
	uv run render
	@touch $@
