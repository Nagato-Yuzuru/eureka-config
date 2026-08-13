---
name: principle-migrate-callers-then-delete-legacy-apis
description: "Apply when changing an internal contract or introducing a new API while old callers exist. Enumerate every call site, migrate callers and delete the old API in the same wave, and converge on the end state instead of building throwaway compatibility code."
disable-model-invocation: true
---

# Migrate callers, then delete legacy APIs

A contract change isn't done until every caller is updated, and a migration isn't done while the old path still exists.

**Enumerate before you edit.** Find call sites with LSP `findReferences` first (resolves imports and aliases), then `ast-grep 'foo($$$)'` when no language server covers the language, then ripgrep plus manual filtering as the last resort. Never patch just the call sites you remember.

**Migrate and delete in the same wave.**
- Do not keep legacy API paths alive only because internal callers still exist.
- No backwards-compat aliases or re-export shims unless asked.
- Treat temporary adapters as exceptional and time-boxed, not default architecture.
- Update tests to assert the new contract, and delete tests that only protect pre-refactor implementation details.

**Converge on the end state.** Prioritize end-state integrity over transitional stability. Don't build throwaway compatibility code just to keep every intermediate state smooth: intermediate breakage is acceptable when it is planned, scoped, and reversible — declare where. Keep high-signal checks for actively touched areas while migrating, and require full static and runtime verification at completion (per-unit checks along the way per **principle-sequence-verifiable-units**).

**When this applies:** no external users depend on backward compatibility, and the project can absorb coordinated breaking changes. Keeping both old and new APIs creates dual-path complexity, slows cleanup, and makes the codebase feel append-only.
