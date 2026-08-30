---
name: principle-testing
description: "Code & delivery. Apply when writing or reviewing tests, or deciding whether a behavior is worth testing."
---

# Testing

A behavior isn't tested until it has all three:

1. **Positive case** — valid input → expected output, asserted on specific values (not truthiness, not "no exception").
2. **Negative case** — invalid input → the specific error type/message, not just "raises".
3. **Boundaries** — empty / one / many / max / off-by-one.

Test names state the expected behavior. Mock only at system boundaries you don't own — a test that mocks the unit under test verifies nothing. Every test must be able to fail: asserting existence, signatures, or constants is not a test.

## What earns a test

The three parts above say what a test must contain, not which behavior deserves one.

Add tests for realistic observable regressions, non-trivial invariants or boundaries, and concrete bugs. Code changing or coverage increasing is not sufficient justification by itself.

Prefer existing coverage at the behavior boundary. Avoid tests that mirror literals, mappings, obvious control flow, implementation details, or removed features, unless absence is itself a contract.

For concurrency, prefer deterministic coordination or controlled scheduling over sleeps when practical.
