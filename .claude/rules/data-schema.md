# Data Schema Ownership

## Schema Changes

- Data schemas are owned by the user, not by Claude.
- Never create, alter, or drop a table (or equivalent schema object) in any service —
  data-store, portfolio, or any future service — without first agreeing the schema with the user.
- Propose the schema (fields, types, relationships, constraints) and get explicit sign-off before
  writing migration or model code.

## Documentation

- Every schema must be documented at `services/<service>/docs/schema.md`.
- When a schema changes, update that file as part of the same change — not as a follow-up.
