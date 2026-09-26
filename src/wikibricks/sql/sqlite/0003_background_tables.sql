-- Forward migration: add tables that were retroactively added to 0001_core.sql
-- in commit 633d277. Databases created before that commit already have 0001
-- recorded in schema_migrations and were never given these tables.
-- IF NOT EXISTS makes this safe to run against fresh databases too.

CREATE TABLE IF NOT EXISTS background_leases (
    name TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    expires_at REAL NOT NULL,
    renewed_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sync_state (
    target TEXT PRIMARY KEY,
    cursor TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
