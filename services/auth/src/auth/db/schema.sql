CREATE TABLE IF NOT EXISTS users (
    id            UUID         PRIMARY KEY,
    email         VARCHAR(320) NOT NULL,
    display_name  VARCHAR(100) NOT NULL,
    password_hash TEXT         NOT NULL,
    created_at    TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email ON users (email);

-- Added after the table shipped, so a column on CREATE TABLE above would never reach an existing
-- database. Granted only via `auth user set-admin` -- see docs/schema.md.
ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN NOT NULL DEFAULT false;

CREATE TABLE IF NOT EXISTS sessions (
    id                  UUID        PRIMARY KEY,
    token_hash          BYTEA       NOT NULL,
    user_id             UUID        NOT NULL REFERENCES users (id) ON DELETE CASCADE,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at          TIMESTAMPTZ NOT NULL,
    absolute_expires_at TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_sessions_token_hash ON sessions (token_hash);
CREATE INDEX IF NOT EXISTS idx_sessions_user_id           ON sessions (user_id);
CREATE INDEX IF NOT EXISTS idx_sessions_expires_at         ON sessions (expires_at);
