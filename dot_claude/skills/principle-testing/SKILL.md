---
name: principle-testing
description: "Code & delivery. Apply when writing or reviewing tests, or deciding whether a behavior is worth testing."
---

# Testing

A test is a claim about what the code does for its users: a caller, an end user, or another service. Assert what they can observe through the interface they use (return values, errors they branch on, state they can read back, effects on systems outside the code such as a request sent, a row written, a message emitted), never what they can't: private helpers, internal call order, intermediate data, which collaborator did the work.

A behavior isn't tested until it has all three:

1. **Positive case** — valid input → expected output, asserted on specific values (not truthiness, not "no exception").
2. **Negative case** — invalid input → the specific error callers branch on (its type or code), not just "raises". The message wording is pinned only when a caller or user reads it.
3. **Boundaries** — empty / one / many / max / off-by-one.

Test names state the expected behavior. Mock only at system boundaries you don't own — a test that mocks the unit under test verifies nothing. Derive the expected value from the requirement, not from a run of the code.

## Two imagined edits

Judge every test, new or existing, against two edits:

- **Break the behavior.** Make the code return a wrong value, skip an effect, or do it twice. If the test still passes, it proves nothing. Usual causes: an assertion looser than the contract, an expected value computed by the code under test, a restated constant that never runs the code that reads it, an assertion on data the test built itself.
- **Change the internals.** Keep every observable result and restructure underneath. If the test now fails, it is pinned to the implementation: it needs a rewrite on every refactor and still catches no bug.

Keep a test that catches the first and survives the second; rewrite or delete the rest. The contract decides, not the assertion's shape: when "an empty cart never charges" is the promise, asserting the payment client got no call is behavior, paired with a case where the charge happens so the test shows it can see a call. The payload a mocked external service receives is behavior, because that service observes it. A test can pin an invariant instead of a call (a key that must exist in two tables, a compile-time type check); the invariant is the behavior.

## What earns a test

Add tests for realistic observable regressions, non-trivial invariants or boundaries, and concrete bugs. Code changing or coverage increasing is not sufficient justification by itself.

Prefer existing coverage at the behavior boundary. Avoid tests that mirror literals, mappings, obvious control flow, implementation details, or removed features, unless absence is itself a contract.

For concurrency, prefer deterministic coordination or controlled scheduling over sleeps when practical.
