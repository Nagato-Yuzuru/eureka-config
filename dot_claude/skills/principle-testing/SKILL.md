---
name: principle-testing
description: "Apply when writing or reviewing tests. A behavior isn't tested until it has a positive case asserted on specific values, a negative case asserting the specific error, and boundaries (empty/one/many/max/off-by-one). Mock only at boundaries you don't own."
disable-model-invocation: true
---

# Testing

A behavior isn't tested until it has all three:

1. **Positive case** — valid input → expected output, asserted on specific values (not truthiness, not "no exception").
2. **Negative case** — invalid input → the specific error type/message, not just "raises".
3. **Boundaries** — empty / one / many / max / off-by-one.

Test names state the expected behavior. Mock only at system boundaries you don't own — a test that mocks the unit under test verifies nothing. Every test must be able to fail: asserting existence, signatures, or constants is not a test.
