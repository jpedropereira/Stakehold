# Testing Principles

## Black Box Testing

- Test behavior, not implementation.
- Tests should not depend on implementation details.
- Preserve intended redundancy in tests.
- Hard-coded values in tests may be deliberate.

## Deterministic Tests

- No dependency on randomness.
- Use fixed seeds if random needed.
- Mock time-dependent behavior.
- Isolate external dependencies.

## Workflow

- Write the unit tests before writing the implementation. Confirm they fail for the expected
  reason, then implement until they pass.
- Do not write implementation code before its tests exist unless the user explicitly says
  otherwise.

## Coverage

- Success cases, failure cases, edge cases.
- Add tests to prevent recurrence of fixed bugs.
- Verify tests actually test intended behavior.

## Avoid

- Overfitting on internal structure.
- Coupling tests to implementation details.
- Removing "redundant" tests that serve different purposes.
