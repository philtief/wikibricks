-- Forward migration: backfill tables that were retroactively added to
-- 0001_core.sql after the live database had already applied that migration.
--
-- b7ea656 (2026-09-01 15:28 +02:00) added links, sources, and operations.
-- 633d277 (2026-09-01 15:36 +02:00) added background_leases and sync_state.
--
-- A database created between 5893121 (15:21) and b7ea656 (15:28) is missing
-- all five tables. IF NOT EXISTS makes this safe to run against fresh databases.

CREATE TABLE IF NOT EXISTS links (
    link_id TEXT PRIMARY KEY,
    source_page_id TEXT NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    target_page_id TEXT NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    link_type TEXT NOT NULL,
    origin TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(source_page_id, target_page_id, link_type)
);

CREATE TABLE IF NOT EXISTS sources (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    uri TEXT NOT NULL,
    metadata TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS operations (
    operation_id TEXT PRIMARY KEY,
    op_type TEXT NOT NULL,
    path TEXT,
    query TEXT,
    details TEXT,
    created_at TEXT NOT NULL
);

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
