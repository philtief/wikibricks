CREATE TABLE IF NOT EXISTS pages (
    page_id TEXT PRIMARY KEY,
    path TEXT NOT NULL UNIQUE,
    current_version_id TEXT,
    status TEXT NOT NULL DEFAULT 'active',
    superseded_by_page_id TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS page_versions (
    version_id TEXT PRIMARY KEY,
    page_id TEXT NOT NULL REFERENCES pages(page_id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    title TEXT NOT NULL,
    page_type TEXT NOT NULL,
    content TEXT NOT NULL,
    content_text TEXT NOT NULL,
    tags TEXT NOT NULL,
    source_ids TEXT,
    parent_id TEXT,
    chunk_index INTEGER,
    created_by TEXT NOT NULL,
    content_hash TEXT NOT NULL,
    curation_patch_id TEXT,
    created_at TEXT NOT NULL,
    UNIQUE(page_id, version)
);

CREATE TABLE IF NOT EXISTS sync_outbox (
    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    entity_kind TEXT NOT NULL,
    entity_id TEXT NOT NULL,
    version_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    batch_id TEXT,
    created_at TEXT NOT NULL,
    acknowledged_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id TEXT PRIMARY KEY,
    harness TEXT NOT NULL,
    external_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    agent TEXT,
    workspace TEXT,
    started_at TEXT,
    source_updated_at TEXT,
    page_path TEXT NOT NULL UNIQUE,
    title TEXT NOT NULL,
    metadata TEXT NOT NULL,
    current_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(harness, external_id)
);

CREATE TABLE IF NOT EXISTS session_events (
    event_id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL REFERENCES sessions(session_id) ON DELETE CASCADE,
    external_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    current_version_id TEXT,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(session_id, external_id)
);

CREATE TABLE IF NOT EXISTS session_event_versions (
    version_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL REFERENCES session_events(event_id) ON DELETE CASCADE,
    version INTEGER NOT NULL,
    kind TEXT NOT NULL,
    content TEXT NOT NULL,
    metadata TEXT NOT NULL,
    source_created_at TEXT,
    content_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(event_id, version)
);

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

CREATE TABLE IF NOT EXISTS page_search_chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id TEXT NOT NULL REFERENCES page_versions(version_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    UNIQUE(version_id, chunk_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS page_search_fts USING fts5(
    version_id UNINDEXED,
    chunk_text,
    tokenize='unicode61'
);

CREATE TABLE IF NOT EXISTS session_search_chunks (
    chunk_id INTEGER PRIMARY KEY AUTOINCREMENT,
    version_id TEXT NOT NULL REFERENCES session_event_versions(version_id) ON DELETE CASCADE,
    chunk_index INTEGER NOT NULL,
    start_offset INTEGER NOT NULL,
    end_offset INTEGER NOT NULL,
    chunk_text TEXT NOT NULL,
    UNIQUE(version_id, chunk_index)
);

CREATE VIRTUAL TABLE IF NOT EXISTS session_search_fts USING fts5(
    version_id UNINDEXED,
    chunk_text,
    tokenize='unicode61'
);

CREATE INDEX IF NOT EXISTS pages_active_path_idx
ON pages(path) WHERE status = 'active';

CREATE INDEX IF NOT EXISTS page_versions_page_idx
ON page_versions(page_id, version DESC);

CREATE INDEX IF NOT EXISTS sync_outbox_pending_idx
ON sync_outbox(sequence) WHERE acknowledged_at IS NULL;

CREATE INDEX IF NOT EXISTS session_events_order_idx
ON session_events(session_id, active, position);
