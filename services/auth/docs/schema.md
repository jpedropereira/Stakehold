# auth schema

DDL lives at `src/auth/db/schema.sql` and is applied via `auth db init`. This document explains
the *why* behind each table's grain and loading strategy; keep it in lockstep with any schema
change (see [`data-schema.md`](../../../.claude/rules/data-schema.md)). For how the service around
these tables is structured, see [`architecture.md`](architecture.md).

Two tables: `users` (the only identity table in the application) and `sessions` (opaque,
hash-stored login tokens).

**Migrations**: `schema.sql` is applied by `auth db init` and must stay safe to rerun on any
database, including one mid-upgrade — every statement is `IF NOT EXISTS`-guarded, the same
discipline [`data-store`'s schema.md](../../data-store/docs/schema.md) documents (a single
unguarded, failing statement aborts the whole script, not just its own step).

## `users`

One row per registered user. Per
[`architecture.md`](architecture.md#responsibility), this is the *only* `users` table in the
application — every other service references a user by this table's `id`, never keeping a local
copy of `email`/`display_name`.

| Column          | Type            | Notes                                                        |
|-----------------|-----------------|----------------------------------------------------------------|
| `id`            | `UUID`          | PK. **No DB default** — generated in the app with `uuid.uuid7()` at signup, not `gen_random_uuid()` (Postgres 17 only offers v4 natively). v7 gives index locality and sortable creation order, which `architecture.md` chose specifically because `id` is a non-secret identifier where that trade-off has no downside — unlike a session token, where randomness is what matters. |
| `email`         | `VARCHAR(320)`  | Stored already-lowercased; normalization (`service._normalize_email`, strip + lower) happens at the app layer, not in the DB. `VARCHAR(320)` is the RFC 5321 maximum. Uniqueness is a plain unique index rather than a `citext` column — `citext` needs an extension present in every environment for something one `.lower()` at the write boundary already achieves. |
| `display_name`  | `VARCHAR(100)`  | Collected at signup. Exists so `web` has somewhere to read a greeting from without keeping its own copy of user data. |
| `password_hash` | `TEXT`          | An Argon2id PHC string (`passwords.hash_password`, `argon2-cffi`). `TEXT`, not a fixed width: the string's length moves with the cost parameters, and re-tuning those parameters later shouldn't force a migration. |
| `created_at`    | `TIMESTAMPTZ`   | Defaults to `now()`.                                            |

Indexes: `UNIQUE (email)` — the signup uniqueness check and the login lookup both key off it.

**Loading strategy**: single-row insert at signup (`POST /signup`). Never bulk-loaded, never
updated in bulk — a password change or display-name edit is a single-row `UPDATE` by `id`.

## `sessions`

One row per active (or not-yet-expired) login session. Backs the opaque session cookie
`architecture.md` specifies in place of a JWT.

| Column                | Type          | Notes                                                              |
|-----------------------|---------------|-----------------------------------------------------------------------|
| `id`                  | `UUID`        | PK, app-generated (`uuid.uuid7()`). An internal row identifier only — **never the cookie value**. |
| `token_hash`          | `BYTEA`       | `SHA-256` of the raw session token. The cookie carries 32 random bytes, base64url-encoded; only their hash is stored. This is a deliberate departure from `architecture.md`'s original sketch (`sessions(id UUID, ...)` where `id` *was* the cookie value): storing the raw token would mean anyone who can read this table — a DB dump, a misdirected query, a backup — can replay every live session. Hashing costs one extra column and one indexed equality lookup on login/validate; it buys the same property password hashing buys for credentials. |
| `user_id`             | `UUID`        | `REFERENCES users (id) ON DELETE CASCADE`. A real FK, unlike `portfolio.accounts.user_id` (a trusted plain column) — legitimate here because both tables live in `auth`'s own database, where a cross-database FK is impossible anyway. Deleting a user cleans up their sessions automatically. |
| `created_at`           | `TIMESTAMPTZ` | Defaults to `now()`.                                                  |
| `last_seen_at`         | `TIMESTAMPTZ` | Updated on every successful validation — the audit trail for sliding renewal. |
| `expires_at`           | `TIMESTAMPTZ` | The sliding expiry: pushed forward on each validation (`architecture.md`'s "on the order of 7–14 days"). A session is valid only while `now() < expires_at`. |
| `absolute_expires_at`  | `TIMESTAMPTZ` | Fixed at login, **never extended by sliding renewal**. Closes the "whether to add an absolute session lifetime cap" open question in `architecture.md`: without this column a continuously-used session could live forever. A session is valid only while `now() < expires_at AND now() < absolute_expires_at`. |

Indexes: `UNIQUE (token_hash)` for the validate/logout lookup by cookie value; `(user_id)` for
"list/revoke all of a user's sessions"; `(expires_at)` for the expired-session sweep
(`auth sessions purge-expired`).

**Loading strategy**: insert at login; `UPDATE ... SET expires_at = ..., last_seen_at = now()` on
each sliding renewal; `DELETE` on logout or expiry sweep. This is the whole reason
`architecture.md` chose opaque tokens over JWTs — revocation is a row delete, not a deny-list.
